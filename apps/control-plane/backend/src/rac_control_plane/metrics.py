# pattern: Imperative Shell
"""OpenTelemetry metrics instrumentation for RAC Control Plane.

Declares and initializes instruments:
- rac.submissions.by_status: Counter for submission FSM transitions by target status
- rac.approvals.time_to_decision_seconds: Histogram for approval decision timing

Module-level state: `submission_counter` and `approval_duration_histogram` are
instantiated at import time from the default meter. configure_metrics() must be
called at startup BEFORE any metric emissions to set up the real MeterProvider.
Without configure_metrics(), emissions go to the default no-op provider.
"""

from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader


def configure_metrics(otlp_endpoint: str) -> None:
    """Initialize OpenTelemetry metrics with OTLP export.

    Call once at application startup after settings are loaded. Sets the global
    MeterProvider so that subsequent meter access (via metrics.get_meter) uses
    the configured provider instead of the default no-op.

    Args:
        otlp_endpoint: OTLP gRPC collector endpoint (e.g., "http://localhost:4317")
    """
    exporter = OTLPMetricExporter(endpoint=otlp_endpoint)
    reader = PeriodicExportingMetricReader(exporter, export_interval_millis=30_000)
    provider = MeterProvider(metric_readers=[reader])
    metrics.set_meter_provider(provider)


# Get meter at module import time; configure_metrics() must be called before emissions
_meter = metrics.get_meter("rac.control_plane")

# Submission counter: incremented each time an FSM transition completes.
# Labeled by the target status (e.g., "awaiting_scan", "scan_rejected", etc.).
submission_counter = _meter.create_counter(
    name="rac.submissions.by_status",
    description="Count of submission FSM state transitions, labeled by target status.",
    unit="1",
)

# Approval duration histogram: recorded in Phase 5 when approval decisions are made.
# Tracks wall-clock seconds from submission creation to first approval decision.
# Labeled by decision type (e.g., "approved", "rejected").
approval_duration_histogram = _meter.create_histogram(
    name="rac.approvals.time_to_decision_seconds",
    description="Wall-clock seconds from submission creation to first approval decision.",
    unit="s",
)
