# Agent guide: idle-dps

Observability is a first-class requirement in this repo, not an add-on. Principles follow Honeycomb's
OpenTelemetry guidance. Full rationale and the current attribute/metric inventory are in
[README.md](README.md#telemetry-opentelemetry-otlphttp). Setup code is in `idle_dps/telemetry.py`.

## Rules for every change
1. **New behavior ships with telemetry.** A new lever, sim stage, upgrade, or endpoint must emit
   attributes (and a span if warranted) in the same change.
2. **Wide events over new spans.** Add attributes to the existing span (`sim.tick`, the HTTP action span,
   a stage span) before creating a child span. Create a span only if the work is *interesting* (variable
   latency or can fail) and *aggregable* (useful grouped by name + attributes). No spans for helpers,
   loop iterations, or getters. Never leave `test`/`debug` spans.
3. **Attributes answer three questions:** who is affected (levers, tier, phase), what changed (version,
   upgrade levels, before/after values), where is the bottleneck (`sim.bottleneck`, stage outputs, buffers).
4. **Naming:** dot-namespaced, `sim.*` for simulation state, `app.*` for player/HTTP actions, OTel semantic
   conventions where they exist. Fixed keys only; variable data goes in values. Span names are low-cardinality.
5. **Cardinality:** high-cardinality values are fine on spans. Metric attributes must stay low-cardinality.
6. **Types matter:** numbers as numbers, booleans as booleans, so they can be aggregated and charted.
7. **Errors:** span status ERROR + low-cardinality error category attribute; full message/stack on the
   exception event or log, not as a group-by field.
8. **Layer signals:** traces for causality, metrics for cheap alerts/trends, logs only for rare events
   (e.g. SLO transitions) with trace correlation. Don't log per tick.
9. **Don't add head ratio sampling.** Only the noisy `GET /api/state` poll is dropped. Volume control belongs
   in the collector (tail sampling).
10. **No secrets or personal data** in any attribute, metric label, or log.
11. **Keep the SLO honest.** `sim.slo.met` / `sim.dps.target` are the SLI; changes to balance or tiers must
    keep these meaningful and be checked against budget burn.

## Verify, don't assume
After touching telemetry, run the app against a local collector (or debug exporter) and check: tick span
shape and child stages, expected attributes with correct types, no poll spans, correlated logs, metrics
present. Passing tests only proves the sim works, not that it is observable.

## Debugging loop
Define the question → visualize (`HEATMAP(sim.dps)`, percentiles, never AVG for latency) → investigate
(BubbleUp, then a trace) → evaluate (query with/without the suspected cause). If BubbleUp finds nothing,
the fix is usually a missing attribute: add it and re-run.

## Honeycomb tooling
If the Honeycomb MCP/plugin is available, use its skills (`otel-instrumentation`, `observability-fundamentals`,
`query-patterns`, `production-investigation`, `slos-and-triggers`) and the `instrumentation-advisor` agent
for gap analysis. Honeycomb is configured on the collector, not in this app.
