"""OpenTelemetry setup. Exports OTLP/HTTP to a separately-run collector (default localhost:4318)."""
from __future__ import annotations

import logging
import os
import uuid

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import (ALWAYS_ON, Decision, ParentBased, Sampler,
                                              SamplingResult)

SERVICE = "idle-dps"
VERSION = "0.1.0"
# The UI polls this every second; it is pure noise as a trace, so drop it at the head.
# Metrics (http.server.duration) still count every request, so nothing is invisible.
DROP_SPAN_NAMES = {"GET /api/state"}


class DropNoisySampler(Sampler):
    def __init__(self):
        self._inner = ParentBased(ALWAYS_ON)

    def should_sample(self, parent_context, trace_id, name, *a, **kw):
        if name in DROP_SPAN_NAMES:
            return SamplingResult(Decision.DROP)
        return self._inner.should_sample(parent_context, trace_id, name, *a, **kw)

    def get_description(self): return "DropNoisySampler"


def setup(endpoint: str | None = None):
    base = (endpoint or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or "http://localhost:4318").rstrip("/")
    resource = Resource.create({
        "service.name": os.getenv("OTEL_SERVICE_NAME", SERVICE),
        "service.version": VERSION,
        "service.instance.id": str(uuid.uuid4()),  # one id per game session/process
        "deployment.environment": os.getenv("DEPLOY_ENV", "local"),
    })
    tp = TracerProvider(resource=resource, sampler=DropNoisySampler())
    tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{base}/v1/traces")))
    trace.set_tracer_provider(tp)

    reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=f"{base}/v1/metrics"),
                                           export_interval_millis=5000)
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))

    lp = LoggerProvider(resource=resource)
    lp.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{base}/v1/logs")))
    # Log records emitted inside a span automatically carry its trace_id/span_id.
    logging.getLogger().addHandler(LoggingHandler(level=logging.INFO, logger_provider=lp))
    logging.getLogger().setLevel(logging.INFO)

    def shutdown():
        tp.shutdown(); lp.shutdown()
    return shutdown
