"""app/backend/db/path_utils.py

Path normalisation for database-stored paths.

All paths that go into the DB MUST be:
  * relative to the repository root (never absolute)
  * use forward slashes (POSIX form) regardless of host OS
  * contain no `..` segments (defence against accidental directory
    traversal)

Functions:
  - normalize(path: str) -> str           for writing
  - to_absolute(rel: str, root: Path)    for reading (resolves against the repo root)
"""
from __future__ import annotations

import os
from pathlib import Path

from .exceptions import PathValidationError


def normalize(path: str | os.PathLike[str]) -> str:
    """Validate and normalise a path for DB storage.

    Raises:
        PathValidationError: if the path is absolute, contains '..',
            or is otherwise unsafe.
    """
    if path is None:
        raise PathValidationError("path is None")
    p = Path(path).as_posix()
    if not p:
        raise PathValidationError("path is empty")
    if Path(p).is_absolute() or p.startswith(("/", "\\")):
        raise PathValidationError(f"DB paths must be relative, got absolute: {p!r}")
    if ".." in Path(p).parts:
        raise PathValidationError(f"DB paths must not contain '..': {p!r}")
    return p


def to_absolute(rel: str, root: Path) -> Path:
    """Resolve a normalised relative path against a root directory."""
    if not rel:
        raise PathValidationError("relative path is empty")
    return (root / rel).resolve()
