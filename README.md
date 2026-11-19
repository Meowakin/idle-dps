# idle-dps

Idle simulation where an economic foundation feeds into generating an abstract damage output. Intended to be an example of OpenTelemetry instrumentation and to generate data to show off the power of Observability.
Ore from **miners** is split by a live lever between a **mint** (→ coin for upgrades/labs) and turret **ammo**.
Buying a unit enqueues a build (FIFO, one at a time): it draws coin and ore (before the ore split) each tick at a capped rate and stalls when starved.
Turrets turn ammo into DPS. Each source has its own upgrade path; levers (ore split, power, target priority) are tunable live from a web UI.

    python -m venv .venv && .venv/bin/pip install -r requirements.txt
    .venv/bin/python -m idle_dps          # UI at http://127.0.0.1:8000 (PORT to change)
    .venv/bin/python -m pytest

## Windows exe (no Python needed)
Download `idle-dps-windows` from the latest **build** run in GitHub Actions and run `idle-dps.exe`.
It opens your browser to the UI (set `NO_BROWSER=1` to skip). Build locally on Windows: `pip install pyinstaller && pyinstaller idle-dps.spec`.

## Telemetry (OpenTelemetry, OTLP/HTTP)
Sends traces, metrics and logs to a collector you run separately: `http://localhost:4318`
(override with `OTEL_EXPORTER_OTLP_ENDPOINT`). Honeycomb is a collector exporter concern, not this app's.

| Principle | Where |
|---|---|
| Wide events | `sim.tick` root span: levers, unit counts, upgrade levels, buffers, dps, bottleneck as attributes (`sim.*`) |
| Trace structure | tick → `sim.source.miner` / `sim.convert.mint` / `sim.source.lab` / `sim.turret.fire`; HTTP spans for each player action with before/after state |
| Slice by lever | `sim.lever.*` on every tick, `app.lever.*` on change spans — "did DPS drop after the power lever moved?" |
| Builds | one long-lived `sim.build.unit` span per unit (own trace, linked to the enqueuing request): `sim.build.{kind,queue_wait_s,build_s,stall_s,total_s,cost.*}`; `sim.tick` carries `sim.build.{queue_depth,active,rate_fraction,stall_reason}`; bottleneck `build-starved`; histograms `sim.build.duration`/`sim.build.stall`, gauge `sim.build.queue_depth` |
| SLO | `sim.slo.met` (boolean SLI), `sim.dps.target` per tier; counter `sim.slo.ticks` |
| Metrics | gauges `sim.damage.dps`, `sim.ore.rate`, `sim.coin.rate`, balances; counters `sim.damage.total`, `sim.ore.overflow`, `sim.upgrade.purchased`, `sim.lever.changed`; histograms for tick and HTTP duration |
| Sampling | head sampler drops `GET /api/state` (1 Hz UI poll); metrics still count it |
| Logs | correlated with trace ids; emitted on SLO transitions only, not per tick |
| Resource | `service.name`, `service.version`, per-process `service.instance.id`, `deployment.environment` |

Try in Honeycomb: `HEATMAP(sim.dps)` grouped by `sim.bottleneck`; `GROUP BY sim.lever.priority, sim.phase` on `sim.slo.met`.

### Observability principles (read before changing code)
Agents and humans working here follow the same rules; see [AGENTS.md](AGENTS.md) for the checklist.

- **Observability = answering questions you didn't plan for**, without shipping new code. Data shape is decided at query time, so keep events wide and raw instead of pre-aggregating.
- **A span is a wide event.** Prefer adding attributes to an existing span over creating new spans. Every attribute is a dimension BubbleUp can diff on. Add the ones that answer *who is affected* (levers, tier, phase), *what changed* (version, upgrade levels, lever old/new) and *where is the bottleneck* (`sim.bottleneck`, stage outputs, buffers).
- **High cardinality is a feature** on spans (ids, exact balances, DPS). It is not on metric attributes: keep metric labels low-cardinality (`sim.tier`, `app.lever.name`), never ids or free text.
- **Create a span only if it is interesting and aggregable**: variable latency or can fail, and useful when grouped by name and attributes. Tick stages and player actions qualify; private helpers, loop iterations and pure getters do not. Roll tiny repeated work into attributes on the parent. No `test-span` leftovers.
- **Layer signals, don't duplicate them.** Traces give causality and full context; metrics give cheap alerting and trends (and survive sampling); logs are for rare, notable events (SLO transitions), correlated by trace id.
- **Naming**: dot-namespaced attributes. `sim.*` for simulation state, `app.*` for player/HTTP actions, OTel semantic conventions (`http.request.method`, `url.path`, `http.response.status_code`) where one exists. Span names describe the operation and are low-cardinality (route templates, not ids).
- **Errors**: set span status to ERROR plus a low-cardinality, greppable category attribute for aggregation; keep full messages and stack traces on the exception event/log, not as a `GROUP BY` field.
- **Sampling trades cost for debuggability.** Head sampling here drops only the noisy poll; do not add ratio sampling to the app. If volume ever matters, tail-sample in the collector (Refinery / `tail_sampling`) so errors and slow ticks are kept.
- **Define SLOs on events**: `sim.slo.met` is the SLI, `sim.dps.target` the per-tier threshold. Check budget burn before and after balance changes.
- **Instrument like you test.** A new feature, lever or sim stage means new telemetry in the same change, reviewed with the code, plus a follow-up whenever BubbleUp came back empty (that means a missing dimension).

### The core analysis loop
**Define** the question (e.g. "DPS fell after the last build") → **Visualize** (`HEATMAP(sim.dps)`, `P99(sim.tick.duration)`) → **Investigate** (BubbleUp the low-DPS outliers vs baseline; open a trace) → **Evaluate** (re-query with and without the suspected cause, e.g. `sim.lever.power`). Then loop. If you cannot slice by the thing you suspect, add the attribute.

### Verifying instrumentation
Run a local collector (or a `debug` exporter) and confirm: one `sim.tick` per tick with its child stage spans, no `GET /api/state` spans, `sim.*` attributes populated with correct types (numbers as numbers, booleans as booleans), logs carrying trace ids, and metrics arriving. Do this after any telemetry change; passing tests only proves the sim works.

### Pitfalls
- Honeycomb ingest needs the `x-honeycomb-team` header (set in the collector's exporter, not here); without it data is silently dropped.
- Never put secrets, API keys or personal data in attributes.
- Use `P50/P95/P99` and `HEATMAP` for latency, not `AVG`.
- Dynamic attribute *names* explode columns; keep keys fixed and put variable data in values.
