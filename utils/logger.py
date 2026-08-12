from datetime import datetime, timedelta, timezone
from opentelemetry import trace

# Sri Lanka is a fixed UTC+5:30 offset, year-round -- no daylight saving time
# to account for, so a simple constant offset is safe and doesn't depend on
# the container having a full IANA timezone database installed.
SRI_LANKA_OFFSET = timedelta(hours=5, minutes=30)
SRI_LANKA_TZ = timezone(SRI_LANKA_OFFSET)

def log(message):
    local_time = datetime.now(SRI_LANKA_TZ)
    
    # --- OTEL Trace Injection ---
    span_context = trace.get_current_span().get_span_context()
    
    if span_context.is_valid:
        trace_id = trace.format_trace_id(span_context.trace_id)
        span_id = trace.format_span_id(span_context.span_id)
        prefix = f"[{local_time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}] [trace_id={trace_id} span_id={span_id}]"
    else:
        prefix = f"[{local_time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}]"
        
    print(f"{prefix} {message}")