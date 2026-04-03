# RBAC Design: Permission-Based Authorization

## Context

The current authorization system uses a 4-tier linear role hierarchy (MEMBER < STAFF < DIRECTOR < ADMIN) with a single `has_role(user, minimum_role)` check. Authorization is enforced almost exclusively at the frontend (UI visibility), leaving backend service functions unprotected. Role definitions are hardcoded as a Python enum.

**Problems:**
- Backend operations (resource CRUD, user management, workflow creation) have no role checks
- Optional `actor_user` / `is_admin` parameters let callers skip authorization
- Fixed role hierarchy can't adapt to changing organizational needs without code changes
- No way to create custom roles or reassign permissions without redeploying

## Design

### Permission Registry

A module-local registry pattern (same philosophy as `ConfigSection`). Each module declares the permissions it needs; a central registry collects them.

**`backend/permissions.py`** provides the infrastructure:

```python
@dataclass(frozen=True)
class PermissionInfo:
    key: str
    label: str
    description: str

_registry: dict[str, PermissionInfo] = {}

def permission(key: str, label: str, description: str = "") -> str:
    """Register a permission and return its key."""
    _registry[key] = PermissionInfo(key=key, label=label, description=description)
    return key

def get_permissions() -> dict[str, PermissionInfo]:
    """Return all registered permissions."""
    return _registry
```

**Enforcement functions** (also in `permissions.py`):

```python
async def has_permissions(user, *permissions: str) -> bool:
    """Check if user's role grants all given permissions."""
    role_config = await RolesConfig.get()
    role = role_config.roles.get(user.role)
    if role is None:
        return False
    return all(p in role.permissions for p in permissions)

def require(*permissions: str):
    """FastAPI dependency — raises 403 if user lacks permissions."""
    async def checker(user=Depends(current_active_user)):
        if not await has_permissions(user, *permissions):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user
    return checker

async def check_permission(user, *permissions: str) -> None:
    """NiceGUI callback guard — raises PermissionError if user lacks permissions."""
    if not await has_permissions(user, *permissions):
        raise PermissionError("Insufficient permissions")
```

### Permission Catalog

Each module owns its permissions. No central enum to maintain.

| Module | Permission Key | Label | Description |
|---|---|---|---|
| `backend/booking_service.py` | `manage_bookings` | Manage bookings | Create/edit/delete resources and software |
| `backend/workflow_service.py` | `create_workflows` | Create workflows | Start new workflow requests |
| `backend/workflow_service.py` | `approve_workflows` | Approve workflows | Act on role-assigned workflow steps |
| `frontend/directory.py` or `backend/users.py` | `manage_users` | Manage users | Edit/delete users in directory |
| `frontend/audit_log.py` | `view_audit_log` | View audit log | Access the audit log |
| `backend/app_config.py` or `permissions.py` | `manage_settings` | Manage settings | Access admin settings page |
| `backend/permissions.py` | `manage_roles` | Manage roles | Create/edit roles and their permissions |

### Role Storage

Roles and their permission sets are stored as a `ConfigSection` in the existing `app_setting` DB table.

**Schema:**

```python
class RoleDefinition(BaseModel):
    label: str
    permissions: list[str] = []

class RolesConfig(BaseModel):
    roles: dict[str, RoleDefinition] = {
        "admin": RoleDefinition(
            label="Administrator",
            permissions=[]  # seed script fills this; lockout guard ensures manage_roles + manage_settings
        )
    }
```

**Lockout guard:** The `RolesConfig` section overrides `set()` to enforce:
- A role named `"admin"` always exists
- `"admin"` always includes `manage_roles` and `manage_settings`

**Seed default:** On first run, the `"admin"` role is created with all registered permissions. No other roles are pre-created. Admins build additional roles through the UI.

### User Model Change

`User.role` changes from `role: Mapped[Role]` (enum) to `role: Mapped[str]` (plain string key into `RolesConfig`). Default value: `"admin"` for the first-run setup user. New users created via registration or LDAP get a configurable default role (stored in `RolesConfig.default_role`, initially empty string meaning no permissions).

`is_superuser` stays synced: `user.is_superuser = (user.role == "admin")`.

### Workflow Step Authorization

Workflow step configs change `assignee_role` to `assignee_permission` — a permission key string. `can_user_act()` in `workflow_engine.py` becomes async (needs to read role config) or moves to the service layer.

Since `workflow_engine.py` is currently pure (no DB/IO), the permission check should move to `workflow_service.py`. The engine keeps the structural logic (what step is current, who is the assignee target), but the "does this user have this permission" check happens in the service.

### Frontend Changes

All `has_role(user, Role.X)` calls become `await has_permissions(user, SOME_PERMISSION)` calls:

| Location | Old Check | New Check |
|---|---|---|
| `shell.py` tab visibility | `has_role(user, Role.STAFF)` | `await has_permissions(user, CREATE_WORKFLOWS)` |
| `shell.py` admin tabs | `has_role(user, Role.ADMIN)` | `await has_permissions(user, MANAGE_SETTINGS)` |
| `bookings.py` admin sections | `has_role(user, Role.ADMIN)` | `await check_permission(user, MANAGE_BOOKINGS)` |
| `directory.py` user edit/delete | none (gap) | `await check_permission(user, MANAGE_USERS)` |
| `admin_settings.py` render | none (gap) | `await check_permission(user, MANAGE_SETTINGS)` |
| `new_request.py` | `has_role(user, Role(wf.start_role))` | `await has_permissions(user, CREATE_WORKFLOWS)` |
| `dashboard.py` all-requests | `has_role(user, Role.ADMIN)` | `await has_permissions(user, VIEW_AUDIT_LOG)` or similar |

### Admin Roles UI

New file: `frontend/admin_roles.py`

- Table of roles: name, label, permission count
- Add/edit role: name + label + checklist of all `get_permissions()` entries
- Delete role: blocked if role is `"admin"` or has users assigned
- Accessible from the Settings tab, guarded by `manage_roles` permission

Role assignment to users: dropdown on user profile / directory page, guarded by `manage_users`.

## Files Changed

| File | Change |
|---|---|
| `backend/permissions.py` | **New** — registry, `require()`, `check_permission()`, `has_permissions()`, `manage_roles` + `manage_settings` permissions |
| `backend/roles.py` | **Rewrite** — remove `Role` enum, `has_role()`, `_ROLE_ORDER`; add `RolesConfig` section with lockout guard |
| `backend/db.py` | `User.role` becomes `str`, remove `Role` import |
| `backend/users.py` | `is_superuser` sync uses `role == "admin"` string |
| `backend/booking_service.py` | Declare `MANAGE_BOOKINGS`, add `check_permission()` to resource CRUD, pass user to `cancel_booking()` |
| `backend/workflow_service.py` | Declare `CREATE_WORKFLOWS` + `APPROVE_WORKFLOWS`, enforce in `create_request()`, make `actor_user` mandatory |
| `backend/workflow_engine.py` | Remove `has_role` import, remove permission check (moves to service layer) |
| `config.py` | Workflow step: `assignee_role` -> `assignee_permission` |
| `frontend/shell.py` | Tab visibility uses `has_permissions()` |
| `frontend/bookings.py` | Admin sections guarded by `check_permission(MANAGE_BOOKINGS)` |
| `frontend/directory.py` | `_update_user`/`_delete_user` guarded by `check_permission(MANAGE_USERS)` |
| `frontend/admin_settings.py` | Guarded by `check_permission(MANAGE_SETTINGS)` |
| `frontend/admin_roles.py` | **New** — roles management UI |
| `frontend/dashboard.py` | Permission-based filtering |
| `frontend/new_request.py` | Uses `CREATE_WORKFLOWS` permission |
| `frontend/audit_log.py` | Declares + uses `VIEW_AUDIT_LOG` |
| Tests | Update all role-based tests |
