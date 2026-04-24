"""Integration tests for cost export ingestion.

Uses a mock BlobServiceClient (no real Azure calls) and a real Postgres
testcontainer for the DB upsert assertions.

Verifies: rac-v1.AC11.2
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.data.models import CostSnapshotMonthly
from rac_control_plane.services.cost.ingest import (
    IngestResult,
    _extract_tag_value,
    _infer_year_month,
    parse_cost_csv,
    ingest_daily_cost_exports,
)


# ---------------------------------------------------------------------------
# Pure helper tests
# ---------------------------------------------------------------------------


def test_extract_tag_value_json() -> None:
    tags = '{"rac_app_slug": "my-app", "env": "dev"}'
    assert _extract_tag_value(tags, "rac_app_slug") == "my-app"
    assert _extract_tag_value(tags, "env") == "dev"
    assert _extract_tag_value(tags, "missing") is None


def test_extract_tag_value_kv_pairs() -> None:
    tags = "rac_app_slug: my-app; env: dev"
    assert _extract_tag_value(tags, "rac_app_slug") == "my-app"
    assert _extract_tag_value(tags, "env") == "dev"


def test_extract_tag_value_empty() -> None:
    assert _extract_tag_value("", "rac_app_slug") is None
    assert _extract_tag_value("  ", "rac_app_slug") is None


def test_infer_year_month_iso() -> None:
    assert _infer_year_month("2026-04-15") == "2026-04"
    assert _infer_year_month("2026-04-15T10:00:00Z") == "2026-04"
    assert _infer_year_month("") is None


def test_infer_year_month_us_format() -> None:
    assert _infer_year_month("04/15/2026") == "2026-04"


def test_parse_cost_csv_basic() -> None:
    """Parse a simple Cost Management CSV fixture."""
    csv_content = (
        "Date,Tags,CostInBillingCurrency\n"
        '2026-04-15,"{""rac_app_slug"": ""app-a""}",10.50\n'
        '2026-04-15,"{""rac_app_slug"": ""app-a""}",5.25\n'
        '2026-04-15,"{""rac_app_slug"": ""app-b""}",20.00\n'
        '2026-04-15,,3.00\n'  # untagged row
    )
    totals = parse_cost_csv(csv_content.encode())

    assert totals[("app-a", "2026-04")] == Decimal("15.75")
    assert totals[("app-b", "2026-04")] == Decimal("20.00")
    assert totals[("_untagged", "2026-04")] == Decimal("3.00")


def test_parse_cost_csv_no_cost_column_returns_empty() -> None:
    csv_content = "Date,Tags,WrongColumn\n2026-04-15,{},10.50\n"
    totals = parse_cost_csv(csv_content.encode())
    assert totals == {}


def test_parse_cost_csv_handles_bom() -> None:
    """BOM-prefixed UTF-8 CSV (common in Azure exports) is parsed correctly.

    Encodes the plain string to UTF-8 bytes then prepends the UTF-8 BOM bytes
    to simulate what Azure Cost Management exports produce.
    """
    csv_content = "Date,Tags,CostInBillingCurrency\n2026-04-01,,5.00\n"
    # Prepend BOM manually (b'\xef\xbb\xbf') then encode rest as utf-8
    bom_prefixed = b"\xef\xbb\xbf" + csv_content.encode("utf-8")
    totals = parse_cost_csv(bom_prefixed)
    assert ("_untagged", "2026-04") in totals


# ---------------------------------------------------------------------------
# Mock blob fixture builder
# ---------------------------------------------------------------------------


def _make_blob_service_client(
    blobs: list[dict],
) -> MagicMock:
    """Build a mock BlobServiceClient with the given blobs.

    Each blob dict: {
        'name': str,
        'metadata': dict | None,
        'content': bytes,
    }
    """
    mock_client = MagicMock()
    mock_container = MagicMock()
    mock_client.get_container_client.return_value = mock_container

    mock_blob_items = []
    mock_blob_clients: dict[str, MagicMock] = {}

    for blob_def in blobs:
        blob_item = MagicMock()
        blob_item.name = blob_def["name"]
        blob_item.metadata = blob_def.get("metadata") or {}
        mock_blob_items.append(blob_item)

        mock_blob_client = MagicMock()
        download_stream = MagicMock()
        download_stream.readall.return_value = blob_def["content"]
        mock_blob_client.download_blob.return_value = download_stream
        mock_blob_clients[blob_def["name"]] = mock_blob_client

    mock_container.list_blobs.return_value = iter(mock_blob_items)

    def _get_blob_client(name: str) -> MagicMock:
        return mock_blob_clients[name]

    mock_container.get_blob_client.side_effect = _get_blob_client

    return mock_client


# ---------------------------------------------------------------------------
# DB ingest tests
# ---------------------------------------------------------------------------


CSV_WITH_TWO_APPS = (
    "Date,Tags,CostInBillingCurrency\n"
    '2026-04-15,"{""rac_app_slug"": ""app-x""}",100.00\n'
    '2026-04-15,"{""rac_app_slug"": ""app-y""}",50.00\n'
).encode()

CSV_UNTAGGED = (
    "Date,Tags,CostInBillingCurrency\n"
    "2026-04-15,,25.00\n"
).encode()


@pytest.mark.asyncio
async def test_ingest_inserts_snapshots(
    db_setup: AsyncSession,
) -> None:
    """Ingest a CSV with 2 tagged apps → 2 cost_snapshot_monthly rows."""
    mock_client = _make_blob_service_client(
        [{"name": "daily-export.csv", "metadata": {}, "content": CSV_WITH_TWO_APPS}]
    )

    result = await ingest_daily_cost_exports(
        db_setup,
        blob_client_factory=lambda: mock_client,
        now=datetime(2026, 4, 23, tzinfo=timezone.utc),
    )

    assert result.files_processed == 1
    assert result.rows_upserted == 2

    snaps = (
        await db_setup.execute(select(CostSnapshotMonthly).where(
            CostSnapshotMonthly.year_month == "2026-04",
            CostSnapshotMonthly.app_slug != "_untagged",
        ))
    ).scalars().all()

    slugs = {s.app_slug for s in snaps}
    assert "app-x" in slugs
    assert "app-y" in slugs


@pytest.mark.asyncio
async def test_ingest_already_processed_blobs_skipped(
    db_setup: AsyncSession,
) -> None:
    """Blobs with rac_processed=true metadata are skipped."""
    mock_client = _make_blob_service_client(
        [
            {
                "name": "old.csv",
                "metadata": {"rac_processed": "true"},
                "content": CSV_WITH_TWO_APPS,
            }
        ]
    )

    result = await ingest_daily_cost_exports(
        db_setup,
        blob_client_factory=lambda: mock_client,
    )

    assert result.files_processed == 0
    assert result.rows_upserted == 0


@pytest.mark.asyncio
async def test_ingest_upsert_replaces_not_sums(
    db_setup: AsyncSession,
) -> None:
    """Re-ingesting a blob REPLACES the cost_usd rather than summing it."""
    mock_client = _make_blob_service_client(
        [{"name": "daily.csv", "metadata": {}, "content": CSV_WITH_TWO_APPS}]
    )

    # First run
    await ingest_daily_cost_exports(
        db_setup,
        blob_client_factory=lambda: mock_client,
        now=datetime(2026, 4, 23, tzinfo=timezone.utc),
    )
    await db_setup.commit()

    # Second run with updated CSV (higher cost)
    updated_csv = (
        "Date,Tags,CostInBillingCurrency\n"
        '2026-04-15,"{""rac_app_slug"": ""app-x""}",200.00\n'
    ).encode()
    mock_client2 = _make_blob_service_client(
        [{"name": "daily2.csv", "metadata": {}, "content": updated_csv}]
    )

    await ingest_daily_cost_exports(
        db_setup,
        blob_client_factory=lambda: mock_client2,
        now=datetime(2026, 4, 23, 3, 0, tzinfo=timezone.utc),
    )
    await db_setup.commit()

    snap = (
        await db_setup.execute(
            select(CostSnapshotMonthly).where(
                CostSnapshotMonthly.app_slug == "app-x",
                CostSnapshotMonthly.year_month == "2026-04",
            )
        )
    ).scalar_one()

    # Should be 200.00 (replaced), not 300.00 (summed)
    assert float(snap.cost_usd) == pytest.approx(200.00)


@pytest.mark.asyncio
async def test_ingest_untagged_rows_tracked(
    db_setup: AsyncSession,
) -> None:
    """Untagged rows are stored under app_slug='_untagged'."""
    mock_client = _make_blob_service_client(
        [{"name": "untagged.csv", "metadata": {}, "content": CSV_UNTAGGED}]
    )

    result = await ingest_daily_cost_exports(
        db_setup,
        blob_client_factory=lambda: mock_client,
    )
    await db_setup.commit()

    assert result.untagged_rows_skipped == 1

    snap = (
        await db_setup.execute(
            select(CostSnapshotMonthly).where(
                CostSnapshotMonthly.app_slug == "_untagged",
                CostSnapshotMonthly.year_month == "2026-04",
            )
        )
    ).scalar_one_or_none()

    assert snap is not None
    assert float(snap.untagged_usd) == pytest.approx(25.00)
