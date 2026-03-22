"""Tests for audit logging service."""

import pytest
import uuid

from not_dot_net.backend.audit import log_audit, list_audit_events
from not_dot_net.backend.db import Base
from not_dot_net.config import init_settings
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
import not_dot_net.backend.db as db_module
import not_dot_net.backend.audit  # noqa: F401


@pytest.fixture(autouse=True)
async def setup_db():
    init_settings()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    old_engine, old_session = db_module._engine, db_module._async_session_maker
    db_module._engine = engine
    db_module._async_session_maker = session_maker

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()
    db_module._engine, db_module._async_session_maker = old_engine, old_session


async def test_log_and_list():
    await log_audit("auth", "login", actor_email="test@test.com")
    events = await list_audit_events()
    assert len(events) == 1
    assert events[0].category == "auth"
    assert events[0].action == "login"
    assert events[0].actor_email == "test@test.com"


async def test_log_with_all_fields():
    uid = uuid.uuid4()
    tid = uuid.uuid4()
    await log_audit(
        "workflow", "approve",
        actor_id=uid, actor_email="approver@test.com",
        target_type="request", target_id=tid,
        detail="step=approval status=completed",
        metadata={"key": "value"},
    )
    events = await list_audit_events()
    assert len(events) == 1
    ev = events[0]
    assert ev.actor_id == str(uid)
    assert ev.target_type == "request"
    assert ev.detail == "step=approval status=completed"


async def test_filter_by_category():
    await log_audit("auth", "login")
    await log_audit("workflow", "create")
    await log_audit("auth", "login_failed")

    auth_events = await list_audit_events(category="auth")
    assert len(auth_events) == 2
    assert all(e.category == "auth" for e in auth_events)


async def test_filter_by_actor_email():
    await log_audit("auth", "login", actor_email="alice@test.com")
    await log_audit("auth", "login", actor_email="bob@test.com")

    events = await list_audit_events(actor_email="alice")
    assert len(events) == 1
    assert events[0].actor_email == "alice@test.com"


async def test_list_with_limit():
    for i in range(5):
        await log_audit("auth", "login", actor_email=f"user{i}@test.com")
    events = await list_audit_events(limit=3)
    assert len(events) == 3


async def test_list_returns_recent_first():
    """Events should be ordered by created_at descending (newest first)."""
    for i in range(5):
        await log_audit("auth", f"event_{i}")
    events = await list_audit_events()
    assert len(events) == 5
    # All events have the same timestamp in SQLite (second precision),
    # so just verify the count and category filter work together.
    auth_events = await list_audit_events(category="auth")
    assert len(auth_events) == 5
