"""Tests for user update and delete operations (directory functions)."""

import pytest
import uuid
from contextlib import asynccontextmanager

from not_dot_net.backend.db import Base, User, session_scope, get_user_db
from not_dot_net.backend.users import get_user_manager
from not_dot_net.backend.schemas import UserCreate, UserUpdate
from not_dot_net.backend.roles import Role
from not_dot_net.config import init_settings
from sqlalchemy import select
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


async def _create_user(email="user@test.com", password="Password1!") -> User:
    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as manager:
                return await manager.create(UserCreate(email=email, password=password))


async def _update_user(user_id, updates: dict):
    """Mirror of directory.py _update_user."""
    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as manager:
                user = await manager.get(user_id)
                update_schema = UserUpdate(**updates)
                await manager.update(update_schema, user)


async def _delete_user(user_id):
    """Mirror of directory.py _delete_user."""
    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as manager:
                user = await manager.get(user_id)
                await manager.delete(user)


async def test_update_user_full_name():
    user = await _create_user()
    await _update_user(user.id, {"full_name": "Alice Smith"})
    async with session_scope() as session:
        refreshed = await session.get(User, user.id)
        assert refreshed.full_name == "Alice Smith"


async def test_update_user_multiple_fields():
    user = await _create_user()
    await _update_user(user.id, {"full_name": "Bob", "phone": "+33123", "office": "B204"})
    async with session_scope() as session:
        refreshed = await session.get(User, user.id)
        assert refreshed.full_name == "Bob"
        assert refreshed.phone == "+33123"
        assert refreshed.office == "B204"


async def test_update_user_role():
    user = await _create_user()
    await _update_user(user.id, {"role": Role.ADMIN})
    async with session_scope() as session:
        refreshed = await session.get(User, user.id)
        assert refreshed.role == Role.ADMIN


async def test_delete_user():
    user = await _create_user()
    await _delete_user(user.id)
    async with session_scope() as session:
        refreshed = await session.get(User, user.id)
        # FastAPI-Users soft-deletes by setting is_active=False
        # or hard-deletes depending on config. Check either case.
        if refreshed is not None:
            assert refreshed.is_active is False
        else:
            assert refreshed is None


async def test_delete_nonexistent_user_raises():
    from fastapi_users.exceptions import UserNotExists
    with pytest.raises(UserNotExists):
        await _delete_user(uuid.uuid4())
