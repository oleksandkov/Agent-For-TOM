"""Tests for :mod:`backend.tom.mcp_bridge.manifest`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.tom.mcp_bridge.manifest import (
    DEFAULT_TOOL_TIMEOUT_SECONDS,
    ManifestError,
    McpServerManifestModel,
    load_manifest,
    manifest_json_schema,
    on_disk_json_schema,
)


def _minimal() -> dict:
    return {
        "name": "tom-fmt",
        "version": "0.1.0",
        "description": "text formatting tools",
        "entrypoint": {"command": "python", "args": ["-m", "tom_fmt.server"]},
        "capabilities": ["tools"],
    }


def test_minimal_manifest_loads() -> None:
    m = load_manifest("<inline>", on_disk_json=_minimal())
    assert m.name == "tom-fmt"
    assert m.version == "0.1.0"
    assert m.tool_timeout_seconds == DEFAULT_TOOL_TIMEOUT_SECONDS
    assert m.entrypoint.command == "python"


def test_invalid_name_rejected() -> None:
    data = _minimal() | {"name": "Tom_FMT"}
    with pytest.raises(ManifestError):
        load_manifest("<inline>", on_disk_json=data)


def test_unknown_capability_rejected() -> None:
    data = _minimal() | {"capabilities": ["tools", "weather"]}
    with pytest.raises(ManifestError):
        load_manifest("<inline>", on_disk_json=data)


def test_extra_fields_rejected() -> None:
    data = _minimal() | {"weird_field": "oops"}
    with pytest.raises(ManifestError):
        load_manifest("<inline>", on_disk_json=data)


def test_missing_entrypoint_rejected() -> None:
    data = _minimal()
    data["entrypoint"] = {"command": "python"}
    with pytest.raises(ManifestError):
        load_manifest("<inline>", on_disk_json=data)


def test_to_dict_round_trip() -> None:
    m = load_manifest("<inline>", on_disk_json=_minimal())
    out = m.to_dict()
    assert out["name"] == "tom-fmt"
    # Make sure manifest validates back from its own to_dict()
    m2 = load_manifest("<inline>", on_disk_json=out)
    assert m2 == m


def test_json_schema_matches_pydantic() -> None:
    pyd = McpServerManifestModel.model_json_schema()
    on_disk = on_disk_json_schema()
    # Both require the same essential keys
    pyd_required = set(pyd.get("required", []))
    on_disk_required = set(on_disk.get("required", []))
    assert {"name", "version", "entrypoint", "capabilities"} <= pyd_required
    assert {"name", "version", "entrypoint", "capabilities"} <= on_disk_required


def test_manifest_json_schema_callable() -> None:
    schema = manifest_json_schema()
    assert "properties" in schema
    assert "name" in schema["properties"]


def test_load_manifest_from_real_file(tmp_path: Path) -> None:
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(_minimal()), encoding="utf-8")
    m = load_manifest(p)
    assert m.name == "tom-fmt"


def test_load_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ManifestError):
        load_manifest(tmp_path / "no.json")
