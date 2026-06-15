"""Unit tests for the database layer.

Each test uses a fresh in-memory or temp-file SQLite DB to avoid
interfering with the real app/db/agent.db.

Run with:
    & .venv/Scripts/python.exe -m unittest tests.test_database -v
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from app.backend.db.connection import Database
from app.backend.db.exceptions import PathValidationError
from app.backend.db.facade import BridgeRepository
from app.backend.db.path_utils import normalize, to_absolute
from app.backend.db.repositories.audit import AuditLogRepository
from app.backend.db.repositories.cache import CacheRepository
from app.backend.db.repositories.instructions import InstructionRepository
from app.backend.db.repositories.library_file import LibraryFileRepository
from app.backend.db.repositories.pipeline_runs import PipelineRunsRepository
from app.backend.db.repositories.sessions import SessionRepository
from app.backend.db.repositories.settings import AppSettingsRepository
from app.backend.db.repositories.secrets import SecretsRepository
from app.backend.db.repositories.templates import TemplateRepository
from app.backend.db.repositories.user_style import UserStyleRepository


class _TempDatabase(unittest.TestCase):
    """Base class that creates a fresh temp-file DB for each test."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        db_path = Path(self._tmpdir.name) / "test.db"
        # Direct sqlite3 connection without going through Database()
        # avoids the WAL/Lock singleton issue and keeps tests isolated.
        import sqlite3
        self._raw = sqlite3.connect(str(db_path), check_same_thread=False)
        self._raw.row_factory = sqlite3.Row
        self._raw.executescript(_ALL_SCHEMA_SQL())
        self._raw.commit()
        # Wrap into a Database for the repositories.
        # We bypass the real __init__ to avoid running migrations on
        # the temp DB (it already has the schema).
        from app.backend.db.connection import Database as _DB
        self.db = _DB.__new__(_DB)
        self.db._path = db_path
        self.db._lock = __import__("threading").Lock()
        self.db._conn = self._raw
        # Use TRUNCATE journal mode in tests so SQLite doesn't keep
        # the .db-wal file open between tests on Windows.
        self._raw.execute("PRAGMA journal_mode=TRUNCATE")
        self._raw.execute("PRAGMA foreign_keys=ON")
        self._raw.execute("PRAGMA busy_timeout=5000")
        # 004_seeds.sql already inserted lab1 + lab2 + default
        # settings as part of _ALL_SCHEMA_SQL() — that matches what
        # the real Database() would have after migration + seed.

    def tearDown(self) -> None:
        # Best-effort close before the tmpdir cleanup tries to remove
        # WAL/SHM sidecar files.
        try:
            self._raw.close()
        except Exception:
            pass

    def bridge(self) -> BridgeRepository:
        return BridgeRepository(self.db)


# Inline copy of the schema SQL for tests. Keeping a single source
# of truth in app/db/schema/*.sql is the goal; for tests we just
# concat them at import time. Done lazily on first test run.
_ALL_SCHEMA_SQL_CACHE: str | None = None


def _ALL_SCHEMA_SQL() -> str:
    global _ALL_SCHEMA_SQL_CACHE
    if _ALL_SCHEMA_SQL_CACHE is not None:
        return _ALL_SCHEMA_SQL_CACHE
    schema_dir = REPO / "app" / "db" / "schema"
    parts: list[str] = []
    for fname in sorted(schema_dir.glob("*.sql")):
        parts.append(fname.read_text(encoding="utf-8"))
    _ALL_SCHEMA_SQL_CACHE = "\n".join(parts)
    return _ALL_SCHEMA_SQL_CACHE


class TestPathUtils(unittest.TestCase):
    def test_normalize_posix(self):
        self.assertEqual(normalize("a/b/c"), "a/b/c")

    def test_normalize_windows_path(self):
        self.assertEqual(normalize(r"app\db\foo.db"), "app/db/foo.db")

    def test_normalize_rejects_absolute(self):
        with self.assertRaises(PathValidationError):
            normalize("C:/Windows/System32")
        with self.assertRaises(PathValidationError):
            normalize("/etc/passwd")

    def test_normalize_rejects_parent_traversal(self):
        with self.assertRaises(PathValidationError):
            normalize("../etc/passwd")
        with self.assertRaises(PathValidationError):
            normalize("a/../../b")

    def test_to_absolute(self):
        root = Path(tempfile.gettempdir())
        self.assertEqual(
            to_absolute("a/b.txt", root).as_posix(),
            (root / "a" / "b.txt").resolve().as_posix(),
        )


class TestSecrets(_TempDatabase):
    def test_hf_token_round_trip(self):
        bridge = self.bridge()
        self.assertIsNone(bridge.secrets.get_hf_token())
        bridge.secrets.set_hf_token("hf_TEST_abcdef123")
        self.assertEqual(bridge.secrets.get_hf_token(), "hf_TEST_abcdef123")
        # Check plaintext not in raw DB bytes
        raw = self.db.path.read_bytes()
        self.assertNotIn(b"hf_TEST_abcdef123", raw)

    def test_set_rejects_empty(self):
        bridge = self.bridge()
        with self.assertRaises(ValueError):
            bridge.secrets.set_hf_token("")

    def test_delete(self):
        bridge = self.bridge()
        bridge.secrets.set_hf_token("hf_X")
        bridge.secrets.delete("hf.token")
        self.assertFalse(bridge.secrets.has("hf.token"))


class TestLibraryDedup(_TempDatabase):
    def _write_file(self, name: str, content: bytes) -> Path:
        p = Path(self._tmpdir.name) / name
        p.write_bytes(content)
        return p

    def test_dedup_by_hash(self):
        bridge = self.bridge()
        f1 = self._write_file("a.pdf", b"hello world")
        f2 = self._write_file("b.pdf", b"hello world")  # same content
        r1 = bridge.library_file.attach(
            original_path=f1, original_name="a.pdf",
            original_type="application/pdf",
            stored_path="storage/library/aa/a.pdf",
        )
        r2 = bridge.library_file.attach(
            original_path=f2, original_name="b.pdf",
            original_type="application/pdf",
            stored_path="storage/library/bb/b.pdf",
        )
        self.assertEqual(r1["id"], r2["id"])  # SAME row returned
        # And there's only one row.
        rows = self.db.conn.execute("SELECT COUNT(*) AS n FROM library_file").fetchone()
        self.assertEqual(rows["n"], 1)

    def test_different_files_two_rows(self):
        bridge = self.bridge()
        f1 = self._write_file("a.pdf", b"AAA")
        f2 = self._write_file("b.pdf", b"BBB")
        r1 = bridge.library_file.attach(
            original_path=f1, original_name="a.pdf", original_type="application/pdf",
            stored_path="storage/library/aa/a.pdf",
        )
        r2 = bridge.library_file.attach(
            original_path=f2, original_name="b.pdf", original_type="application/pdf",
            stored_path="storage/library/bb/b.pdf",
        )
        self.assertNotEqual(r1["id"], r2["id"])

    def test_path_normalisation_rejected(self):
        bridge = self.bridge()
        with self.assertRaises(PathValidationError):
            bridge.library_file.attach(
                original_path=Path(self._tmpdir.name) / "x",
                original_name="x", stored_path="/etc/passwd",
            )


class TestInstructionsVersioning(_TempDatabase):
    def test_one_active_per_type(self):
        bridge = self.bridge()
        r1 = bridge.instructions.save_new_version(type_="global", content="v1")
        r2 = bridge.instructions.save_new_version(type_="global", content="v2")
        self.assertEqual(r2["content"], "v2")
        self.assertEqual(r2["is_active"], 1)
        # Old version is now inactive
        old = bridge.instructions.get(r1["id"])
        self.assertEqual(old["is_active"], 0)

    def test_special_instructions_per_template(self):
        bridge = self.bridge()
        r1 = bridge.instructions.save_new_version(
            type_="special", content="lab1 v1",
            template_id="00000000-0000-0000-0000-000000000001",
        )
        r2 = bridge.instructions.save_new_version(
            type_="special", content="lab2 v1",
            template_id="00000000-0000-0000-0000-000000000002",
        )
        self.assertNotEqual(r1["id"], r2["id"])
        # Each is independently active
        self.assertEqual(bridge.instructions.get_active("special", template_id="00000000-0000-0000-0000-000000000001")["content"], "lab1 v1")
        self.assertEqual(bridge.instructions.get_active("special", template_id="00000000-0000-0000-0000-000000000002")["content"], "lab2 v1")


class TestSessionLifecycle(_TempDatabase):
    def _lab1_id(self) -> str:
        """Look up the seeded lab1 template."""
        bridge = self.bridge()
        t = bridge.templates.get_by_name("lab1")
        assert t is not None, "lab1 should have been seeded by 004_seeds.sql"
        return t["id"]

    def test_create_complete(self):
        bridge = self.bridge()
        tid = self._lab1_id()
        s = bridge.sessions.create(
            template_id=tid, name="test",
            input_snapshot={"theme": "x"},
        )
        self.assertEqual(s["status"], "draft")
        bridge.sessions.set_started(s["id"])
        bridge.sessions.set_completed(
            s["id"], duration_ms=100, docx_path="out.docx", pdf_path="out.pdf",
        )
        got = bridge.sessions.get(s["id"])
        self.assertEqual(got["status"], "completed")
        self.assertEqual(got["duration_ms"], 100)
        self.assertEqual(got["docx_path"], "out.docx")

    def test_cascade_deletes_pipeline_runs(self):
        bridge = self.bridge()
        tid = self._lab1_id()
        s = bridge.sessions.create(template_id=tid, name="t", input_snapshot={})
        sid = s["id"]
        run_id = bridge.pipeline_runs.start(sid, "stage1")
        self.db.conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
        self.db.conn.commit()
        row = self.db.conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
        self.assertIsNone(row)


class TestCache(_TempDatabase):
    def test_llm_round_trip(self):
        bridge = self.bridge()
        bridge.cache.set_llm_response(
            cache_key="k1", template_id="lab1", template_name="lab1",
            params={"a": 1}, user_files_hash="h", style_hash="s",
            response_text="print('ok')", prompt_tokens=10, output_tokens=5,
        )
        hit = bridge.cache.get_llm_response("k1")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["response_text"], "print('ok')")

    def test_document_cache(self):
        bridge = self.bridge()
        bridge.cache.set_document(
            content_hash="doc1", docx_path="a.docx", pdf_path="a.pdf",
        )
        hit = bridge.cache.get_document("doc1")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["docx_path"], "a.docx")


class TestAudit(_TempDatabase):
    def test_audit_writes_row(self):
        bridge = self.bridge()
        bridge.audit.log(actor="cli", action="test.action", target_id="abc", details={"k": 1})
        recent = bridge.audit.list_recent(1)
        self.assertEqual(recent[0]["action"], "test.action")
        self.assertEqual(recent[0]["details"], {"k": 1})


class TestPipelineHardStop(_TempDatabase):
    """Verify the orchestrator's hard-stop behaviour: when gap_values
    have ai_accessible=true but no HF token, stage 5 must raise."""

    def test_hard_stop_without_any_llm(self):
        bridge = self.bridge()
        t = bridge.templates.get_by_name("lab1")
        assert t is not None
        s = bridge.sessions.create(
            template_id=t["id"], name="hard-stop-test",
            input_snapshot={
                "gap_values": {
                    "lab_number": {"value": "1", "ai_accessible": False},
                    "goal": {"value": "old", "ai_accessible": True},
                },
            },
        )
        # Make sure the test DB has no HF token.
        bridge.secrets.delete("hf.token")
        self.assertIsNone(bridge.secrets.get_hf_token())

        # Now stage5 should raise with a clear message (only when
        # both local Qwen and remote HF are unavailable).
        from app.backend.pipeline.stage5_output import _synthesize
        from app.backend.pipeline.types import PipelineContext
        from unittest.mock import patch
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            ctx = PipelineContext(
                session_id=s["id"], session_name="hard-stop",
                template_id="lab1", input_snapshot={},
                transit_dir=Path(td), compact_dir=Path(td),
                main_out_dir=Path(td), image_gen_dir=Path(td),
                output_dir=Path(td),
            )
            params_path = Path(td) / "lab1_params.json"
            params_path.write_text(json.dumps({
                "gap_values": {
                    "lab_number": {"value": "1", "ai_accessible": False},
                    "goal": {"value": "old", "ai_accessible": True},
                },
            }), encoding="utf-8")
            errors: list[str] = []
            # Force QwenRunner to look unavailable.
            with patch(
                "app.backend.compact.qwen_runner.QwenRunner",
                side_effect=Exception("Qwen not available in this env"),
            ):
                with self.assertRaises(RuntimeError) as cm:
                    _synthesize(Path(td), "lab1", {}, [], errors)
            self.assertIn("No LLM", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
