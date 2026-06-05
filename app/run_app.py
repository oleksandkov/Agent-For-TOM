"""
Launcher for the Agent-For-TOM desktop GUI (CustomTkinter).

Run from the repo root:

    python app/run_app.py

Opens a native window with a metadata panel, AI settings, a preset
selector (Лабораторна робота / Методичні рекомендації), a dynamic form
that adapts to the chosen preset, and a Generate button. Output files
(PDF + DOCX) are saved wherever the user picks.
"""
import os
import sys

# Make the repo root importable so `app.gui.lab_app` resolves when the
# user runs `python app/run_app.py` from either the repo root or from
# inside the `app/` directory.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from app.gui.lab_app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
