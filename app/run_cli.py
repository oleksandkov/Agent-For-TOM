"""
Launcher for the Agent-For-TOM interactive CLI.

Run from the repo root:

    python app/run_cli.py
    python app/run_cli.py --yes
    python app/run_cli.py --api-key hf_xxxxxxxxxxxx
    python app/run_cli.py --no-ai

The CLI lets a single user (typically a Ukrainian university teacher) fill
in a few prompts about a future lab work and produces both a PDF and a
DOCX in `app/output/`, formatted to ДСТУ 3008:2015.
"""
import os
import sys

# Make the repo root importable so `app.cli.lab_generator` resolves when
# the user runs `python app/run_cli.py` from the repo root.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from app.cli.lab_generator import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())

