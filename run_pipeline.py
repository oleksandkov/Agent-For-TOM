"""run_pipeline.py — CLI entry point for the Agent-For-TOM pipeline.

Usage:
    python run_pipeline.py <transit_session_id>
    python run_pipeline.py --transit-dir <path>
    python run_pipeline.py --all        # run every session under app/debug/transit/

Examples:
    python run_pipeline.py example_session
    python run_pipeline.py --transit-dir app/debug/transit/example_session
    python run_pipeline.py --all --no-qwen
"""
from __future__ import annotations

import argparse
import json
import sys
import subprocess
from pathlib import Path

# Auto-re-execute using the virtual environment python if not already running in it.
# This prevents ModuleNotFoundError if the user runs the script using the system python.
venv_python = Path(__file__).resolve().parent / ".venv" / "Scripts" / "python.exe"
if not venv_python.exists():
    venv_python = Path(__file__).resolve().parent / ".venv" / "bin" / "python"

venv_dir = Path(__file__).resolve().parent / ".venv"
if venv_python.exists() and Path(sys.prefix).resolve() != venv_dir.resolve():
    cmd = [str(venv_python)] + sys.argv
    sys.exit(subprocess.run(cmd).returncode)

# Make `app` importable when run from the repo root.
REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.backend.pipeline import (
    PipelineRunner,
    TRANSIT_DIR,
    get_hf_token,
    load_env,
)


def _print_step(step: str, result) -> None:
    parts = [
        f"[{step}]",
        f"status={result.status}",
        f"duration_ms={result.duration_ms:.1f}",
    ]
    if result.warnings:
        parts.append(f"warnings={len(result.warnings)}")
    if result.errors:
        parts.append(f"errors={len(result.errors)}")
    print(" ".join(parts))
    for w in result.warnings:
        print(f"  WARN  {w}")
    for e in result.errors:
        print(f"  ERROR {e}")


def _resolve_sessions(args: argparse.Namespace) -> list[Path]:
    if args.transit_dir:
        p = Path(args.transit_dir).resolve()
        if not p.is_dir():
            print(f"ERROR: --transit-dir not found: {p}", file=sys.stderr)
            sys.exit(2)
        return [p]
    if args.all:
        if not TRANSIT_DIR.is_dir():
            print(f"ERROR: transit dir missing: {TRANSIT_DIR}", file=sys.stderr)
            sys.exit(2)
        return sorted(p for p in TRANSIT_DIR.iterdir() if p.is_dir())
    if args.session_id:
        p = (TRANSIT_DIR / args.session_id).resolve()
        if not p.is_dir():
            print(f"ERROR: transit session not found: {p}", file=sys.stderr)
            sys.exit(2)
        return [p]
    print("ERROR: provide a session id, --transit-dir, or --all", file=sys.stderr)
    sys.exit(2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the Agent-For-TOM pipeline on a transit snapshot.",
    )
    parser.add_argument("session_id", nargs="?", help="Session id under app/debug/transit/")
    parser.add_argument("--transit-dir", help="Explicit transit directory path")
    parser.add_argument("--all", action="store_true", help="Run every session under app/debug/transit/")
    parser.add_argument("--no-qwen", action="store_true", help="Skip Qwen local compaction (force heuristic fallback)")
    parser.add_argument("--json", action="store_true", help="Print the full index.json payload at the end")
    args = parser.parse_args(argv)

    load_env()
    from app.backend.pipeline.utils import get_gemini_api_key, get_openrouter_api_key, get_groq_api_key
    has_remote = bool(get_gemini_api_key() or get_openrouter_api_key() or get_groq_api_key())
    if not has_remote:
        print(
            "NOTE: No remote LLM API key (GOOGLE_API_KEY, OPENROUTER_API_KEY, or GROQ_API_KEY) "
            "is set in .env — pipeline will use the local gap_values fallback.",
            file=sys.stderr,
        )

    sessions = _resolve_sessions(args)
    overall_rc = 0
    for transit in sessions:
        print(f"\n=== {transit} ===")
        runner = PipelineRunner(use_qwen=not args.no_qwen)
        run = runner.run(transit, on_step=_print_step)
        if run.index and run.is_ok:
            artifacts = run.index.get("artifacts", {})
            print(
                f"status: {run.index.get('status')!r}  source_mode: {run.index.get('source_mode')!r}\n"
                f"docx:   {artifacts.get('docx') or '(missing)'}\n"
                f"pdf:    {artifacts.get('pdf') or '(missing)'}\n"
                f"warnings: {len(run.index.get('warnings', []))}\n"
                f"errors:   {len(run.index.get('errors', []))}"
            )
        elif not run.is_ok:
            # Show the most recent failure reason.
            last = run.stages[-1] if run.stages else None
            if last is not None and last.errors:
                for e in last.errors:
                    print(f"FAIL: {e}")
            print(
                f"\nlast stage: {last.name if last else '?'} status={last.status if last else '?'} "
                f"errors={len(last.errors) if last else 0}"
            )
        else:
            print("Pipeline completed without writing index.json (unexpected).")
        if not run.is_ok:
            overall_rc = 1
        if args.json:
            print(json.dumps(run.to_dict(), ensure_ascii=False, indent=2))
    return overall_rc


if __name__ == "__main__":
    raise SystemExit(main())
