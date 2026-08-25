"""
TOMAS's own version — manually bumped, not derived from git.

Git-based versioning was considered and rejected: a real install's tree
excludes `.git` entirely (see install.ps1's exclude list), so `git log` has
nothing to read there, and even in a dev checkout its commit date can disagree
with what is actually on disk whenever there are uncommitted changes. A small
constant bumped on each meaningful change is the one source that is always
right for the build actually running. The date-based scheme matches how this
project already dates its own commits ("17.08", "16.08", ...) rather than
introducing semver for a self-hosted, single-user tool.
"""

from __future__ import annotations

VERSION = "2026.08.25"
LAST_UPDATED = "2026-08-25"


def git_info() -> tuple[str, str] | None:
    """(short hash, commit date) when this is a dev checkout with `.git`
    present, else None. Supplementary only — never the primary version
    source, since it is absent from every real install.
    """
    import subprocess
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent
    if not (repo_root / ".git").exists():
        return None
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%h %cs"],
            cwd=repo_root, capture_output=True, text=True, timeout=3,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return None
        commit_hash, _, commit_date = out.stdout.strip().partition(" ")
        return commit_hash, commit_date
    except Exception:
        return None
