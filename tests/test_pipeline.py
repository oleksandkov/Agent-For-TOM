"""tests/test_pipeline.py — smoke + integration tests for the backend pipeline.

These tests use the frozen example session under
``app/debug/transit/example_session/`` as their fixture. They verify
that each pipeline stage, in isolation, produces the right artifacts
and that the orchestrator runs end-to-end.

Run with:
    & .venv/Scripts/python.exe -m pytest tests/ -v
or:
    & .venv/Scripts/python.exe -m unittest tests.test_pipeline -v
"""
from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.backend.llm.gap_assembler import render_filled_py
from app.backend.pipeline.orchestrator import PipelineRunner
from app.backend.pipeline.types import (
    PipelineContext,
    STAGE_FAIL,
    STAGE_OK,
    STAGE_WARN,
)
from app.backend.pipeline.utils import (
    COMPACT_DIR,
    COMPACTION_TARGET_TOKENS,
    MAIN_OUT_DIR,
    OUTPUT_DIR,
    TRANSIT_DIR,
    count_tokens,
    load_env,
    session_paths,
)


EXAMPLE_SESSION_ID = "example_session"
EXAMPLE_TRANSIT = TRANSIT_DIR / EXAMPLE_SESSION_ID


def _is_healthy_filled_py(path: Path) -> list[str]:
    """Return a list of human-readable problems with a filled.py (empty = ok)."""
    if not path.is_file():
        return [f"missing: {path}"]
    text = path.read_text(encoding="utf-8")
    problems: list[str] = []
    if "[Вставте" in text or "[ВСТАВТЕ" in text or "[вставте" in text:
        problems.append("unfilled placeholder remains in filled.py")
    if "def create_docx" not in text:
        problems.append("missing create_docx function")
    if "def create_pdf" not in text:
        problems.append("missing create_pdf function")
    try:
        import ast
        ast.parse(text)
    except SyntaxError as exc:
        problems.append(f"syntax error: {exc}")
    return problems


class TestUtils(unittest.TestCase):
    def test_count_tokens(self):
        self.assertEqual(count_tokens(""), 0)
        self.assertEqual(count_tokens("one two three"), 3)
        self.assertEqual(count_tokens("  a\tb\nc  "), 3)

    def test_load_env_idempotent(self):
        load_env()  # must not raise
        load_env()  # calling twice is safe

    def test_session_paths_returns_all_five(self):
        paths = session_paths("xyz")
        self.assertEqual(set(paths.keys()), {
            "transit", "compact", "main_out", "image_gen", "output",
        })
        self.assertTrue(paths["compact"].name == "xyz")


class TestGapAssembler(unittest.TestCase):
    """Tests for the local fallback filled.py synthesizer."""

    def test_renders_with_minimal_gap_values(self):
        out = render_filled_py("lab1", {})
        self.assertIn("def create_docx", out["filled_py"])
        self.assertIn("def create_pdf", out["filled_py"])
        self.assertEqual(out["out_docx"], "Lab_Template_Final.docx")
        self.assertEqual(out["out_pdf"], "Lab_Template_Final.pdf")

    def test_renders_lab2_with_correct_filenames(self):
        out = render_filled_py("lab2", {})
        self.assertEqual(out["out_docx"], "Lab_Template_Lab2_Style.docx")
        self.assertEqual(out["out_pdf"], "Lab_Template_Lab2_Style.pdf")

    def test_full_pipeline_runs(self):
        for stage_dir in [
            COMPACT_DIR / EXAMPLE_SESSION_ID,
            MAIN_OUT_DIR / EXAMPLE_SESSION_ID,
            OUTPUT_DIR / EXAMPLE_SESSION_ID,
        ]:
            if stage_dir.is_dir():
                shutil.rmtree(stage_dir)
        from unittest.mock import patch
        from app.backend.llm import synthesizer as synth_mod
        mocked_json = (
            '{"goal": "test goal", "general_info": "' + 'x' * 60 +
            '", "tasks": ["t1"], "control_questions": ["q1"], "bibliography": ["b1"]}'
        )
        with patch.object(synth_mod, "get_hf_token", return_value="hf_FAKE_TOKEN_FOR_TESTING"), \
             patch.object(synth_mod, "get_groq_api_key", return_value=None), \
             patch.object(synth_mod, "_call_local_qwen_json_llm",
                           return_value=(None, {"error": "mocked: no Qwen"})), \
             patch.object(synth_mod, "_call_remote_json_llm",
                           return_value=(json.loads(mocked_json),
                                        {"model": "fake", "source": "remote",
                                         "prompt_tokens": 100, "output_tokens": 200})):
            runner = PipelineRunner(use_qwen=False)
            run = runner.run(EXAMPLE_TRANSIT)
        self.assertTrue(run.is_ok, f"stages failed: {[s.to_dict() for s in run.stages]}")
        problems = _is_healthy_filled_py(
            OUTPUT_DIR / EXAMPLE_SESSION_ID / "filled.py"
        )
        self.assertEqual(problems, [], f"filled.py problems: {problems}")



class TestStage1(unittest.TestCase):
    def test_validates_example_session(self):
        if not EXAMPLE_TRANSIT.is_dir():
            self.skipTest(f"example transit snapshot missing: {EXAMPLE_TRANSIT}")
        ctx = PipelineContext(
            session_id=EXAMPLE_SESSION_ID,
            session_name=EXAMPLE_SESSION_ID,
            template_id="lab1",
            input_snapshot={},
            transit_dir=EXAMPLE_TRANSIT,
            compact_dir=COMPACT_DIR / EXAMPLE_SESSION_ID,
            main_out_dir=MAIN_OUT_DIR / EXAMPLE_SESSION_ID,
            image_gen_dir=TRANSIT_DIR.parent / "image-gen" / EXAMPLE_SESSION_ID,
            output_dir=OUTPUT_DIR / EXAMPLE_SESSION_ID,
        )
        from app.backend.pipeline.stage1_transit import run_stage1
        result = run_stage1(ctx)
        self.assertNotEqual(result.status, STAGE_FAIL, msg=str(result.errors))
        self.assertIn("session_context", result.artifacts)
        self.assertGreaterEqual(result.metrics.get("attached_files", 0), 1)


    def test_stage1_ignores_missing_special_instructions_if_toggled_off(self):
        # Create a temp session dir with special instructions disabled and file missing
        temp_session = TRANSIT_DIR / "temp_special_off"
        if temp_session.is_dir():
            shutil.rmtree(temp_session)
        temp_session.mkdir(parents=True, exist_ok=True)
        try:
            # Copy example files
            for f in ["context.json", "library_files.json"]:
                shutil.copy2(EXAMPLE_TRANSIT / f, temp_session / f)
            # Write session context with include_special_instructions: False
            sc = {
                "id": "temp_special_off",
                "template_id": "lab1",
                "status": "processing",
                "input_snapshot": {
                    "template_id": "lab1",
                    "include_special_instructions": False
                }
            }
            (temp_session / "session_context.json").write_text(json.dumps(sc), encoding="utf-8")
            
            ctx = PipelineContext(
                session_id="temp_special_off",
                session_name="temp_special_off",
                template_id="lab1",
                input_snapshot=sc["input_snapshot"],
                transit_dir=temp_session,
                compact_dir=COMPACT_DIR / "temp_special_off",
                main_out_dir=MAIN_OUT_DIR / "temp_special_off",
                image_gen_dir=TRANSIT_DIR.parent / "image-gen" / "temp_special_off",
                output_dir=OUTPUT_DIR / "temp_special_off",
            )
            from app.backend.pipeline.stage1_transit import run_stage1
            result = run_stage1(ctx)
            self.assertNotEqual(result.status, STAGE_FAIL)
            # Verify no warning about missing instructions
            warnings = [w for w in result.warnings if "_fill.md" in w or "Template instructions" in w]
            self.assertEqual(warnings, [])
        finally:
            if temp_session.is_dir():
                shutil.rmtree(temp_session)


class TestStage2(unittest.TestCase):
    def test_compacts_attached_file(self):
        if not EXAMPLE_TRANSIT.is_dir():
            self.skipTest(f"example transit snapshot missing: {EXAMPLE_TRANSIT}")
        ctx = PipelineContext(
            session_id=EXAMPLE_SESSION_ID,
            session_name=EXAMPLE_SESSION_ID,
            template_id="lab1",
            input_snapshot={},
            transit_dir=EXAMPLE_TRANSIT,
            compact_dir=COMPACT_DIR / EXAMPLE_SESSION_ID,
            main_out_dir=MAIN_OUT_DIR / EXAMPLE_SESSION_ID,
            image_gen_dir=TRANSIT_DIR.parent / "image-gen" / EXAMPLE_SESSION_ID,
            output_dir=OUTPUT_DIR / EXAMPLE_SESSION_ID,
        )
        from app.backend.pipeline.stage2_compact import run_stage2
        result = run_stage2(ctx, use_qwen=False)  # force heuristic
        self.assertNotEqual(result.status, STAGE_FAIL, msg=str(result.errors))
        attached_dst = Path(result.artifacts.get("attached_dir", ""))
        self.assertTrue(attached_dst.is_dir(), f"missing: {attached_dst}")
        txts = list(attached_dst.glob("*.txt"))
        self.assertGreaterEqual(len(txts), 1, "no compacted attached files")

    def test_stage2_ignores_user_style_if_toggled_off(self):
        temp_session = TRANSIT_DIR / "temp_style_off"
        temp_compact = COMPACT_DIR / "temp_style_off"
        if temp_session.is_dir():
            shutil.rmtree(temp_session)
        if temp_compact.is_dir():
            shutil.rmtree(temp_compact)
        temp_session.mkdir(parents=True, exist_ok=True)
        try:
            # Copy all files including user_style.md
            for f in ["context.json", "library_files.json", "lab1_params.json"]:
                shutil.copy2(EXAMPLE_TRANSIT / f, temp_session / f)
            # Write a user_style.md
            (temp_session / "user_style.md").write_text("Custom user style text", encoding="utf-8")
            # Write session context with include_user_style: False
            sc = {
                "id": "temp_style_off",
                "template_id": "lab1",
                "status": "processing",
                "input_snapshot": {
                    "template_id": "lab1",
                    "include_user_style": False
                }
            }
            (temp_session / "session_context.json").write_text(json.dumps(sc), encoding="utf-8")
            
            ctx = PipelineContext(
                session_id="temp_style_off",
                session_name="temp_style_off",
                template_id="lab1",
                input_snapshot=sc["input_snapshot"],
                transit_dir=temp_session,
                compact_dir=temp_compact,
                main_out_dir=MAIN_OUT_DIR / "temp_style_off",
                image_gen_dir=TRANSIT_DIR.parent / "image-gen" / "temp_style_off",
                output_dir=OUTPUT_DIR / "temp_style_off",
            )
            from app.backend.pipeline.stage2_compact import run_stage2
            result = run_stage2(ctx, use_qwen=False)
            self.assertNotEqual(result.status, STAGE_FAIL, msg=str(result.errors))
            # Verify user_style.md was NOT copied/compacted
            self.assertFalse((temp_compact / "user_style.md").is_file())
            self.assertNotIn("user_style", result.artifacts)
        finally:
            if temp_session.is_dir():
                shutil.rmtree(temp_session)
            if temp_compact.is_dir():
                shutil.rmtree(temp_compact)


class TestOrchestrator(unittest.TestCase):
    """End-to-end test on the example session."""

    @classmethod
    def setUpClass(cls):
        if not EXAMPLE_TRANSIT.is_dir():
            raise unittest.SkipTest(
                f"example transit snapshot missing: {EXAMPLE_TRANSIT}"
            )

    def setUp(self):
        # Make sure no test token lingers in the DB from a prior run.
        from app.backend.db.connection import Database
        from app.backend.db.repositories.secrets import SecretsRepository
        try:
            db = Database()
            SecretsRepository(db).delete("hf.token")
            db.close()
        except Exception:
            pass
        # Also clear the env-based fallbacks so the synthesizer
        # really sees "no token".
        import os
        for key in (
            "HUGGY_FACE_TOKEN", "HUGGING_FACE_TOKEN", "HF_TOKEN",
            "GOOGLE_API_KEY", "GOOGLE_GEMINI_API_KEY",
            "OPENROUTER_API_KEY", "OPENROUTER_KEY",
            "GROQ_API_KEY", "GROQ_TOKEN", "REMOTE_LLM_PROVIDER"
        ):
            os.environ.pop(key, None)
        os.environ["SUPPORT_ATTACH_FILES"] = "true"


    def test_full_pipeline_hard_stops_with_no_llm_at_all(self):
        # No local Qwen AND no remote token (HF or Groq) → Stage 5
        # must HARD-STOP. (Local Qwen is now PRIMARY, so the
        # hard-stop requires both to be absent.)
        for stage_dir in [
            COMPACT_DIR / EXAMPLE_SESSION_ID,
            MAIN_OUT_DIR / EXAMPLE_SESSION_ID,
            OUTPUT_DIR / EXAMPLE_SESSION_ID,
        ]:
            if stage_dir.is_dir():
                shutil.rmtree(stage_dir)
        from unittest.mock import patch
        
        # Patch the API key getters, load_env, and QwenRunner to simulate no LLM access at all
        with patch("app.backend.pipeline.utils.get_gemini_api_key", return_value=None), \
             patch("app.backend.pipeline.utils.get_openrouter_api_key", return_value=None), \
             patch("app.backend.pipeline.utils.get_groq_api_key", return_value=None), \
             patch("app.backend.pipeline.utils.get_hf_token", return_value=None), \
             patch("app.backend.llm.providers.get_gemini_api_key", return_value=None), \
             patch("app.backend.llm.providers.get_openrouter_api_key", return_value=None), \
             patch("app.backend.llm.providers.get_groq_api_key", return_value=None), \
             patch("app.backend.llm.synthesizer.get_hf_token", return_value=None), \
             patch("app.backend.llm.synthesizer.get_groq_api_key", return_value=None), \
             patch("app.backend.pipeline.utils.load_env", return_value={}), \
             patch("app.backend.llm.synthesizer.load_env", return_value=None), \
             patch("app.backend.compact.qwen_runner.QwenRunner", side_effect=Exception("no qwen")):
            runner = PipelineRunner(use_qwen=False)
            run = runner.run(EXAMPLE_TRANSIT)
        self.assertFalse(run.is_ok)
        last = run.stages[-1]
        self.assertEqual(last.name, "stage5_output")
        self.assertEqual(last.status, "fail")
        self.assertTrue(
            any("No LLM" in e for e in last.errors),
            f"expected hard-stop error, got: {last.errors}",
        )


    def test_full_pipeline_with_fake_token_uses_remote_fallback(self):
        # Force the local Qwen to fail, leaving remote as the only
        # available path. We patch the remote LLM call to return
        # valid JSON so the pipeline completes.
        from app.backend.db.connection import Database
        from app.backend.db.repositories.secrets import SecretsRepository
        from unittest.mock import patch
        from app.backend.llm import synthesizer as synth_mod
        db = Database()
        SecretsRepository(db).set_hf_token("hf_FAKE_TOKEN_FOR_TESTING")
        db.close()
        # Wipe Groq env so the test exercises the HF path, not Groq.
        import os
        for key in ("GROQ_API_KEY", "GROQ_TOKEN", "REMOTE_LLM_PROVIDER"):
            os.environ.pop(key, None)
        try:
            for stage_dir in [
                COMPACT_DIR / EXAMPLE_SESSION_ID,
                MAIN_OUT_DIR / EXAMPLE_SESSION_ID,
                OUTPUT_DIR / EXAMPLE_SESSION_ID,
            ]:
                if stage_dir.is_dir():
                    shutil.rmtree(stage_dir)
            mocked_json = (
                '{"goal": "test goal", "general_info": "' + 'x' * 60 +
                '", "tasks": ["t1"], "control_questions": ["q1"], "bibliography": ["b1"]}'
            )
            with patch.object(synth_mod, "get_hf_token", return_value="hf_FAKE_TOKEN_FOR_TESTING"), \
                 patch.object(synth_mod, "get_groq_api_key", return_value=None), \
                 patch.object(synth_mod, "_call_local_qwen_json_llm",
                              return_value=(None, {"error": "mocked: no Qwen"})), \
                 patch.object(synth_mod, "_call_remote_json_llm",
                              return_value=(json.loads(mocked_json),
                                           {"model": "fake", "source": "remote",
                                            "prompt_tokens": 100, "output_tokens": 200})):
                runner = PipelineRunner(use_qwen=False)
                run = runner.run(EXAMPLE_TRANSIT)
            self.assertTrue(run.is_ok, f"stages failed: {[s.to_dict() for s in run.stages]}")
            self.assertEqual(run.index.get("status"), "completed")
            artifacts = run.index.get("artifacts", {})
            self.assertTrue(
                artifacts.get("docx", "").endswith(".docx"),
                f"bad docx: {artifacts.get('docx')}",
            )
            self.assertTrue(
                artifacts.get("pdf", "").endswith(".pdf"),
                f"bad pdf: {artifacts.get('pdf')}",
            )
            problems = _is_healthy_filled_py(
                OUTPUT_DIR / EXAMPLE_SESSION_ID / "filled.py"
            )
            self.assertEqual(problems, [], f"filled.py problems: {problems}")
        finally:
            db = Database()
            SecretsRepository(db).delete("hf.token")
            db.close()


if __name__ == "__main__":
    unittest.main()
