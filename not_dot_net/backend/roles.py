"""RBAC role definitions — DB-backed via ConfigSection.

Roles are pure config data: the code knows nothing about specific role keys.
The server admin is identified by `User.is_superuser` (set via CLI or
bootstrap), and bypasses all permission checks in `permissions.has_permissions`.
"""

from pydantic import BaseModel

from not_dot_net.backend.app_config import ConfigSection, section


class RoleDefinition(BaseModel):
    label: str
    permissions: list[str] = []


class RolesConfig(BaseModel):
    default_role: str = ""
    roles: dict[str, RoleDefinition] = {}


roles_config = section("roles", RolesConfig, label="Roles")
