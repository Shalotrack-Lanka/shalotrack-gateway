import os
import logging
from opentelemetry import trace, metrics
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.instrumentation.logging import LoggingInstrumentor

# Service identity
resource = Resource.create(attributes={
    "service.name": "shalotrack-gateway",
    "service.namespace": "production",
    "deployment.environment": os.getenv("ENVIRONMENT", "prod")
})

OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")

# ---------------------------------------------------------------------------
# OTel export timeout — critical for availability
#
# Without a timeout, BatchSpanProcessor and PeriodicExportingMetricReader
# block indefinitely when the OTel collector is unreachable (e.g. Tempo
# OOM crash causes collector retry storm). This was the root cause of
# DB pool exhaustion: spans held DB connections open while waiting for
# OTel to export.
#
# With these timeouts:
# - Export attempt gives up after 2 seconds
# - Spans are dropped (not data — just telemetry) if collector is down
# - Gateway DB connections are NEVER held by OTel delays
# - GPS tracking continues even when SRE observability stack is down
# ---------------------------------------------------------------------------
OTEL_EXPORT_TIMEOUT_MILLIS = int(os.getenv("OTEL_EXPORT_TIMEOUT_MILLIS", "2000"))  # 2 seconds
OTEL_METRIC_INTERVAL_MILLIS = int(os.getenv("OTEL_METRIC_INTERVAL_MILLIS", "10000"))  # 10 seconds


class _NoOpTelemetry:
    """
    Fallback telemetry when OTel initialisation fails.
    All metric operations become no-ops so the gateway starts
    regardless of OTel collector availability.
    """
    class _NoOpCounter:
        def add(self, *args, **kwargs): pass
    class _NoOpHistogram:
        def record(self, *args, **kwargs): pass
    class _NoOpTracer:
        def start_as_current_span(self, *args, **kwargs):
            from contextlib import contextmanager
            @contextmanager
            def noop(*a, **kw):
                class _Span:
                    def set_attribute(self, *a, **k): pass
                    def record_exception(self, *a, **k): pass
                    def set_status(self, *a, **k): pass
                yield _Span()
            return noop()

    def __init__(self):
        self.tracer = self._NoOpTracer()
        self.active_connections = self._NoOpCounter()
        self.packets_processed = self._NoOpCounter()
        self.parsing_errors = self._NoOpCounter()
        self.processing_latency = self._NoOpHistogram()


class TelemetryManager:
    def __init__(self):
        try:
            self._init_tracing()
            self._init_metrics()
            self._init_logging_correlation()
        except Exception as e:
            # OTel init failure must NEVER crash the gateway
            import sys
            print(f"⚠️  OTel initialisation failed ({e}) — running without telemetry", file=sys.stderr)
            noop = _NoOpTelemetry()
            self.tracer = noop.tracer
            self.active_connections = noop.active_connections
            self.packets_processed = noop.packets_processed
            self.parsing_errors = noop.parsing_errors
            self.processing_latency = noop.processing_latency

    def _init_tracing(self):
        tracer_provider = TracerProvider(resource=resource)
        span_processor = BatchSpanProcessor(
            OTLPSpanExporter(endpoint=OTLP_ENDPOINT, insecure=True),
            # Export timeout — give up after 2s, never block gateway threads
            export_timeout_millis=OTEL_EXPORT_TIMEOUT_MILLIS,
            # Schedule delay — how often to flush the span queue
            schedule_delay_millis=5000,
            # Max queue size — drop oldest spans if queue fills up
            max_queue_size=512,
            # Max batch size per export
            max_export_batch_size=64,
        )
        tracer_provider.add_span_processor(span_processor)
        trace.set_tracer_provider(tracer_provider)
        self.tracer = trace.get_tracer("shalotrack-gateway-tracer")

    def _init_metrics(self):
        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=OTLP_ENDPOINT, insecure=True),
            # Export interval — push metrics every 10s (was 5s, reduces pressure)
            export_interval_millis=OTEL_METRIC_INTERVAL_MILLIS,
            # Export timeout — give up after 2s if collector is slow
            export_timeout_millis=OTEL_EXPORT_TIMEOUT_MILLIS,
        )
        meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
        metrics.set_meter_provider(meter_provider)
        self.meter = metrics.get_meter("shalotrack-gateway-meter")

        self.active_connections = self.meter.create_up_down_counter(
            name="gateway_active_tcp_connections",
            description="Number of currently connected GPS devices",
            unit="1"
        )
        self.packets_processed = self.meter.create_counter(
            name="gateway_packets_processed_total",
            description="Total count of processed packets",
            unit="1"
        )
        self.parsing_errors = self.meter.create_counter(
            name="gateway_parsing_errors_total",
            description="Total count of packet parsing or validation failures",
            unit="1"
        )
        self.processing_latency = self.meter.create_histogram(
            name="gateway_packet_processing_latency_seconds",
            description="Time spent processing a packet from receipt to ACK",
            unit="s"
        )

    def _init_logging_correlation(self):
        LoggingInstrumentor().instrument(set_logging_format=True)


# Global singleton — initialised once at startup
telemetry = TelemetryManager()