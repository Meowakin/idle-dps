# idle-dps

Idle damage sim: no waves, no lose state, score = damage per second.
Ore from **miners** is split by a live lever between a **mint** (→ gold for upgrades/labs) and turret **ammo**.
Turrets turn ammo into DPS. Each source has its own upgrade path; levers (ore split, power, target priority) are tunable live from a web UI.

    python -m venv .venv && .venv/bin/pip install -r requirements.txt
    .venv/bin/python -m idle_dps          # UI at http://127.0.0.1:8000 (PORT to change)
    .venv/bin/python -m pytest

## Telemetry (OpenTelemetry, OTLP/HTTP)
Sends traces, metrics and logs to a collector you run separately: `http://localhost:4318`
(override with `OTEL_EXPORTER_OTLP_ENDPOINT`). Honeycomb is a collector exporter concern, not this app's.

| Principle | Where |
|---|---|
| Wide events | `sim.tick` root span: levers, unit counts, upgrade levels, buffers, dps, bottleneck as attributes (`sim.*`) |
| Trace structure | tick → `sim.source.miner` / `sim.convert.mint` / `sim.source.lab` / `sim.turret.fire`; HTTP spans for each player action with before/after state |
| Slice by lever | `sim.lever.*` on every tick, `app.lever.*` on change spans — "did DPS drop after the power lever moved?" |
| SLO | `sim.slo.met` (boolean SLI), `sim.dps.target` per tier; counter `sim.slo.ticks` |
| Metrics | gauges `sim.damage.dps`, `sim.ore.rate`, `sim.gold.rate`, balances; counters `sim.damage.total`, `sim.ore.overflow`, `sim.upgrade.purchased`, `sim.lever.changed`; histograms for tick and HTTP duration |
| Sampling | head sampler drops `GET /api/state` (1 Hz UI poll); metrics still count it |
| Logs | correlated with trace ids; emitted on SLO transitions only, not per tick |
| Resource | `service.name`, `service.version`, per-process `service.instance.id`, `deployment.environment` |

Try in Honeycomb: `HEATMAP(sim.dps)` grouped by `sim.bottleneck`; `GROUP BY sim.lever.priority, sim.phase` on `sim.slo.met`.
