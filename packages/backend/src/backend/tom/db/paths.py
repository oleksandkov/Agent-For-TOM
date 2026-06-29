"""Cross-platform data directory resolution for TOM.

The data dir holds:
- ``tom.db``              — encrypted SQLite database (sqlcipher3 + vec0)
- ``keyring.id``          — UUID identifying which OS-keyring slot holds the DB key
- ``core_memory.json``    — user-editable structured memory (Section 4)
- ``uploads/``            — user-uploaded attachments (Section 6)
- ``skills/builtin/``     — packaged MCP servers (Section 7)
- ``skills/generated/``   — auto-generated MCP servers (Section 10)
- ``logs/``               — runtime logs

Resolution order:
1. ``TOM_DATA_DIR`` env var (testing, sandbox, portable mode)
2. Platform default: ``%APPDATA%\\tom`` / ``~/Library/Application Support/tom`` / ``~/.local/share/tom``

All paths are created lazily and idempotently by :func:`ensure_dirs`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_DEFAULT_DIR_NAME = "tom"
_ENV_OVERRIDE = "TOM_DATA_DIR"


def data_dir() -> Path:
    """Return the absolute TOM data directory for the current user/platform.

    Honours ``TOM_DATA_DIR`` if set; otherwise chooses the platform default.
    The directory is *not* created here — call :func:`ensure_dirs` for that.
    """
    override = os.environ.get(_ENV_OVERRIDE)
    if override:
        return Path(override).expanduser().resolve()
    return _platform_default().resolve()


def _platform_default() -> Path:
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / _DEFAULT_DIR_NAME
        return Path.home() / "AppData" / "Roaming" / _DEFAULT_DIR_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / _DEFAULT_DIR_NAME
    return Path.home() / ".local" / "share" / _DEFAULT_DIR_NAME


def db_file() -> Path:
    """Path to the encrypted DB file (``tom.db``)."""
    return data_dir() / "tom.db"


def keyring_id_file() -> Path:
    """Path to the ``keyring.id`` tracker (UUID only, never the key)."""
    return data_dir() / "keyring.id"


def core_memory_file() -> Path:
    """Path to the user-editable ``core_memory.json`` (Section 4)."""
    return data_dir() / "core_memory.json"


def uploads_dir() -> Path:
    """Path to the user uploads directory."""
    return data_dir() / "uploads"


def skills_builtin_dir() -> Path:
    """Path to the packaged MCP servers directory (Section 7)."""
    return data_dir() / "skills" / "builtin"


def skills_generated_dir() -> Path:
    """Path to the auto-generated MCP servers directory (Section 10).

    The sandbox (Section 10.C) restricts writes to this directory only.
    """
    return data_dir() / "skills" / "generated"


def logs_dir() -> Path:
    """Path to the runtime logs directory."""
    return data_dir() / "logs"


def ensure_dirs() -> list[Path]:
    """Create every TOM data subdirectory if missing. Idempotent.

    Returns the list of directories that exist after the call.
    Safe to invoke repeatedly; missing parent dirs are created.
    """
    targets = [
        data_dir(),
        uploads_dir(),
        skills_builtin_dir(),
        skills_generated_dir(),
        logs_dir(),
    ]
    for path in targets:
        path.mkdir(parents=True, exist_ok=True)
    return targets


__all__: list[str] = [
    "core_memory_file",
    "data_dir",
    "db_file",
    "ensure_dirs",
    "keyring_id_file",
    "logs_dir",
    "skills_builtin_dir",
    "skills_generated_dir",
    "uploads_dir",
]
