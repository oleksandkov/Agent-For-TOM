"""initial schema: 7 tables + vec0 index on memory embeddings

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-29 00:00:00.000000

Creates the full TOM schema. Mirrors :mod:`backend.tom.db.models`.

Embedding storage: each :class:`MemoryRecordORM` row keeps its raw float
bytes in ``memory_records.embedding_blob`` (BLOB). The vec0 virtual
table ``memory_records_vec`` mirrors those rows so the memory layer
(Section 4) can issue KNN queries without scanning full rows.

The vec0 dimension is fixed at migration time. Changing it later requires
a new migration that drops + recreates ``memory_records_vec``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIM = 384


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("title", sa.String, nullable=False, server_default=""),
        sa.Column("provider", sa.String, nullable=False, server_default=""),
        sa.Column("model", sa.String, nullable=False, server_default=""),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="open",
        ),
        sa.Column("total_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("session_id", sa.String, nullable=False),
        sa.Column("role", sa.String, nullable=False),
        sa.Column("content", sa.String, nullable=False, server_default=""),
        sa.Column("tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tool_calls", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_messages_session_id", "messages", ["session_id"])
    op.create_index("ix_messages_created_at", "messages", ["created_at"])

    op.create_table(
        "memory_records",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("tier", sa.String(length=16), nullable=False, server_default="archival"),
        sa.Column("content", sa.String, nullable=False, server_default=""),
        sa.Column("embedding_blob", sa.LargeBinary, nullable=True),
        sa.Column("embedding_dim", sa.Integer, nullable=True),
        sa.Column("source_session_id", sa.String, nullable=True),
        sa.Column("confidence", sa.Float, nullable=False, server_default="1.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_session_id"], ["sessions.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_memory_records_tier", "memory_records", ["tier"])
    op.create_index("ix_memory_records_source_session_id", "memory_records", ["source_session_id"])
    op.create_index("ix_memory_records_created_at", "memory_records", ["created_at"])

    op.create_table(
        "skills",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("name", sa.String, nullable=False, unique=True),
        sa.Column("version", sa.String, nullable=False, server_default="0.0.1"),
        sa.Column("manifest", sa.JSON, nullable=False),
        sa.Column("source", sa.String, nullable=False, server_default="builtin"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("invocation_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_invocation_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_skills_status", "skills", ["status"])

    op.create_table(
        "patterns",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("centroid_blob", sa.LargeBinary, nullable=True),
        sa.Column("sample_session_ids", sa.JSON, nullable=False),
        sa.Column("occurrences", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("draft_skill_id", sa.String, nullable=True),
        sa.ForeignKeyConstraint(["draft_skill_id"], ["skills.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_patterns_status", "patterns", ["status"])

    op.create_table(
        "provider_configs",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("type", sa.String, nullable=False),
        sa.Column("name", sa.String, nullable=False, unique=True),
        sa.Column("base_url", sa.String, nullable=True),
        sa.Column("model", sa.String, nullable=True),
        sa.Column("fallback_chain", sa.JSON, nullable=False),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.text("0")),
        sa.Column("config", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String, nullable=False, server_default="system"),
        sa.Column("action", sa.String, nullable=False),
        sa.Column("target_type", sa.String, nullable=True),
        sa.Column("target_id", sa.String, nullable=True),
        sa.Column("payload", sa.JSON, nullable=False),
    )
    op.create_index("ix_audit_log_ts", "audit_log", ["ts"])
    op.create_index("ix_audit_log_action", "audit_log", ["action"])
    op.create_index("ix_audit_log_target_type", "audit_log", ["target_type"])
    op.create_index("ix_audit_log_target_id", "audit_log", ["target_id"])

    op.execute(
        f"""
        CREATE VIRTUAL TABLE memory_records_vec USING vec0(
            memory_id TEXT PRIMARY KEY,
            embedding float[{EMBEDDING_DIM}]
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS memory_records_vec")
    op.drop_table("audit_log")
    op.drop_table("provider_configs")
    op.drop_table("patterns")
    op.drop_table("skills")
    op.drop_table("memory_records")
    op.drop_table("messages")
    op.drop_table("sessions")
