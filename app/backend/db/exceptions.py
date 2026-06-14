"""app/backend/db/exceptions.py

Custom exception hierarchy for the database layer.
"""
from __future__ import annotations


class DbError(RuntimeError):
    """Generic database error. Wrap sqlite3.* when surfacing to the UI."""


class NotFoundError(DbError):
    """Raised when a get_or_404 / list_by_id lookup returns nothing."""


class VersionConflictError(DbError):
    """Raised when concurrent edits touch the same row / version."""


class PathValidationError(DbError):
    """Raised when a path passed to a repository is not a safe relative path."""


class SecretKeyMissingError(DbError):
    """Raised when the Fernet key file is missing (secrets cannot be decrypted)."""
