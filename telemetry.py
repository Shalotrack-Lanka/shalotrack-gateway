"""
telemetry.py — NoOp stub

OTel distributed tracing has been disabled permanently.

Root cause: OTel export queue fills when Tempo (SRE t3.micro) OOMs.
BatchSpanProcessor then blocks gateway threads which hold DB connections,
exhausting the pool (max=150) and stopping GPS tracking.

Decision: GPS tracking reliability > distributed traces on a TCP server.
Prometheus metrics via /metrics endpoint (if added) remain the observability
path. Traces removed entirely until SRE is on a stable, sized instance.

All callers continue to work — tracer and metric objects exist as no-ops.
"""

import logging

logger = logging.getLogger(__name__)


class _NoOpSpan:
    def set_attribute(self, *args, **kwargs): pass
    def record_exception(self, *args, **kwargs): pass
    def set_status(self, *args, **kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass


class _NoOpContextManager:
    def __init__(self): pass
    def __enter__(self): return _NoOpSpan()
    def __exit__(self, *args): pass


class _NoOpTracer:
    def start_as_current_span(self, name, *args, **kwargs):
        return _NoOpContextManager()


class _NoOpCounter:
    def add(self, amount, *args, **kwargs): pass


class _NoOpHistogram:
    def record(self, amount, *args, **kwargs): pass


class TelemetryManager:
    """
    Drop-in replacement for the OTel TelemetryManager.
    All attributes match the original interface so no call sites change.
    """

    def __init__(self):
        self.tracer              = _NoOpTracer()
        self.active_connections  = _NoOpCounter()
        self.packets_processed   = _NoOpCounter()
        self.parsing_errors      = _NoOpCounter()
        self.processing_latency  = _NoOpHistogram()
        logger.info("Telemetry: running in NoOp mode (OTel disabled)")


# Global singleton — initialised once at startup
telemetry = TelemetryManager()
