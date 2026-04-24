"""Tests for manifest/schema.py and manifest/parser.py.

Verifies: rac-v1.AC8.5 (schema), rac-v1.AC8.6 (shared_reference rejection).

Coverage:
- Valid manifests parse correctly (0-asset, mixed-asset).
- Duplicate names/mount_paths raise ManifestParseError.
- version: 2 is rejected.
- Bad YAML syntax raises ManifestParseError(code="yaml_syntax_error").
- Unknown top-level keys are rejected (extra="forbid").
- ExternalUrlAsset.sha256 must be 64 hex chars.
- shared_reference passes parse, fails reject_shared_references (AC8.6).
- Property: parse → model_dump → manifest_from_dict round-trip is identity.
"""

from __future__ import annotations

import textwrap
from typing import Any

import pytest
from hypothesis import given, settings as hyp_settings
from hypothesis import strategies as st

from rac_control_plane.manifest.parser import (
    ManifestParseError,
    SharedReferenceNotYetSupportedError,
    manifest_from_dict,
    parse_manifest,
    reject_shared_references,
)
from rac_control_plane.manifest.schema import (
    ExternalUrlAsset,
    ManifestV1,
    SharedReferenceAsset,
    UploadAsset,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_VALID_SHA256 = "a" * 64
_VALID_URL = "https://example.com/data.tar.gz"

_MINIMAL_YAML = "version: 1\n"

_UPLOAD_YAML = textwrap.dedent("""\
    version: 1
    assets:
      - kind: upload
        name: genome
        mount_path: /mnt/ref/genome.fa
    target_port: 8080
    cpu_cores: 0.5
    memory_gb: 1.0
""")

_EXTERNAL_YAML = textwrap.dedent(f"""\
    version: 1
    assets:
      - kind: external_url
        name: weights
        mount_path: /mnt/model/weights.bin
        url: {_VALID_URL}
        sha256: {_VALID_SHA256}
""")

_MIXED_YAML = textwrap.dedent(f"""\
    version: 1
    assets:
      - kind: upload
        name: genome
        mount_path: /mnt/ref/genome.fa
      - kind: external_url
        name: weights
        mount_path: /mnt/model/weights.bin
        url: {_VALID_URL}
        sha256: {_VALID_SHA256}
""")

_SHARED_REF_YAML = textwrap.dedent("""\
    version: 1
    assets:
      - kind: shared_reference
        name: hg38
        mount_path: /mnt/ref/hg38
        catalog_id: cat-001
""")


# ---------------------------------------------------------------------------
# Zero-asset manifest
# ---------------------------------------------------------------------------

def test_zero_asset_manifest_parses() -> None:
    m = parse_manifest(_MINIMAL_YAML)
    assert m.version == 1
    assert m.assets == []
    assert m.target_port == 8080
    assert m.cpu_cores == 0.5
    assert m.memory_gb == 1.0
    assert m.env_vars == {}


# ---------------------------------------------------------------------------
# Upload asset
# ---------------------------------------------------------------------------

def test_upload_asset_parses() -> None:
    m = parse_manifest(_UPLOAD_YAML)
    assert len(m.assets) == 1
    asset = m.assets[0]
    assert isinstance(asset, UploadAsset)
    assert asset.name == "genome"
    assert asset.mount_path == "/mnt/ref/genome.fa"
    assert asset.sha256 is None
    assert asset.size_bytes is None


# ---------------------------------------------------------------------------
# External URL asset
# ---------------------------------------------------------------------------

def test_external_url_asset_parses() -> None:
    m = parse_manifest(_EXTERNAL_YAML)
    assert len(m.assets) == 1
    asset = m.assets[0]
    assert isinstance(asset, ExternalUrlAsset)
    assert asset.name == "weights"
    assert str(asset.url).startswith("https://example.com")
    assert asset.sha256 == _VALID_SHA256


def test_external_url_sha256_must_be_64_hex() -> None:
    bad_sha_yaml = textwrap.dedent(f"""\
        version: 1
        assets:
          - kind: external_url
            name: weights
            mount_path: /mnt/model/weights.bin
            url: {_VALID_URL}
            sha256: tooshort
    """)
    with pytest.raises(ManifestParseError) as exc_info:
        parse_manifest(bad_sha_yaml)
    assert exc_info.value.code == "validation_error"


def test_external_url_sha256_must_be_hex_chars() -> None:
    """64 characters but not hex → rejected."""
    non_hex = "z" * 64
    yaml_text = textwrap.dedent(f"""\
        version: 1
        assets:
          - kind: external_url
            name: weights
            mount_path: /mnt/model/weights.bin
            url: {_VALID_URL}
            sha256: {non_hex}
    """)
    with pytest.raises(ManifestParseError) as exc_info:
        parse_manifest(yaml_text)
    assert exc_info.value.code == "validation_error"


def test_external_url_sha256_normalised_to_lowercase() -> None:
    mixed_case_sha = "A" * 64
    yaml_text = textwrap.dedent(f"""\
        version: 1
        assets:
          - kind: external_url
            name: weights
            mount_path: /mnt/model/weights.bin
            url: {_VALID_URL}
            sha256: {mixed_case_sha}
    """)
    m = parse_manifest(yaml_text)
    asset = m.assets[0]
    assert isinstance(asset, ExternalUrlAsset)
    assert asset.sha256 == "a" * 64


# ---------------------------------------------------------------------------
# Mixed assets
# ---------------------------------------------------------------------------

def test_mixed_upload_external_url_parses() -> None:
    m = parse_manifest(_MIXED_YAML)
    assert len(m.assets) == 2
    kinds = {type(a).__name__ for a in m.assets}
    assert kinds == {"UploadAsset", "ExternalUrlAsset"}


# ---------------------------------------------------------------------------
# Duplicate validation
# ---------------------------------------------------------------------------

def test_duplicate_asset_names_raises() -> None:
    yaml_text = textwrap.dedent("""\
        version: 1
        assets:
          - kind: upload
            name: data
            mount_path: /mnt/data1
          - kind: upload
            name: data
            mount_path: /mnt/data2
    """)
    with pytest.raises(ManifestParseError) as exc_info:
        parse_manifest(yaml_text)
    assert exc_info.value.code == "validation_error"
    assert "unique" in exc_info.value.message.lower() or any(
        "unique" in d["msg"].lower() for d in exc_info.value.details
    )


def test_duplicate_mount_paths_raises() -> None:
    yaml_text = textwrap.dedent("""\
        version: 1
        assets:
          - kind: upload
            name: data1
            mount_path: /mnt/data
          - kind: upload
            name: data2
            mount_path: /mnt/data
    """)
    with pytest.raises(ManifestParseError) as exc_info:
        parse_manifest(yaml_text)
    assert exc_info.value.code == "validation_error"
    assert "unique" in exc_info.value.message.lower() or any(
        "unique" in d["msg"].lower() for d in exc_info.value.details
    )


# ---------------------------------------------------------------------------
# Version validation
# ---------------------------------------------------------------------------

def test_version_2_rejected() -> None:
    yaml_text = "version: 2\n"
    with pytest.raises(ManifestParseError) as exc_info:
        parse_manifest(yaml_text)
    assert exc_info.value.code == "validation_error"


# ---------------------------------------------------------------------------
# Extra fields forbidden
# ---------------------------------------------------------------------------

def test_unknown_top_level_key_rejected() -> None:
    yaml_text = "version: 1\nunknown_field: true\n"
    with pytest.raises(ManifestParseError) as exc_info:
        parse_manifest(yaml_text)
    assert exc_info.value.code == "validation_error"


# ---------------------------------------------------------------------------
# YAML syntax error
# ---------------------------------------------------------------------------

def test_yaml_syntax_error_raises_manifest_parse_error() -> None:
    bad_yaml = "version: 1\nassets: [\n  - broken"
    with pytest.raises(ManifestParseError) as exc_info:
        parse_manifest(bad_yaml)
    assert exc_info.value.code == "yaml_syntax_error"


def test_yaml_not_a_mapping_raises() -> None:
    yaml_text = "- item1\n- item2\n"
    with pytest.raises(ManifestParseError) as exc_info:
        parse_manifest(yaml_text)
    assert exc_info.value.code == "yaml_syntax_error"


# ---------------------------------------------------------------------------
# Shared reference: AC8.6
# ---------------------------------------------------------------------------

def test_shared_reference_passes_parse() -> None:
    """schema accepts shared_reference; only reject_shared_references blocks it."""
    m = parse_manifest(_SHARED_REF_YAML)
    assert len(m.assets) == 1
    assert isinstance(m.assets[0], SharedReferenceAsset)
    assert m.assets[0].name == "hg38"


def test_reject_shared_references_raises_with_entry_name() -> None:
    """AC8.6: reject_shared_references raises with the specific asset name."""
    m = parse_manifest(_SHARED_REF_YAML)
    with pytest.raises(SharedReferenceNotYetSupportedError) as exc_info:
        reject_shared_references(m)
    err = exc_info.value
    assert err.entry_name == "hg38"
    assert "hg38" in err.message
    assert "coming soon" in err.message
    assert err.code == "shared_reference_not_supported"


def test_reject_shared_references_first_entry_named() -> None:
    """When multiple shared_reference assets exist, the first one is named."""
    yaml_text = textwrap.dedent("""\
        version: 1
        assets:
          - kind: shared_reference
            name: ref-alpha
            mount_path: /mnt/a
            catalog_id: cat-001
          - kind: shared_reference
            name: ref-beta
            mount_path: /mnt/b
            catalog_id: cat-002
    """)
    m = parse_manifest(yaml_text)
    with pytest.raises(SharedReferenceNotYetSupportedError) as exc_info:
        reject_shared_references(m)
    assert exc_info.value.entry_name == "ref-alpha"


def test_reject_shared_references_no_shared_ref_returns_manifest() -> None:
    """Manifests without shared_reference pass through unchanged."""
    m = parse_manifest(_MIXED_YAML)
    result = reject_shared_references(m)
    assert result is m


# ---------------------------------------------------------------------------
# Resource limits
# ---------------------------------------------------------------------------

def test_target_port_out_of_range_rejected() -> None:
    yaml_text = "version: 1\ntarget_port: 99999\n"
    with pytest.raises(ManifestParseError) as exc_info:
        parse_manifest(yaml_text)
    assert exc_info.value.code == "validation_error"


def test_cpu_cores_out_of_range_rejected() -> None:
    yaml_text = "version: 1\ncpu_cores: 5.0\n"
    with pytest.raises(ManifestParseError):
        parse_manifest(yaml_text)


def test_memory_gb_out_of_range_rejected() -> None:
    yaml_text = "version: 1\nmemory_gb: 0.1\n"
    with pytest.raises(ManifestParseError):
        parse_manifest(yaml_text)


# ---------------------------------------------------------------------------
# env_vars
# ---------------------------------------------------------------------------

def test_env_vars_parsed() -> None:
    yaml_text = "version: 1\nenv_vars:\n  FOO: bar\n  BAZ: '42'\n"
    m = parse_manifest(yaml_text)
    assert m.env_vars == {"FOO": "bar", "BAZ": "42"}


# ---------------------------------------------------------------------------
# manifest_from_dict
# ---------------------------------------------------------------------------

def test_manifest_from_dict_validates_same_as_parse() -> None:
    d: dict[str, Any] = {
        "version": 1,
        "assets": [
            {"kind": "upload", "name": "data", "mount_path": "/mnt/data"},
        ],
    }
    m = manifest_from_dict(d)
    assert isinstance(m.assets[0], UploadAsset)


def test_manifest_from_dict_raises_manifest_parse_error_on_bad_input() -> None:
    with pytest.raises(ManifestParseError) as exc_info:
        manifest_from_dict({"version": 99})
    assert exc_info.value.code == "validation_error"
    assert len(exc_info.value.details) > 0


# ---------------------------------------------------------------------------
# Property: round-trip identity (AC8.5)
# ---------------------------------------------------------------------------

_upload_asset_st = st.fixed_dictionaries({
    "kind": st.just("upload"),
    "name": st.text(min_size=1, max_size=20, alphabet="abcdefghijklmnopqrstuvwxyz0123456789-"),
    "mount_path": st.just("/mnt/data"),
})


@given(
    target_port=st.integers(min_value=1, max_value=65535),
    cpu_cores=st.floats(min_value=0.25, max_value=2.0, allow_nan=False, allow_infinity=False),
    memory_gb=st.floats(min_value=0.5, max_value=8.0, allow_nan=False, allow_infinity=False),
)
@hyp_settings(max_examples=100)
def test_property_roundtrip_no_assets(
    target_port: int,
    cpu_cores: float,
    memory_gb: float,
) -> None:
    """parse → model_dump → manifest_from_dict round-trips with no assets."""
    original = manifest_from_dict({
        "version": 1,
        "target_port": target_port,
        "cpu_cores": cpu_cores,
        "memory_gb": memory_gb,
    })
    dumped = original.model_dump()
    restored = manifest_from_dict(dumped)
    assert restored.version == original.version
    assert restored.target_port == original.target_port
    assert abs(restored.cpu_cores - original.cpu_cores) < 1e-9
    assert abs(restored.memory_gb - original.memory_gb) < 1e-9
    assert restored.assets == original.assets
    assert restored.env_vars == original.env_vars


@given(
    name=st.text(min_size=1, max_size=20, alphabet="abcdefghijklmnopqrstuvwxyz"),
    mount_path=st.just("/mnt/data"),
)
@hyp_settings(max_examples=50)
def test_property_roundtrip_upload_asset(name: str, mount_path: str) -> None:
    """Upload asset survives model_dump → manifest_from_dict round-trip."""
    original = manifest_from_dict({
        "version": 1,
        "assets": [{"kind": "upload", "name": name, "mount_path": mount_path}],
    })
    dumped = original.model_dump()
    # model_dump serialises HttpUrl as str; manifest_from_dict must handle both
    restored = manifest_from_dict(dumped)
    assert len(restored.assets) == 1
    asset = restored.assets[0]
    assert isinstance(asset, UploadAsset)
    assert asset.name == name
    assert asset.mount_path == mount_path
