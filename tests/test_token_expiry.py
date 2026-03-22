"""Tests for workflow token expiry."""

import pytest
import uuid
from datetime import datetime, timedelta, timezone

from not_dot_net.backend.workflow_service import (
    create_request,
    submit_step,
    get_request_by_token,
)
from not_dot_net.backend.workflow_models import WorkflowRequest
from not_dot_net.backend.db import Base, User, session_scope
from not_dot_net.backend.roles import Role
from not_dot_net.config import init_settings
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
import not_dot_net.backend.db as db_module
import not_dot_net.backend.workflow_models  # noqa: F401
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


async def _create_user(email="staff@test.com", role=Role.STAFF) -> User:
    async with session_scope() as session:
        user = User(id=uuid.uuid4(), email=email, hashed_password="x", role=role)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def test_valid_token_found():
    """A token within its expiry window should be found."""
    user = await _create_user()
    req = await create_request(
        workflow_type="onboarding",
        created_by=user.id,
        data={"person_name": "Test", "person_email": "test@ext.com",
              "role_status": "postdoc", "team": "Plasma Physics", "start_date": "2026-04-01"},
    )
    # Submit the request step to advance to newcomer_info (which generates a token)
    req = await submit_step(req.id, user.id, "submit", data={})
    assert req.token is not None

    found = await get_request_by_token(req.token)
    assert found is not None
    assert found.id == req.id


async def test_expired_token_not_found():
    """An expired token should not be found."""
    user = await _create_user()
    req = await create_request(
        workflow_type="onboarding",
        created_by=user.id,
        data={"person_name": "Test", "person_email": "test@ext.com",
              "role_status": "postdoc", "team": "Plasma Physics", "start_date": "2026-04-01"},
    )
    req = await submit_step(req.id, user.id, "submit", data={})
    assert req.token is not None

    # Manually expire the token
    async with session_scope() as session:
        db_req = await session.get(WorkflowRequest, req.id)
        db_req.token_expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await session.commit()

    found = await get_request_by_token(req.token)
    assert found is None


async def test_nonexistent_token_not_found():
    assert await get_request_by_token("nonexistent-token") is None
