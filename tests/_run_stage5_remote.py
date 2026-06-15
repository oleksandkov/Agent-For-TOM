"""Run only Stage 5, forcing the REMOTE HF LLM tier.

Strategy: monkey-patch ``_call_local_qwen_json_llm`` in the
synthesizer module to return ``(None, error_metrics)`` so the
synthesizer falls through to Tier 2 (remote HF) and produces a real
remote-driven synthesis.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, r"C:\Github\Agent-For-Labs")

# Monkey-patch BEFORE the synthesizer module imports _call_local_qwen_json_llm
from app.backend.llm import synthesizer as _synth

# Make Qwen look unavailable so we fall through to remote.
def _fake_local_qwen(prompt: str):
    return None, {
        "model": "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
        "source": "local_qwen",
        "raw_response": "",
        "error": "disabled for this run (force-remote mode)",
    }
_synth._call_local_qwen_json_llm = _fake_local_qwen

# Now run the pipeline.
from app.backend.pipeline.orchestrator import PipelineRunner
from app.backend.pipeline.utils import APP_DIR

session = sys.argv[1] if len(sys.argv) > 1 else "example_session"
transit_dir = APP_DIR / "debug" / "transit" / session
print(f"=== Compacting + Stage5 (remote-only) on: {transit_dir} ===")

runner = PipelineRunner(steps=("stage1", "stage2", "stage5"), use_qwen=True)
run = runner.run(transit_dir)
print()
for s in run.stages:
    print(f"[{s.name}] status={s.status} duration_ms={s.duration_ms:.1f}")
    for w in s.warnings:
        print(f"  WARN  {w}")
    for e in s.errors:
        print(f"  ERROR {e}")
    for k, v in (s.metrics or {}).items():
        print(f"  metric {k}={v}")

# Inspect main_out
mo = APP_DIR / "debug" / "main_out" / session
print()
print("--- main_out artifacts ---")
for p in mo.iterdir():
    print(f"  {p.name} ({p.stat().st_size} bytes)")

# Read raw response
raw = mo / "llm_raw_response.txt"
if raw.is_file():
    text = raw.read_text(encoding="utf-8")
    print()
    print(f"--- llm_raw_response.txt ({len(text)} chars) ---")
    print(text[:1500])
    if len(text) > 1500:
        print("... [truncated] ...")
