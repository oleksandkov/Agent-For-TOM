"""SQLAlchemy 2 ORM models for TOM.

Tables map to backend-imp.md §"Таблиці БД":

- :class:`SessionORM`        — chat sessions
- :class:`MessageORM`        — messages within sessions
- :class:`MemoryRecordORM`   — archival memory (vector lives in vec0 sibling)
- :class:`SkillORM`          — MCP servers (built-in + user-approved generated)
- :class:`PatternORM`        — recurring clusters detected from archival
- :class:`ProviderConfigORM`— LLM provider config (no secrets)
- :class:`AuditLogORM`      — append-only audit trail

Embeddings: the raw float bytes live in ``memory_records.embedding_blob``
(BLOB). A separate vec0 virtual table for KNN search is created by the
initial Alembic migration (see ``db/migrations/versions/0001_initial.py``).
The memory layer (Section 4) keeps the two in sync at the repository
level; the models themselves are vector-agnostic.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, LargeBinary, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    """Declarative base for all TOM ORM models."""


def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    """Extract ``.value`` for SQLAlchemy ``Enum`` storage; required so we
    store the lowercase string ("archival") instead of the enum name
    ("ARCHIVAL") — matches the strings used in raw-SQL queries
    (e.g. archival-tier searches).
    """
    return [member.value for member in enum_cls]  # noqa: RUF100


class SessionStatus(enum.StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class SkillStatus(enum.StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    REJECTED = "rejected"


class PatternStatus(enum.StrEnum):
    PENDING = "pending"
    INBOX = "inbox"
    APPROVED = "approved"
    ARCHIVED = "archived"


class SessionORM(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    title: Mapped[str] = mapped_column(String, default="", nullable=False)
    provider: Mapped[str] = mapped_column(String, default="", nullable=False)
    model: Mapped[str] = mapped_column(String, default="", nullable=False)
    status: Mapped[SessionStatus] = mapped_column(
        Enum(
            SessionStatus,
            native_enum=False,
            length=16,
            values_callable=_enum_values,
        ),
        default=SessionStatus.OPEN,
        nullable=False,
    )
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )

    messages: Mapped[list[MessageORM]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="MessageORM.created_at",
    )


class MessageORM(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String, nullable=False)  # user|assistant|system|tool
    content: Mapped[str] = mapped_column(String, default="", nullable=False)
    tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tool_calls: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )

    session: Mapped[SessionORM] = relationship(back_populates="messages")


class MemoryTier(enum.StrEnum):
    CORE = "core"
    ARCHIVAL = "archival"
    RECALL = "recall"


class MemoryRecordORM(Base):
    __tablename__ = "memory_records"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    tier: Mapped[MemoryTier] = mapped_column(
        Enum(
            MemoryTier,
            native_enum=False,
            length=16,
            values_callable=_enum_values,
        ),
        default=MemoryTier.ARCHIVAL,
        nullable=False,
        index=True,
    )
    content: Mapped[str] = mapped_column(String, default="", nullable=False)
    embedding_blob: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    embedding_dim: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    confidence: Mapped[float] = mapped_column(default=1.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )


class SkillORM(Base):
    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    version: Mapped[str] = mapped_column(String, default="0.0.1", nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    source: Mapped[str] = mapped_column(
        String, default="builtin", nullable=False
    )  # builtin|generated
    status: Mapped[SkillStatus] = mapped_column(
        Enum(
            SkillStatus,
            native_enum=False,
            length=16,
            values_callable=_enum_values,
        ),
        default=SkillStatus.ACTIVE,
        nullable=False,
        index=True,
    )
    invocation_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_invocation_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PatternORM(Base):
    __tablename__ = "patterns"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    centroid_blob: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    sample_session_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    occurrences: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[PatternStatus] = mapped_column(
        Enum(
            PatternStatus,
            native_enum=False,
            length=16,
            values_callable=_enum_values,
        ),
        default=PatternStatus.PENDING,
        nullable=False,
        index=True,
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )
    draft_skill_id: Mapped[str | None] = mapped_column(
        ForeignKey("skills.id", ondelete="SET NULL"), nullable=True
    )


class ProviderConfigORM(Base):
    __tablename__ = "provider_configs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    type: Mapped[str] = mapped_column(
        String, nullable=False
    )  # ollama|openai|anthropic|google|custom
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    base_url: Mapped[str | None] = mapped_column(String, nullable=True)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    fallback_chain: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    is_default: Mapped[bool] = mapped_column(default=False, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )


class AuditLogORM(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
    actor: Mapped[str] = mapped_column(String, default="system", nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    target_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


__all__: list[str] = [
    "AuditLogORM",
    "Base",
    "MemoryRecordORM",
    "MemoryTier",
    "MessageORM",
    "PatternORM",
    "PatternStatus",
    "ProviderConfigORM",
    "SessionORM",
    "SessionStatus",
    "SkillORM",
    "SkillStatus",
]
