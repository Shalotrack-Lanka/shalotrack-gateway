from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.resources import Resource

_initialized = False
active_connections = None


def init_telemetry():
    """
    Sets up OTel metrics export. Wrapped so that if this fails for any
    reason — network issue, misconfiguration, anything — the gateway
    keeps running and processing real packets normally. Monitoring
    failing silently is acceptable here; it must never take down
    actual GPS data ingestion.
    """
    global _initialized, active_connections

    if _initialized:
        return

    try:
        resource = Resource.create({"service.name": "shalotrack-gateway"})

        exporter = OTLPMetricExporter(
            endpoint="http://otel.shalotrack.internal:4318/v1/metrics"
        )
        reader = PeriodicExportingMetricReader(
            exporter, export_interval_millis=15000
        )
        provider = MeterProvider(resource=resource, metric_readers=[reader])
        metrics.set_meter_provider(provider)

        meter = metrics.get_meter("shalotrack-gateway")

        active_connections = meter.create_up_down_counter(
            name="gateway_active_connections",
            description="Number of currently connected GPS tracker devices",
            unit="1",
        )

        _initialized = True

    except Exception as ex:
        print(f"⚠️ Telemetry init failed (non-fatal, gateway continues normally): {ex}")


def record_connection_opened():
    try:
        if active_connections:
            active_connections.add(1)
    except Exception:
        pass  # never let telemetry disrupt real connection handling


def record_connection_closed():
    try:
        if active_connections:
            active_connections.add(-1)
    except Exception:
        pass