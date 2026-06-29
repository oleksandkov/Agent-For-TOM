"""ORM model roundtrip tests against a real SQLCipher DB."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.tom.db.engine import make_engine
from backend.tom.db.init_db import init_db
from backend.tom.db.models import (
    AuditLogORM,
    MemoryRecordORM,
    MemoryTier,
    MessageORM,
    PatternORM,
    PatternStatus,
    ProviderConfigORM,
    SessionORM,
    SessionStatus,
    SkillORM,
    SkillStatus,
)
from backend.tom.db.session import SessionLocal


def _build_session() -> SessionORM:
    sess = SessionORM(title="hello", provider="ollama", model="qwen2:1.5b")
    return sess


def test_session_roundtrip(virtual_keyring: object, tmp_path: Path) -> None:
    init_db()
    s = SessionLocal()
    try:
        sess = _build_session()
        s.add(sess)
        s.flush()
        # status defaults to OPEN
        assert sess.status is SessionStatus.OPEN
        assert sess.id  # UUID assigned
        assert sess.created_at <= datetime.now(sess.created_at.tzinfo)
        s.commit()
        loaded = s.get(SessionORM, sess.id)
        assert loaded is not None
        assert loaded.title == "hello"
        assert loaded.provider == "ollama"
    finally:
        s.close()


def test_message_cascades_with_session(virtual_keyring: object, tmp_path: Path) -> None:
    init_db()
    s = SessionLocal()
    try:
        sess = _build_session()
        s.add(sess)
        s.flush()
        msg = MessageORM(
            session_id=sess.id,
            role="user",
            content="hi",
            tokens=2,
        )
        s.add(msg)
        s.commit()
        # delete session -> message cascades
        s.delete(sess)
        s.commit()
        remaining = (
            s.execute(select(MessageORM).where(MessageORM.session_id == sess.id)).scalars().all()
        )
        assert remaining == []
    finally:
        s.close()


def test_memory_record_embedding_blob(virtual_keyring: object, tmp_path: Path) -> None:
    init_db()
    s = SessionLocal()
    try:
        rec = MemoryRecordORM(
            tier=MemoryTier.ARCHIVAL,
            content="a memory",
            embedding_blob=b"\x00\x01\x02\x03",
            embedding_dim=4,
            confidence=0.9,
        )
        s.add(rec)
        s.commit()
        loaded = s.get(MemoryRecordORM, rec.id)
        assert loaded is not None
        assert loaded.embedding_blob == b"\x00\x01\x02\x03"
        assert loaded.embedding_dim == 4
        assert loaded.tier is MemoryTier.ARCHIVAL
    finally:
        s.close()


def test_skill_default_active(virtual_keyring: object, tmp_path: Path) -> None:
    init_db()
    s = SessionLocal()
    try:
        skill = SkillORM(name="tom-docx", manifest={"tools": ["read_docx"]})
        s.add(skill)
        s.commit()
        loaded = s.get(SkillORM, skill.id)
        assert loaded is not None
        assert loaded.status is SkillStatus.ACTIVE
        assert loaded.source == "builtin"
    finally:
        s.close()


def test_pattern_status_default(virtual_keyring: object, tmp_path: Path) -> None:
    init_db()
    s = SessionLocal()
    try:
        pat = PatternORM(sample_session_ids=["a", "b"])
        s.add(pat)
        s.commit()
        loaded = s.get(PatternORM, pat.id)
        assert loaded is not None
        assert loaded.status is PatternStatus.PENDING
        assert loaded.occurrences == 1
    finally:
        s.close()


def test_provider_config_uniqueness(virtual_keyring: object, tmp_path: Path) -> None:
    init_db()
    s = SessionLocal()
    try:
        s.add(ProviderConfigORM(type="ollama", name="local", is_default=True))
        s.commit()
        s.add(ProviderConfigORM(type="ollama", name="local", is_default=False))
        # sqlcipher3 may surface IntegrityError before SQLAlchemy wraps it; match
        # on the message so the test stays backend-agnostic.
        with pytest.raises(Exception, match="UNIQUE constraint"):
            s.commit()
    finally:
        s.rollback()
        s.close()


def test_audit_log_appends(virtual_keyring: object, tmp_path: Path) -> None:
    init_db()
    s = SessionLocal()
    try:
        for i in range(3):
            s.add(
                AuditLogORM(
                    actor="test",
                    action="write_file",
                    target_type="skill",
                    target_id=str(i),
                    payload={"i": i},
                )
            )
        s.commit()
        count = (
            s.execute(select(AuditLogORM).where(AuditLogORM.action == "write_file")).scalars().all()
        )
        assert len(count) == 3
    finally:
        s.close()


def test_engine_uses_data_dir_db(virtual_keyring: object, tmp_path: Path) -> None:
    init_db()
    engine = make_engine()
    with engine.connect() as conn:
        from sqlalchemy import text

        rows = conn.execute(text("SELECT count(*) FROM sessions")).all()
    assert rows
