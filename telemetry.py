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

# Define Service Resource
resource = Resource.create(attributes={
    "service.name": "shalotrack-gateway",
    "service.namespace": "production",
    "deployment.environment": os.getenv("ENVIRONMENT", "prod")
})

# OTLP Collector Endpoint
OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "localhost:4317")

class TelemetryManager:
    def __init__(self):
        self._init_tracing()
        self._init_metrics()
        self._init_logging_correlation()

    def _init_tracing(self):
        """Sets up distributed tracing exporter."""
        tracer_provider = TracerProvider(resource=resource)
        span_processor = BatchSpanProcessor(
            OTLPSpanExporter(endpoint=OTLP_ENDPOINT, insecure=True)
        )
        tracer_provider.add_span_processor(span_processor)
        trace.set_tracer_provider(tracer_provider)
        self.tracer = trace.get_tracer("shalotrack-gateway-tracer")

    def _init_metrics(self):
        """Sets up Prometheus-compatible OTEL metric instruments."""
        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=OTLP_ENDPOINT, insecure=True),
            export_interval_millis=5000
        )
        meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
        metrics.set_meter_provider(meter_provider)
        self.meter = metrics.get_meter("shalotrack-gateway-meter")

        # Key Gateway Metrics
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
            description="Time spent processing a packet from receipt to database insertion",
            unit="s"
        )

    def _init_logging_correlation(self):
        """Injects trace_id and span_id into standard Python logs for Loki correlation."""
        LoggingInstrumentor().instrument(set_logging_format=True)

# Global Telemetry Instance
telemetry = TelemetryManager()