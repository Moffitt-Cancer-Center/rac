# pattern: Functional Core
"""rac_control_plane.manifest.schema — Pydantic v2 models for rac.yaml.

Contains validators (sha256 format, unique asset names, unique mount paths)
that run on every model instantiation. Functional Core: validators are pure
functions of the input data with no I/O.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

_HEX64_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class UploadAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["upload"] = "upload"
    name: str
    mount_path: str
    sha256: str | None = None       # populated server-side after upload completes
    size_bytes: int | None = None   # populated server-side
    notes: str | None = None


class ExternalUrlAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["external_url"] = "external_url"
    name: str
    mount_path: str
    url: HttpUrl
    sha256: str                     # declared by researcher; verified at fetch time
    size_bytes: int | None = None
    notes: str | None = None

    @field_validator("sha256")
    @classmethod
    def sha256_must_be_64_hex(cls, v: str) -> str:
        if not _HEX64_RE.match(v):
            raise ValueError(
                f"sha256 must be exactly 64 hex characters, got {len(v)} characters: {v!r}"
            )
        return v.lower()


class SharedReferenceAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["shared_reference"] = "shared_reference"
    name: str
    mount_path: str
    catalog_id: str     # accepted in schema; rejected at submission time (AC8.6)


Asset = Annotated[
    UploadAsset | ExternalUrlAsset | SharedReferenceAsset,
    Field(discriminator="kind"),
]


class ManifestV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    assets: list[Asset] = Field(default_factory=list)
    target_port: int = Field(default=8080, ge=1, le=65535)
    cpu_cores: float = Field(default=0.5, ge=0.25, le=2.0)
    memory_gb: float = Field(default=1.0, ge=0.5, le=8.0)
    env_vars: dict[str, str] = Field(default_factory=dict)

    @field_validator("assets")
    @classmethod
    def no_duplicate_names(cls, v: list[Asset]) -> list[Asset]:
        names = [a.name for a in v]
        if len(set(names)) != len(names):
            raise ValueError("asset names must be unique within a manifest")
        return v

    @field_validator("assets")
    @classmethod
    def no_duplicate_mount_paths(cls, v: list[Asset]) -> list[Asset]:
        paths = [a.mount_path for a in v]
        if len(set(paths)) != len(paths):
            raise ValueError("mount_path values must be unique within a manifest")
        return v
