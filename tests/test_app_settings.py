"""Tests for runtime-editable app settings (OS choices, software tags)."""

import pytest

from not_dot_net.backend.app_settings import (
    get_os_choices,
    get_software_tags,
    set_os_choices,
    set_software_tags,
)
from not_dot_net.backend.db import Base
from not_dot_net.config import init_settings
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
import not_dot_net.backend.db as db_module
import not_dot_net.backend.app_settings  # noqa: F401


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


async def test_os_choices_defaults_from_config():
    choices = await get_os_choices()
    assert isinstance(choices, list)


async def test_set_and_get_os_choices():
    await set_os_choices(["Ubuntu", "Windows 11", "Fedora"])
    choices = await get_os_choices()
    assert choices == ["Ubuntu", "Windows 11", "Fedora"]


async def test_os_choices_override_persists():
    await set_os_choices(["Arch"])
    assert await get_os_choices() == ["Arch"]
    await set_os_choices(["Debian", "NixOS"])
    assert await get_os_choices() == ["Debian", "NixOS"]


async def test_software_tags_defaults_from_config():
    tags = await get_software_tags()
    assert isinstance(tags, dict)


async def test_set_and_get_software_tags():
    tags = {"science": ["Python", "Julia"], "dev": ["GCC", "CMake"]}
    await set_software_tags(tags)
    result = await get_software_tags()
    assert result == tags


async def test_software_tags_override_persists():
    await set_software_tags({"a": ["1"]})
    assert await get_software_tags() == {"a": ["1"]}
    await set_software_tags({"b": ["2", "3"]})
    assert await get_software_tags() == {"b": ["2", "3"]}
