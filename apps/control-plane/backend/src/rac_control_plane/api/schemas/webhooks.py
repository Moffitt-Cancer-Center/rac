# pattern: Functional Core
"""Pydantic schemas for webhook callback ingestion."""

from typing import Literal

from pydantic import BaseModel


class PipelineCallback(BaseModel):
    """Payload sent by the rac-pipeline on scan completion."""

    verdict: Literal["passed", "rejected", "partial_passed", "partial_rejected", "build_failed"]
    effective_severity: Literal["none", "low", "medium", "high", "critical"]
    findings: list[dict]  # type: ignore[type-arg]
    build_log_uri: str | None = None
    sbom_uri: str | None = None
    grype_report_uri: str | None = None
    defender_report_uri: str | None = None
    image_digest: str | None = None
    image_ref: str | None = None
    defender_timed_out: bool = False
