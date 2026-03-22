import pytest
import uuid
from unittest.mock import patch, AsyncMock
from not_dot_net.backend.workflow_service import create_request, submit_step
from not_dot_net.backend.roles import Role
from not_dot_net.backend.db import Base, User, get_async_session
from not_dot_net.config import init_settings
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from contextlib import asynccontextmanager
import not_dot_net.backend.db as db_module
import not_dot_net.backend.workflow_models  # noqa: F401


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
    get_session = asynccontextmanager(get_async_session)
    async with get_session() as session:
        user = User(id=uuid.uuid4(), email=email, hashed_password="x", role=role)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def test_submit_step_fires_notifications():
    """After submitting the request step of vpn_access, directors should be notified."""
    staff = await _create_user(email="staff@test.com", role=Role.STAFF)
    director = await _create_user(email="director@test.com", role=Role.DIRECTOR)

    req = await create_request(
        workflow_type="vpn_access",
        created_by=staff.id,
        data={"target_name": "Alice", "target_email": "alice@test.com"},
    )

    with patch("not_dot_net.backend.workflow_service.notify", new_callable=AsyncMock) as mock_notify:
        mock_notify.return_value = ["director@test.com"]
        await submit_step(req.id, staff.id, "submit", data={})
        mock_notify.assert_called_once()
        assert mock_notify.call_args.kwargs["event"] == "submit"


async def test_approve_fires_notifications():
    staff = await _create_user(email="staff@test.com", role=Role.STAFF)
    director = await _create_user(email="director@test.com", role=Role.DIRECTOR)

    req = await create_request(
        workflow_type="vpn_access",
        created_by=staff.id,
        data={"target_name": "Alice", "target_email": "alice@test.com"},
    )

    # Submit first step (mock notifications)
    with patch("not_dot_net.backend.workflow_service.notify", new_callable=AsyncMock) as mock_notify:
        mock_notify.return_value = []
        req = await submit_step(req.id, staff.id, "submit", data={})

    # Approve (check notifications fire)
    with patch("not_dot_net.backend.workflow_service.notify", new_callable=AsyncMock) as mock_notify:
        mock_notify.return_value = []
        await submit_step(req.id, director.id, "approve", data={})
        mock_notify.assert_called_once()
