"""Tests for manifest/form_mapper.py — Functional Core.

Verifies: rac-v1.AC8.5 (form-generated manifest is indistinguishable from
YAML-parsed manifest with equivalent contents).

Coverage:
- Upload asset form input → valid UploadAsset in manifest.
- External URL asset with both fields → valid ExternalUrlAsset.
- External URL missing sha256 → ManifestParseError(code="external_url_missing_fields").
- External URL missing url → ManifestParseError(code="external_url_missing_fields").
- SharedReference passes build_manifest_from_form (rejection is at submission boundary).
- Duplicate names / mount_paths from form → ManifestParseError.
- Round-trip: build form manifest → model_dump → manifest_from_dict → equal.
- AC8.5: form-generated and YAML-parsed manifests with equivalent contents are equal.
- Default values (target_port, cpu_cores, memory_gb, env_vars) match schema defaults.
"""

from __future__ import annotations

import textwrap

import pytest
from hypothesis import given, settings as hyp_settings
from hypothesis import strategies as st

from rac_control_plane.manifest.form_mapper import (
    FormAssetInput,
    FormSubmissionInput,
    build_manifest_from_form,
)
from rac_control_plane.manifest.parser import ManifestParseError, parse_manifest
from rac_control_plane.manifest.schema import (
    ExternalUrlAsset,
    SharedReferenceAsset,
    UploadAsset,
)

_SHA256 = "b" * 64
_URL = "https://example.com/weights.bin"


# ---------------------------------------------------------------------------
# Upload asset
# ---------------------------------------------------------------------------

def test_upload_asset_produces_upload_in_manifest() -> None:
    form = FormSubmissionInput(
        assets=[
            FormAssetInput(kind="upload", name="genome", mount_path="/mnt/ref/genome.fa"),
        ]
    )
    m = build_manifest_from_form(form)
    assert len(m.assets) == 1
    assert isinstance(m.assets[0], UploadAsset)
    assert m.assets[0].name == "genome"
    assert m.assets[0].mount_path == "/mnt/ref/genome.fa"
    assert m.assets[0].sha256 is None
    assert m.assets[0].size_bytes is None


def test_upload_asset_notes_preserved() -> None:
    form = FormSubmissionInput(
        assets=[
            FormAssetInput(
                kind="upload",
                name="data",
                mount_path="/mnt/data",
                notes="reference genome",
            )
        ]
    )
    m = build_manifest_from_form(form)
    assert m.assets[0].notes == "reference genome"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# External URL asset
# ---------------------------------------------------------------------------

def test_external_url_asset_with_both_fields_produces_manifest() -> None:
    form = FormSubmissionInput(
        assets=[
            FormAssetInput(
                kind="external_url",
                name="weights",
                mount_path="/mnt/model/weights.bin",
                declared_url=_URL,
                declared_sha256=_SHA256,
            )
        ]
    )
    m = build_manifest_from_form(form)
    assert len(m.assets) == 1
    asset = m.assets[0]
    assert isinstance(asset, ExternalUrlAsset)
    assert str(asset.url).startswith("https://example.com")
    assert asset.sha256 == _SHA256


def test_external_url_missing_sha256_raises() -> None:
    form = FormSubmissionInput(
        assets=[
            FormAssetInput(
                kind="external_url",
                name="weights",
                mount_path="/mnt/model/weights.bin",
                declared_url=_URL,
                declared_sha256=None,   # missing
            )
        ]
    )
    with pytest.raises(ManifestParseError) as exc_info:
        build_manifest_from_form(form)
    assert exc_info.value.code == "external_url_missing_fields"
    assert "weights" in exc_info.value.message


def test_external_url_missing_url_raises() -> None:
    form = FormSubmissionInput(
        assets=[
            FormAssetInput(
                kind="external_url",
                name="weights",
                mount_path="/mnt/model/weights.bin",
                declared_url=None,      # missing
                declared_sha256=_SHA256,
            )
        ]
    )
    with pytest.raises(ManifestParseError) as exc_info:
        build_manifest_from_form(form)
    assert exc_info.value.code == "external_url_missing_fields"


def test_external_url_invalid_sha256_raises_via_pydantic() -> None:
    """Short sha256 bubbles up through Pydantic as validation_error."""
    form = FormSubmissionInput(
        assets=[
            FormAssetInput(
                kind="external_url",
                name="weights",
                mount_path="/mnt/model/weights.bin",
                declared_url=_URL,
                declared_sha256="tooshort",
            )
        ]
    )
    with pytest.raises(ManifestParseError) as exc_info:
        build_manifest_from_form(form)
    assert exc_info.value.code == "validation_error"


# ---------------------------------------------------------------------------
# Shared reference passes through (rejected later at submission boundary)
# ---------------------------------------------------------------------------

def test_shared_reference_passes_build_manifest_from_form() -> None:
    """build_manifest_from_form accepts shared_reference; rejection is elsewhere."""
    form = FormSubmissionInput(
        assets=[
            FormAssetInput(
                kind="shared_reference",
                name="hg38",
                mount_path="/mnt/ref/hg38",
                catalog_id="cat-001",
            )
        ]
    )
    m = build_manifest_from_form(form)
    assert len(m.assets) == 1
    assert isinstance(m.assets[0], SharedReferenceAsset)
    assert m.assets[0].catalog_id == "cat-001"


# ---------------------------------------------------------------------------
# Duplicate validation (flows through Pydantic field validators)
# ---------------------------------------------------------------------------

def test_duplicate_names_raises() -> None:
    form = FormSubmissionInput(
        assets=[
            FormAssetInput(kind="upload", name="data", mount_path="/mnt/data1"),
            FormAssetInput(kind="upload", name="data", mount_path="/mnt/data2"),
        ]
    )
    with pytest.raises(ManifestParseError) as exc_info:
        build_manifest_from_form(form)
    assert exc_info.value.code == "validation_error"


def test_duplicate_mount_paths_raises() -> None:
    form = FormSubmissionInput(
        assets=[
            FormAssetInput(kind="upload", name="data1", mount_path="/mnt/data"),
            FormAssetInput(kind="upload", name="data2", mount_path="/mnt/data"),
        ]
    )
    with pytest.raises(ManifestParseError) as exc_info:
        build_manifest_from_form(form)
    assert exc_info.value.code == "validation_error"


# ---------------------------------------------------------------------------
# Default values match schema defaults
# ---------------------------------------------------------------------------

def test_default_values_match_schema() -> None:
    form = FormSubmissionInput()
    m = build_manifest_from_form(form)
    assert m.version == 1
    assert m.target_port == 8080
    assert m.cpu_cores == 0.5
    assert m.memory_gb == 1.0
    assert m.env_vars == {}
    assert m.assets == []


def test_env_vars_propagated() -> None:
    form = FormSubmissionInput(env_vars={"KEY": "value", "NUM": "42"})
    m = build_manifest_from_form(form)
    assert m.env_vars == {"KEY": "value", "NUM": "42"}


# ---------------------------------------------------------------------------
# AC8.5: form-generated == YAML-parsed for equivalent contents
# ---------------------------------------------------------------------------

def test_ac85_form_manifest_equals_yaml_parsed_manifest() -> None:
    """A form-generated manifest serialized to dict equals a YAML-parsed manifest
    with the same content. Downstream code cannot distinguish the source."""
    yaml_text = textwrap.dedent(f"""\
        version: 1
        assets:
          - kind: upload
            name: genome
            mount_path: /mnt/ref/genome.fa
          - kind: external_url
            name: weights
            mount_path: /mnt/model/weights.bin
            url: {_URL}
            sha256: {_SHA256}
        target_port: 9000
        cpu_cores: 1.0
        memory_gb: 2.0
        env_vars:
          FOO: bar
    """)
    yaml_manifest = parse_manifest(yaml_text)

    form = FormSubmissionInput(
        assets=[
            FormAssetInput(kind="upload", name="genome", mount_path="/mnt/ref/genome.fa"),
            FormAssetInput(
                kind="external_url",
                name="weights",
                mount_path="/mnt/model/weights.bin",
                declared_url=_URL,
                declared_sha256=_SHA256,
            ),
        ],
        target_port=9000,
        cpu_cores=1.0,
        memory_gb=2.0,
        env_vars={"FOO": "bar"},
    )
    form_manifest = build_manifest_from_form(form)

    # Both are ManifestV1 instances; compare via model_dump for full structural equality.
    # The url field serialises as a str in both paths via model_dump.
    assert yaml_manifest.model_dump() == form_manifest.model_dump()


# ---------------------------------------------------------------------------
# Round-trip: build → model_dump → manifest_from_dict → equal
# ---------------------------------------------------------------------------

def test_roundtrip_upload_form() -> None:
    from rac_control_plane.manifest.parser import manifest_from_dict

    form = FormSubmissionInput(
        assets=[FormAssetInput(kind="upload", name="data", mount_path="/mnt/data")],
        target_port=3000,
        cpu_cores=0.25,
        memory_gb=0.5,
        env_vars={"X": "1"},
    )
    original = build_manifest_from_form(form)
    restored = manifest_from_dict(original.model_dump())
    assert restored.model_dump() == original.model_dump()


# ---------------------------------------------------------------------------
# Property: arbitrary resource limits round-trip through form mapper
# ---------------------------------------------------------------------------

@given(
    target_port=st.integers(min_value=1, max_value=65535),
    cpu_cores=st.floats(min_value=0.25, max_value=2.0, allow_nan=False, allow_infinity=False),
    memory_gb=st.floats(min_value=0.5, max_value=8.0, allow_nan=False, allow_infinity=False),
)
@hyp_settings(max_examples=100)
def test_property_resource_limits_roundtrip(
    target_port: int,
    cpu_cores: float,
    memory_gb: float,
) -> None:
    """Any valid resource limits survive form → manifest → model_dump → manifest_from_dict."""
    from rac_control_plane.manifest.parser import manifest_from_dict

    form = FormSubmissionInput(
        target_port=target_port,
        cpu_cores=cpu_cores,
        memory_gb=memory_gb,
    )
    m = build_manifest_from_form(form)
    restored = manifest_from_dict(m.model_dump())
    assert restored.target_port == target_port
    assert abs(restored.cpu_cores - cpu_cores) < 1e-9
    assert abs(restored.memory_gb - memory_gb) < 1e-9
