"""Tests for repo/huge_files_in_git rule."""

from pathlib import Path
from uuid import uuid4

import pytest
from hypothesis import HealthCheck, given, settings as hyp_settings
from hypothesis import strategies as st

from rac_control_plane.detection.contracts import RepoContext, RepoFile
from rac_control_plane.detection.rules.repo.huge_files_in_git import (
    RULE,
    _DEFAULT_THRESHOLD_BYTES,
    _evaluate,
)


def _ctx(files: list[RepoFile], tmp_path: Path) -> RepoContext:
    return RepoContext(
        repo_root=tmp_path,
        submission_id=uuid4(),
        dockerfile_path="Dockerfile",
        dockerfile_text="FROM python:3.12\n",
        files=tuple(files),
        manifest=None,
        submission_metadata={},
    )


def test_file_exactly_at_threshold_fires(tmp_path: Path) -> None:
    """File exactly at threshold → 1 finding (>= semantics)."""
    threshold = 1024 * 1024  # 1 MiB for test speed
    files = [RepoFile(path="data.bin", size_bytes=threshold)]
    findings = list(_evaluate(_ctx(files, tmp_path), threshold_bytes=threshold))
    assert len(findings) == 1
    assert findings[0].rule_id == "repo/huge_files_in_git"
    assert "data.bin" in findings[0].detail
    assert findings[0].file_path == "data.bin"


def test_file_above_threshold_fires(tmp_path: Path) -> None:
    """File above threshold → 1 finding."""
    threshold = 1024 * 1024
    files = [RepoFile(path="model.pkl", size_bytes=threshold + 1)]
    findings = list(_evaluate(_ctx(files, tmp_path), threshold_bytes=threshold))
    assert len(findings) == 1


def test_file_below_threshold_no_finding(tmp_path: Path) -> None:
    """File below threshold → 0 findings."""
    threshold = 1024 * 1024
    files = [RepoFile(path="small.py", size_bytes=threshold - 1)]
    findings = list(_evaluate(_ctx(files, tmp_path), threshold_bytes=threshold))
    assert len(findings) == 0


def test_multiple_huge_files_ac46(tmp_path: Path) -> None:
    """Two huge files → two findings (AC4.6 repeat firing)."""
    threshold = 1024 * 1024
    files = [
        RepoFile(path="model1.pkl", size_bytes=threshold),
        RepoFile(path="model2.pkl", size_bytes=threshold + 1000),
    ]
    findings = list(_evaluate(_ctx(files, tmp_path), threshold_bytes=threshold))
    assert len(findings) == 2
    paths = {f.file_path for f in findings}
    assert paths == {"model1.pkl", "model2.pkl"}


def test_no_files_no_finding(tmp_path: Path) -> None:
    findings = list(_evaluate(_ctx([], tmp_path), threshold_bytes=1024))
    assert len(findings) == 0


def test_default_threshold_is_fifty_mib() -> None:
    """Default threshold constant is 50 MiB."""
    assert _DEFAULT_THRESHOLD_BYTES == 50 * 1024 * 1024


def test_uses_settings_threshold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Rule respects settings.detection_huge_file_threshold_bytes when available."""
    test_threshold = 100  # 100 bytes

    # Set all required settings env vars
    monkeypatch.setenv("RAC_DETECTION_HUGE_FILE_THRESHOLD_BYTES", str(test_threshold))
    monkeypatch.setenv("RAC_ENV", "dev")
    monkeypatch.setenv("RAC_INSTITUTION_NAME", "Test")
    monkeypatch.setenv("RAC_PARENT_DOMAIN", "test.local")
    monkeypatch.setenv("RAC_BRAND_LOGO_URL", "https://example.com/logo.png")
    monkeypatch.setenv("RAC_IDP_TENANT_ID", "t")
    monkeypatch.setenv("RAC_IDP_CLIENT_ID", "c")
    monkeypatch.setenv("RAC_IDP_API_CLIENT_ID", "a")
    monkeypatch.setenv("RAC_PG_HOST", "localhost")
    monkeypatch.setenv("RAC_PG_DB", "test")
    monkeypatch.setenv("RAC_PG_USER", "test")
    monkeypatch.setenv("RAC_PG_PASSWORD", "test")
    monkeypatch.setenv("RAC_KV_URI", "https://kv.vault.azure.net/")
    monkeypatch.setenv("RAC_BLOB_ACCOUNT_URL", "https://blob.core.windows.net/")
    monkeypatch.setenv("RAC_ACR_LOGIN_SERVER", "test.azurecr.io")
    monkeypatch.setenv("RAC_ACA_ENV_RESOURCE_ID", "/subscriptions/t/resourceGroups/t/providers/Microsoft.App/managedEnvironments/t")
    monkeypatch.setenv("RAC_SCAN_SEVERITY_GATE", "high")
    monkeypatch.setenv("RAC_APPROVER_ROLE_RESEARCH", "r")
    monkeypatch.setenv("RAC_APPROVER_ROLE_IT", "i")

    from rac_control_plane.settings import get_settings
    get_settings.cache_clear()
    try:
        files = [RepoFile(path="tiny.py", size_bytes=test_threshold + 1)]
        # Without passing threshold_bytes, it reads from settings
        findings = list(_evaluate(_ctx(files, tmp_path)))
        assert len(findings) == 1
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Property test: lowering threshold never reduces finding count
# ---------------------------------------------------------------------------

@given(
    sizes=st.lists(st.integers(min_value=0, max_value=200 * 1024 * 1024), min_size=1, max_size=10),
    threshold_low=st.integers(min_value=1, max_value=10 * 1024 * 1024),
)
@hyp_settings(
    max_examples=50,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_property_monotonic_threshold(
    sizes: list[int],
    threshold_low: int,
    tmp_path: Path,
) -> None:
    """Property: lowering threshold never reduces finding count."""
    files = [RepoFile(path=f"file_{i}.bin", size_bytes=s) for i, s in enumerate(sizes)]
    ctx = _ctx(files, tmp_path)

    count_high = len(list(_evaluate(ctx, threshold_bytes=threshold_low * 2)))
    count_low = len(list(_evaluate(ctx, threshold_bytes=threshold_low)))

    assert count_low >= count_high, (
        f"Lower threshold {threshold_low} yielded fewer findings ({count_low}) "
        f"than higher threshold {threshold_low * 2} ({count_high})"
    )


def test_rule_constant() -> None:
    assert RULE.rule_id == "repo/huge_files_in_git"
    assert RULE.version == 1
