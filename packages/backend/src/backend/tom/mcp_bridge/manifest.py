"""MCP server manifest (Section 7).

Each MCP server ships a ``manifest.json`` next to its entry point.
The Pydantic model here is the runtime source of truth; the JSON
Schema at ``schemas/mcp-manifest.schema.json`` is the cross-team
contract and is generated from this model via :func:`manifest_json_schema`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_TOOL_TIMEOUT_SECONDS = 30
ALLOWED_CAPABILITIES: tuple[Literal["tools", "resources", "prompts"], ...] = (
    "tools",
    "resources",
    "prompts",
)

_SCHEMA_PATH = Path(__file__).resolve().parents[4] / "schemas" / "mcp-manifest.schema.json"
if not _SCHEMA_PATH.exists():
    # Fallback for repo layout changes; loader checks _SCHEMA_PATH.exists()
    _SCHEMA_PATH = Path(__file__).with_name("mcp-manifest.schema.json")


class ManifestError(ValueError):
    """Raised when a manifest is malformed."""


class EntrypointModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = Field(..., min_length=1)
    args: list[str] = Field(..., min_length=1)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None


class McpServerManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    version: str
    description: str = ""
    entrypoint: EntrypointModel
    capabilities: list[str] = Field(..., min_length=1)
    permissions: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    writable_paths: list[str] = Field(default_factory=list)
    tool_timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS

    @field_validator("capabilities")
    @classmethod
    def _validate_capabilities(cls, v: list[str]) -> list[str]:
        bad = [c for c in v if c not in ALLOWED_CAPABILITIES]
        if bad:
            msg = f"unknown capabilities: {bad}"
            raise ValueError(msg)
        return list(v)

    @field_validator("version")
    @classmethod
    def _validate_version(cls, v: str) -> str:
        if not v or not v[0].isdigit():
            msg = "version must start with a digit"
            raise ValueError(msg)
        return v


@dataclass
class McpServerManifest:
    """Plain dataclass mirroring :class:`McpServerManifestModel`."""

    name: str
    version: str
    description: str
    entrypoint: EntrypointModel
    capabilities: list[str]
    permissions: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    writable_paths: list[str] = field(default_factory=list)
    tool_timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "entrypoint": self.entrypoint.model_dump(),
            "capabilities": list(self.capabilities),
            "permissions": list(self.permissions),
            "risk_flags": list(self.risk_flags),
            "writable_paths": list(self.writable_paths),
            "tool_timeout_seconds": self.tool_timeout_seconds,
        }


def manifest_json_schema() -> dict[str, Any]:
    """Return the JSON Schema derived from :class:`McpServerManifestModel`."""
    return McpServerManifestModel.model_json_schema()


def load_manifest(
    path: str | Path,
    *,
    on_disk_json: Mapping[str, Any] | None = None,
) -> McpServerManifest:
    """Parse and validate ``manifest.json``.

    ``on_disk_json`` is an injection seam for tests — when provided,
    the file is not read. Always validates against the Pydantic model
    (which is structurally equivalent to the JSON Schema on disk).
    """
    if on_disk_json is None:
        path = Path(path)
        if not path.exists():
            msg = f"manifest not found: {path}"
            raise ManifestError(msg)
        try:
            on_disk_json = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            msg = f"cannot read manifest {path}: {exc}"
            raise ManifestError(msg) from exc
    try:
        model = McpServerManifestModel.model_validate(on_disk_json)
    except Exception as exc:
        msg = f"manifest at {path} is invalid: {exc}"
        raise ManifestError(msg) from exc
    return McpServerManifest(
        name=model.name,
        version=model.version,
        description=model.description,
        entrypoint=model.entrypoint,
        capabilities=list(model.capabilities),
        permissions=list(model.permissions),
        risk_flags=list(model.risk_flags),
        writable_paths=list(model.writable_paths),
        tool_timeout_seconds=model.tool_timeout_seconds,
    )


def on_disk_json_schema() -> dict[str, Any]:
    """Read the JSON Schema from ``schemas/mcp-manifest.schema.json``."""
    if not _SCHEMA_PATH.exists():
        msg = f"schema not found on disk: {_SCHEMA_PATH}"
        raise FileNotFoundError(msg)
    raw = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    return dict(raw)


__all__: list[str] = [
    "ALLOWED_CAPABILITIES",
    "DEFAULT_TOOL_TIMEOUT_SECONDS",
    "EntrypointModel",
    "ManifestError",
    "McpServerManifest",
    "McpServerManifestModel",
    "load_manifest",
    "manifest_json_schema",
    "on_disk_json_schema",
]
