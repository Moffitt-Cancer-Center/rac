# pattern: Imperative Shell
"""Cost export ingestion service.

Reads unprocessed Azure Cost Management export blobs from the 'cost-exports'
container, parses each CSV, upserts into cost_snapshot_monthly, and marks the
blob as processed via metadata (rac_processed=true).

Each export blob is assumed to represent complete MTD spend for the period
covered; the upsert therefore REPLACES the existing cost_usd value (not sums)
so that re-ingesting an updated export reflects the latest figure.

Untagged rows (no rac_app_slug tag) are accumulated and stored in the
untagged_usd column of the special row with app_slug='_untagged'.

Verifies: rac-v1.AC11.2
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from azure.storage.blob import BlobServiceClient

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class IngestResult:
    """Summary of one ingest run."""

    files_processed: int
    rows_upserted: int
    untagged_rows_skipped: int


# ---------------------------------------------------------------------------
# CSV parsing helpers (pure-ish; no I/O)
# ---------------------------------------------------------------------------

_TAG_COLUMN_CANDIDATES = (
    "Tags",
    "tags",
    "Tag",
    "tag",
    "ResourceTags",
)

_COST_COLUMN_CANDIDATES = (
    "CostInBillingCurrency",
    "Cost",
    "PreTaxCost",
    "costInBillingCurrency",
    "cost",
)

_DATE_COLUMN_CANDIDATES = (
    "Date",
    "UsageDate",
    "date",
    "usageDate",
    "BillingPeriodStartDate",
)


def _find_column(header: list[str], candidates: tuple[str, ...]) -> str | None:
    for c in candidates:
        if c in header:
            return c
    return None


def _extract_tag_value(tags_raw: str, key: str) -> str | None:
    """Extract a tag value from an Azure Cost Management tags string.

    Azure exports tags as:
      - JSON:       {"rac_app_slug": "my-app", ...}
      - KV pairs:   rac_app_slug: my-app; other_key: other_value

    Returns the value for `key`, or None if not found.
    """
    if not tags_raw:
        return None

    tags_raw = tags_raw.strip()

    # Try JSON first
    if tags_raw.startswith("{"):
        import json  # noqa: PLC0415
        try:
            parsed = json.loads(tags_raw)
            val = parsed.get(key)
            return str(val) if val is not None else None
        except (ValueError, AttributeError):
            pass

    # Key-value pairs: "key: value; key2: value2"
    for pair in tags_raw.split(";"):
        pair = pair.strip()
        if ":" in pair:
            k, _, v = pair.partition(":")
            if k.strip() == key:
                return v.strip()

    return None


def _infer_year_month(date_str: str) -> str | None:
    """Parse a date string and return YYYY-MM.

    Handles:
    - YYYY-MM-DD
    - YYYY-MM-DDTHH:MM:SSZ
    - MM/DD/YYYY
    """
    if not date_str:
        return None
    date_str = date_str.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%SZ", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime("%Y-%m")
        except ValueError:
            continue
    # Fallback: take first 7 chars if they look like YYYY-MM
    if len(date_str) >= 7 and date_str[4] == "-":
        return date_str[:7]
    return None


def parse_cost_csv(content: bytes) -> dict[tuple[str, str], Decimal]:
    """Parse Azure Cost Management export CSV and return cost by (app_slug, year_month).

    Untagged rows are stored under the key ('_untagged', year_month).

    Returns a dict mapping (app_slug, year_month) → total cost.
    """
    text_content = content.decode("utf-8-sig")  # handle BOM
    reader = csv.DictReader(io.StringIO(text_content))
    if reader.fieldnames is None:
        return {}

    header = list(reader.fieldnames)
    tag_col = _find_column(header, _TAG_COLUMN_CANDIDATES)
    cost_col = _find_column(header, _COST_COLUMN_CANDIDATES)
    date_col = _find_column(header, _DATE_COLUMN_CANDIDATES)

    if cost_col is None:
        logger.warning("cost_csv_missing_cost_column", header=header)
        return {}

    totals: dict[tuple[str, str], Decimal] = {}

    for row in reader:
        cost_raw = row.get(cost_col, "") if cost_col else ""
        try:
            cost = Decimal(cost_raw.replace(",", "").strip() or "0")
        except InvalidOperation:
            continue

        # Determine year_month
        year_month = "0000-00"
        if date_col:
            date_raw = row.get(date_col, "")
            parsed_ym = _infer_year_month(date_raw)
            if parsed_ym:
                year_month = parsed_ym

        # Determine app_slug from Tags
        app_slug = "_untagged"
        if tag_col:
            tags_raw = row.get(tag_col, "")
            slug = _extract_tag_value(tags_raw, "rac_app_slug")
            if slug:
                app_slug = slug

        key = (app_slug, year_month)
        totals[key] = totals.get(key, Decimal(0)) + cost

    return totals


# ---------------------------------------------------------------------------
# Ingest shell
# ---------------------------------------------------------------------------


async def ingest_daily_cost_exports(
    session: AsyncSession,
    *,
    blob_client_factory: Callable[[], BlobServiceClient] | None = None,
    container_name: str = "cost-exports",
    now: datetime | None = None,
) -> IngestResult:
    """Ingest unprocessed cost export blobs into cost_snapshot_monthly.

    1. Lists blobs in the container whose metadata does NOT contain
       rac_processed=true.
    2. For each blob, downloads and parses the CSV.
    3. Upserts into cost_snapshot_monthly: REPLACE cost_usd (each export
       represents the latest MTD figure for the period).
    4. Sets blob metadata: rac_processed=true, rac_processed_at=<iso>.

    Args:
        session: Async SQLAlchemy session (already in a transaction).
        blob_client_factory: Factory returning a BlobServiceClient.
                             Defaults to creating one from settings.
        container_name: Blob container name.
        now: Reference datetime for processed_at metadata.

    Returns:
        IngestResult summarising the run.
    """
    if now is None:
        now = datetime.now(UTC)

    if blob_client_factory is None:
        blob_client_factory = _default_blob_client_factory

    blob_service_client: BlobServiceClient = blob_client_factory()
    container_client = blob_service_client.get_container_client(container_name)

    files_processed = 0
    rows_upserted = 0
    untagged_rows_skipped = 0

    try:
        blob_list = container_client.list_blobs(include=["metadata"])
    except Exception:
        logger.exception("cost_ingest_list_blobs_failed", container=container_name)
        return IngestResult(
            files_processed=0,
            rows_upserted=0,
            untagged_rows_skipped=0,
        )

    for blob in blob_list:
        metadata = blob.metadata or {}
        if metadata.get("rac_processed") == "true":
            logger.debug("cost_ingest_blob_already_processed", name=blob.name)
            continue

        logger.info("cost_ingest_processing_blob", name=blob.name)

        blob_client = container_client.get_blob_client(blob.name)

        try:
            download_stream = blob_client.download_blob()
            content = download_stream.readall()
        except Exception:
            logger.exception("cost_ingest_download_failed", name=blob.name)
            continue

        # Parse CSV → per-(app_slug, year_month) cost totals
        totals = parse_cost_csv(content)

        if not totals:
            logger.warning("cost_ingest_empty_csv", name=blob.name)
        else:
            for (app_slug, year_month), cost_value in totals.items():
                if app_slug == "_untagged":
                    # Store untagged separately under cost_snapshot_monthly
                    # with app_slug='_untagged' so the dashboard can surface it.
                    await _upsert_snapshot(
                        session,
                        app_slug="_untagged",
                        year_month=year_month,
                        cost_usd=Decimal(0),
                        untagged_usd=cost_value,
                    )
                    untagged_rows_skipped += 1
                else:
                    await _upsert_snapshot(
                        session,
                        app_slug=app_slug,
                        year_month=year_month,
                        cost_usd=cost_value,
                        untagged_usd=Decimal(0),
                    )
                    rows_upserted += 1

        # Mark blob as processed
        try:
            blob_client.set_blob_metadata(
                metadata={
                    "rac_processed": "true",
                    "rac_processed_at": now.isoformat(),
                }
            )
        except Exception:
            logger.exception(
                "cost_ingest_set_metadata_failed", name=blob.name
            )
            # Non-fatal: the upserts are already committed; worst case this
            # blob is re-processed on the next run (idempotent upsert handles it).

        files_processed += 1

    logger.info(
        "cost_ingest_complete",
        files_processed=files_processed,
        rows_upserted=rows_upserted,
        untagged_rows_skipped=untagged_rows_skipped,
    )

    return IngestResult(
        files_processed=files_processed,
        rows_upserted=rows_upserted,
        untagged_rows_skipped=untagged_rows_skipped,
    )


async def _upsert_snapshot(
    session: AsyncSession,
    *,
    app_slug: str,
    year_month: str,
    cost_usd: Decimal,
    untagged_usd: Decimal,
) -> None:
    """Upsert a cost snapshot row (REPLACE, not sum)."""
    await session.execute(
        text("""
            INSERT INTO cost_snapshot_monthly
                (app_slug, year_month, cost_usd, untagged_usd, updated_at)
            VALUES
                (:app_slug, :year_month, :cost_usd, :untagged_usd, NOW())
            ON CONFLICT (app_slug, year_month)
            DO UPDATE SET
                cost_usd     = excluded.cost_usd,
                untagged_usd = excluded.untagged_usd,
                updated_at   = excluded.updated_at
        """),
        {
            "app_slug": app_slug,
            "year_month": year_month,
            "cost_usd": float(cost_usd),
            "untagged_usd": float(untagged_usd),
        },
    )


def _default_blob_client_factory() -> BlobServiceClient:
    """Create a BlobServiceClient from application settings."""
    from azure.storage.blob import BlobServiceClient  # noqa: PLC0415

    from rac_control_plane.provisioning.credentials import get_azure_credential  # noqa: PLC0415
    from rac_control_plane.settings import get_settings  # noqa: PLC0415

    settings = get_settings()
    return BlobServiceClient(
        account_url=settings.blob_account_url,
        credential=get_azure_credential(),
    )
