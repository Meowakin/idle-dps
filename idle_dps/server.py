"""HTTP server + tick loop. Stdlib only apart from OpenTelemetry."""
from __future__ import annotations

import json
import logging
import os
import threading
import webbrowser
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from opentelemetry import context, metrics, trace
from opentelemetry.trace import Status, StatusCode

from . import telemetry
from .sim import Sim

log = logging.getLogger("idle_dps")
tracer = trace.get_tracer("idle_dps")
meter = metrics.get_meter("idle_dps")
STATIC = Path(__file__).parent / "static"

sim = Sim()
lock = threading.Lock()

# --- metrics: counters/histograms for events, observable gauges for state ---
m_damage = meter.create_counter("sim.damage.total", unit="{damage}", description="Damage dealt")
m_wasted = meter.create_counter("sim.ore.overflow", unit="{ore}", description="Ore offered to mint beyond its capacity")
m_upgrades = meter.create_counter("sim.upgrade.purchased", description="Upgrades/units bought")
m_levers = meter.create_counter("sim.lever.changed", description="Lever changes")
m_slo = meter.create_counter("sim.slo.ticks", description="Ticks by SLO outcome")
h_build = meter.create_histogram("sim.build.duration", unit="s", description="Sim seconds from enqueue to completion")
h_build_stall = meter.create_histogram("sim.build.stall", unit="s", description="Sim seconds a build spent starved")
h_tick = meter.create_histogram("sim.tick.duration", unit="s", description="Wall time per tick")
h_http = meter.create_histogram("http.server.request.duration", unit="s")


def _gauge(name, fn, unit="1"):
    def cb(options):
        with lock:
            yield metrics.Observation(fn())
    meter.create_observable_gauge(name, callbacks=[cb], unit=unit)


_gauge("sim.damage.dps", lambda: sim.dps(), "{damage}/s")
_gauge("sim.ore.rate", lambda: sim.last.get("ore_rate", 0), "{ore}/s")
_gauge("sim.coin.rate", lambda: sim.last.get("coin_rate", 0), "{coin}/s")
_gauge("sim.coin.balance", lambda: sim.coin, "{coin}")
_gauge("sim.ammo.balance", lambda: sim.ammo, "{ore}")
_gauge("sim.build.queue_depth", lambda: len(sim.queue), "{build}")
_gauge("sim.slo.dps_target", lambda: sim.slo_target(), "{damage}/s")


_last_slo = None
build_spans = {}  # build id -> open span; one long-lived span per build (enqueue -> completion)


def start_build_span(b, request_span):
    """Detached root span (own trace) linked to the enqueuing request, so each build is a waterfall bar."""
    span = tracer.start_span("sim.build.unit", context=context.Context(),
                             links=[trace.Link(request_span.get_span_context())],
                             attributes={"sim.build.id": b["id"], "sim.build.kind": b["kind"],
                                         "sim.build.work_s": b["work_s"],
                                         **{f"sim.build.cost.{r}": v for r, v in b["cost"].items()}})
    build_spans[b["id"]] = span


def finish_build_span(b):
    span = build_spans.pop(b["id"], None)
    queue_wait, build_s = b["started_t"] - b["enqueued_t"], b["done_t"] - b["started_t"]
    attrs = {"sim.build.queue_wait_s": queue_wait, "sim.build.build_s": build_s,
             "sim.build.stall_s": b["stall_s"], "sim.build.total_s": queue_wait + build_s}
    if span is not None:
        span.set_attributes(attrs)
        span.end()
    m = {"sim.build.kind": b["kind"]}
    h_build.record(queue_wait + build_s, m)
    h_build_stall.record(b["stall_s"], m)


def tick_once(dt=1.0):
    t0 = time.perf_counter()
    # Root span per tick = one wide event: every attribute below is queryable/groupable.
    with tracer.start_as_current_span("sim.tick") as span, lock:
        r = sim.tick(dt, tracer)
        snap = sim.snapshot()
        bld = r["build"]
        if bld["started"] and (sp := build_spans.get(bld["started"]["id"])):
            b = bld["started"]
            sp.add_event("build.started", {"sim.build.queue_wait_s": b["started_t"] - b["enqueued_t"]})
        if bld["completed"]: finish_build_span(bld["completed"])
        span.set_attributes({
            "sim.t": snap["t"], "sim.tier": snap["tier"], "sim.phase": snap["phase"],
            "sim.dps": snap["dps"], "sim.dps.target": snap["slo_target"], "sim.slo.met": snap["slo_met"],
            "sim.bottleneck": r["bottleneck"], "sim.coin": snap["coin"], "sim.ammo": snap["ammo"],
            "sim.research": snap["research"], "sim.build.queue_depth": bld["queue_depth"],
            "sim.build.active": bld["active"], "sim.build.rate_fraction": bld["rate_fraction"],
            "sim.build.stall_reason": bld["stall_reason"], "sim.damage.tick": r["turret"]["damage"],
            "sim.lever.ore_split": snap["levers"]["ore_split"], "sim.lever.power": snap["levers"]["power"],
            "sim.lever.priority": snap["levers"]["priority"],
            **{f"sim.units.{k}": v for k, v in snap["units"].items()},
            **{f"sim.level.{k}": v for k, v in snap["levels"].items()},
        })
    attrs = {"sim.bottleneck": r["bottleneck"], "sim.phase": snap["phase"]}
    m_damage.add(r["turret"]["damage"], attrs)
    m_wasted.add(r["mint"]["overflow"], attrs)
    m_slo.add(1, {"sim.slo.met": snap["slo_met"], "sim.tier": snap["tier"]})
    h_tick.record(time.perf_counter() - t0)
    global _last_slo
    if snap["slo_met"] != _last_slo:  # log transitions, not every tick
        _last_slo = snap["slo_met"]
        log.info("slo met" if _last_slo else "slo breached", extra={"dps": snap["dps"], "target": snap["slo_target"],
                                            "bottleneck": r["bottleneck"]})


def tick_loop(interval=1.0):
    while True:
        time.sleep(interval)
        try:
            tick_once()
        except Exception:
            log.exception("tick failed")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _traced(self, method):
        path = self.path.split("?")[0]
        name = f"{method} {path}"
        t0 = time.perf_counter()
        status = 500
        with tracer.start_as_current_span(name, kind=trace.SpanKind.SERVER) as span:
            span.set_attributes({"http.request.method": method, "url.path": path})
            try:
                status = self._route(method, path, span)
            except (ValueError, KeyError) as e:
                status = 400
                span.set_attribute("app.error", str(e))
                self._send(400, {"error": str(e)})
            except Exception as e:
                span.record_exception(e); span.set_status(Status(StatusCode.ERROR))
                self._send(500, {"error": "internal"})
            span.set_attribute("http.response.status_code", status)
        h_http.record(time.perf_counter() - t0, {"http.request.method": method, "http.route": path,
                                                 "http.response.status_code": status})

    def do_GET(self): self._traced("GET")
    def do_POST(self): self._traced("POST")

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _route(self, method, path, span):
        if method == "GET" and path in ("/", "/index.html"):
            self._send(200, (STATIC / "index.html").read_bytes(), "text/html"); return 200
        if method == "GET" and path == "/api/state":
            with lock: self._send(200, sim.snapshot())
            return 200
        if method == "POST" and path == "/api/lever":
            b = self._body()
            with lock: old, new = sim.set_lever(b["name"], b["value"]); snap = sim.snapshot()
            span.set_attributes({"app.lever.name": b["name"], "app.lever.old": str(old), "app.lever.new": str(new),
                                 "sim.dps.at_change": snap["dps"], "sim.bottleneck.at_change": snap["last"].get("bottleneck", "")})
            m_levers.add(1, {"app.lever.name": b["name"]})
            log.info("lever changed", extra={"lever": b["name"], "old": str(old), "new": str(new)})
            self._send(200, snap); return 200
        if method == "POST" and path in ("/api/upgrade", "/api/unit"):
            b = self._body()
            kind = "upgrade" if path.endswith("upgrade") else "unit"
            with lock:
                before = sim.dps()
                ok, reason, cost = (sim.buy_upgrade if kind == "upgrade" else sim.buy_unit)(b["id"])
                snap = sim.snapshot()
                if ok and kind == "unit":
                    start_build_span(sim.queue[-1], span)
                    span.set_attributes({"app.build.id": sim.queue[-1]["id"], "app.build.queue_depth": len(sim.queue)})
            span.set_attributes({"app.purchase.kind": kind, "app.purchase.id": b["id"], "app.purchase.ok": ok,
                                 "app.purchase.reason": reason, "app.purchase.cost": cost,
                                 "sim.coin.after": snap["coin"], "sim.dps.before": before})
            m_upgrades.add(1, {"app.purchase.kind": kind, "app.purchase.id": b["id"], "app.purchase.ok": ok})
            self._send(200 if ok else 409, {**snap, "ok": ok, "reason": reason}); return 200 if ok else 409
        self._send(404, {"error": "not found"}); return 404


def _bind(port):
    try:
        return ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:  # port busy: let the OS pick one
        return ThreadingHTTPServer(("127.0.0.1", 0), Handler)


def main():
    shutdown = telemetry.setup()
    threading.Thread(target=tick_loop, daemon=True).start()
    srv = _bind(int(os.getenv("PORT", "8000")))
    port = srv.server_address[1]
    url = f"http://127.0.0.1:{port}"
    log.info("idle-dps listening", extra={"port": port})
    print(f"{url}  (Ctrl+C to quit)")
    if os.getenv("NO_BROWSER") != "1":
        threading.Timer(0.5, webbrowser.open, args=[url]).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        shutdown()


if __name__ == "__main__":
    main()
