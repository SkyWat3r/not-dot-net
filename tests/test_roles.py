# tests/test_roles.py
import pytest

from not_dot_net.backend.roles import RoleDefinition, RolesConfig, roles_config


async def test_default_config_has_no_roles():
    """Roles are pure config data — the default config ships empty.
    No role key (including 'admin') has any built-in meaning in code."""
    cfg = await roles_config.get()
    assert cfg.roles == {}


async def test_set_roles_config():
    cfg = await roles_config.get()
    cfg.roles["staff"] = RoleDefinition(
        label="Staff", permissions=["create_workflows"]
    )
    await roles_config.set(cfg)
    reloaded = await roles_config.get()
    assert "staff" in reloaded.roles
    assert reloaded.roles["staff"].permissions == ["create_workflows"]


async def test_any_role_can_be_deleted():
    """No role is special — any role the admin creates can be removed."""
    cfg = await roles_config.get()
    cfg.roles["whatever"] = RoleDefinition(label="Whatever", permissions=["x"])
    await roles_config.set(cfg)

    cfg = await roles_config.get()
    del cfg.roles["whatever"]
    await roles_config.set(cfg)

    reloaded = await roles_config.get()
    assert "whatever" not in reloaded.roles


async def test_default_role_field():
    cfg = await roles_config.get()
    assert cfg.default_role == ""
