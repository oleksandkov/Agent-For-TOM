"""Tests for the FastAPI router in :mod:`backend.tom.memory.api`."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.tom.db.init_db import init_db
from backend.tom.memory.api import router, set_memory
from backend.tom.memory.tom_memory import TomMemory


@pytest.fixture
def client(virtual_keyring: object, tmp_path: Path) -> TestClient:
    init_db()
    set_memory(TomMemory())
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_get_core_default(client: TestClient) -> None:
    r = client.get("/v1/memory/core")
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == 0
    assert isinstance(body["blocks"], list)


def test_patch_core_applies_and_bumps(client: TestClient) -> None:
    initial = client.get("/v1/memory/core").json()
    r = client.patch(
        "/v1/memory/core",
        json={
            "expected_version": initial["version"],
            "add_facts": ["likes coffee"],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == initial["version"] + 1
    assert "likes coffee" in body["facts"]


def test_patch_core_set_blocks(client: TestClient) -> None:
    initial = client.get("/v1/memory/core").json()
    r = client.patch(
        "/v1/memory/core",
        json={
            "expected_version": initial["version"],
            "set_blocks": [
                {"label": "persona", "text": "updated"},
                {"label": "human", "text": "researcher"},
            ],
        },
    )
    assert r.status_code == 200, r.text
    blocks = r.json()["blocks"]
    labels = {b["label"] for b in blocks}
    assert labels == {"persona", "human"}


def test_patch_core_409_on_version_conflict(client: TestClient) -> None:
    initial = client.get("/v1/memory/core").json()
    r1 = client.patch(
        "/v1/memory/core",
        json={"expected_version": initial["version"], "add_facts": ["first"]},
    )
    assert r1.status_code == 200
    # Second patch with the *original* version must fail.
    r2 = client.patch(
        "/v1/memory/core",
        json={"expected_version": initial["version"], "add_facts": ["second"]},
    )
    assert r2.status_code == 409
    detail = r2.json()["detail"]
    assert detail["reason"] == "version_mismatch"
    assert detail["current_version"] == initial["version"] + 1


def test_patch_core_422_on_bad_payload(client: TestClient) -> None:
    r = client.patch("/v1/memory/core", json={"add_facts": ["x"]})  # no expected_version
    assert r.status_code == 422


def test_patch_core_blocks_set_blocks_payload_validated(client: TestClient) -> None:
    initial = client.get("/v1/memory/core").json()
    r = client.patch(
        "/v1/memory/core",
        json={
            "expected_version": initial["version"],
            "set_blocks": [{"label": "persona", "unexpected": "field"}],
        },
    )
    assert r.status_code == 422
