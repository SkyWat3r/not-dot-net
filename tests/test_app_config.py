import pytest
from pydantic import BaseModel


class SampleConfig(BaseModel):
    name: str = "default"
    count: int = 42
    tags: list[str] = ["a", "b"]


async def test_get_returns_defaults_when_no_db_row():
    from not_dot_net.backend.app_config import section
    cfg_section = section("test_default", SampleConfig)
    result = await cfg_section.get()
    assert result == SampleConfig()


async def test_set_then_get_roundtrips():
    from not_dot_net.backend.app_config import section
    cfg_section = section("test_roundtrip", SampleConfig)
    custom = SampleConfig(name="custom", count=99, tags=["x"])
    await cfg_section.set(custom)
    result = await cfg_section.get()
    assert result == custom


async def test_reset_reverts_to_defaults():
    from not_dot_net.backend.app_config import section
    cfg_section = section("test_reset", SampleConfig)
    await cfg_section.set(SampleConfig(name="changed"))
    await cfg_section.reset()
    result = await cfg_section.get()
    assert result == SampleConfig()


async def test_registry_tracks_sections():
    from not_dot_net.backend.app_config import section, get_registry
    cfg_section = section("test_registry", SampleConfig)
    registry = get_registry()
    assert "test_registry" in registry
    assert registry["test_registry"] is cfg_section
