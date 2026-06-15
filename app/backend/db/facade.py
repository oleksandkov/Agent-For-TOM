"""app/backend/db/facade.py

Single entry point that gives the bridge / orchestrator access to
every repository at once. Construct one ``BridgeRepository`` per
``Database`` and keep it around for the lifetime of the app.
"""
from __future__ import annotations

from .connection import Database
from .repositories.audit import AuditLogRepository
from .repositories.cache import CacheRepository
from .repositories.custom_annotations import CustomAnnotationsRepository
from .repositories.instructions import InstructionRepository
from .repositories.library_file import LibraryFileRepository
from .repositories.pipeline_runs import PipelineRunsRepository
from .repositories.session_files import SessionFilesRepository
from .repositories.sessions import SessionRepository
from .repositories.settings import AppSettingsRepository
from .repositories.secrets import SecretsRepository
from .repositories.templates import TemplateRepository
from .repositories.user_style import UserStyleRepository
from .seed import seed_initial_data


class BridgeRepository:
    """All repositories in one place.

    Example:
        db = Database()
        bridge = BridgeRepository(db)
        bridge.sessions.list_recent(50)
        bridge.secrets.get_hf_token()
    """

    def __init__(self, db: Database) -> None:
        self._db = db
        self.templates = TemplateRepository(db)
        self.instructions = InstructionRepository(db)
        self.user_style = UserStyleRepository(db)
        self.app_settings = AppSettingsRepository(db)
        self.secrets = SecretsRepository(db)
        self.sessions = SessionRepository(db)
        self.session_files = SessionFilesRepository(db)
        self.library_file = LibraryFileRepository(db)
        self.custom_annotations = CustomAnnotationsRepository(db)
        self.pipeline_runs = PipelineRunsRepository(db)
        self.audit = AuditLogRepository(db)
        self.cache = CacheRepository(db)

    @property
    def db(self) -> Database:
        return self._db

    def seed(self) -> None:
        seed_initial_data(self._db)


__all__ = ["BridgeRepository"]
