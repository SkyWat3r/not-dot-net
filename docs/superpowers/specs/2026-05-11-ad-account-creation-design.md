# AD Account Creation + Workflow AD Effects

## Overview

Replace the placeholder `it_account_creation` step at the end of the onboarding workflow with a real, prefilled Active Directory account creation form. Introduce a centralized Unix UID allocator that guarantees no-reuse across the lab's UID range. Add a generic "workflow step → AD effect" framework so any workflow step can declare AD modifications (e.g. *on approve, add target user to AD group X*) declaratively, configured via the existing workflow editor.

## Scope

In scope:
- New `uid_allocation` table + allocator service with PK-enforced no-reuse + seed-from-AD function.
- `AdAccountConfig` ConfigSection (UID range, OUs, eligible groups, default GID/shell, home-dir + mail templates, status group defaults).
- `ldap_create_user` primitive + companions (`ldap_user_exists_by_sam`, `ldap_add_to_groups`, `ldap_remove_from_groups`, `ldap_list_groups`).
- New `type: ad_account_creation` workflow step with bespoke prefilled form, sAMAccountName cascade, server-side create flow, temp-password reveal.
- Generic `effects: list[StepEffectConfig]` on workflow steps; four effect kinds (`ad_add_to_groups`, `ad_remove_from_groups`, `ad_enable_account`, `ad_disable_account`).
- Workflow editor "Effects" panel per step + new step-type entry.
- Seed-data updates: onboarding's last step switches to the new type; VPN workflow gets a demonstration effect (operators can rearrange via the editor).
- ~30 new tests; one Alembic migration; EN + FR i18n keys.

Out of scope (explicitly deferred):
- Off-boarding workflow (would consume `ad_disable_account` + `ad_remove_from_groups` but no concrete flow yet).
- `ad_set_attribute` effect kind — too generic without a concrete need.
- "Populate OUs from observing AD users" helper button.
- Live AD CN resolution in the editor's group picker (DNs only in v1).
- Multi-OU search base for `ldap_list_groups`.

## Components & Ownership

| Component | File | Responsibility |
|---|---|---|
| UID allocator | `backend/uid_allocator.py` (new) | `UidAllocation` model + `allocate_uid()` + `seed_from_ad()` + `list_allocations()`. |
| AD account config | `backend/ad_account_config.py` (new) | `AdAccountConfig` ConfigSection. |
| AD account primitives | `backend/auth/ldap.py` (extend) | `ldap_create_user`, `ldap_user_exists_by_sam`, `ldap_add_to_groups`, `ldap_remove_from_groups`, `ldap_list_groups`. |
| Effects framework | `backend/workflow_effects.py` (new) | `StepEffectConfig` model, `BaseEffectHandler`, `EFFECT_REGISTRY`, four handlers, `run_effects()`. |
| Step type + form | `frontend/workflow_step.py`, `backend/workflow_service.py` (extend) | `type: ad_account_creation` renderer + server-side submit handler. |
| Settings UI | `frontend/admin_ad_account.py` (new) | "Lock existing AD UIDs from AD" button + UID allocations table view. |
| Editor surface | `frontend/workflow_editor.py` (extend) | Effects panel per step + new step-type entry + cross-field warnings. |
| Migration | `alembic/versions/0013_uid_allocation.py` (new) | `uid_allocation` table. |
| Seed updates | `backend/workflow_service.py` (WorkflowsConfig default) | Onboarding last step → new type; demonstration effect on VPN. |

No new permission. Creating AD accounts requires `manage_users`; effects rely on the existing step's permission gate plus AD admin credentials prompted at action time.

## UID Allocator

### Data model

Single table, Alembic 0013:

```
uid_allocation
  uid          INTEGER PRIMARY KEY     -- the UID itself; PK enforces uniqueness
  source       TEXT NOT NULL           -- 'allocated' | 'seeded_from_ad'
  user_id      UUID NULL FK users.id
  sam_account  TEXT NULL               -- snapshot for traceability if user is later deleted
  acquired_at  TIMESTAMP NOT NULL
  note         TEXT NULL
```

Rows are never deleted. The PK on `uid` guarantees no-reuse at the DB level. FK on `user_id` benefits from the conftest's `PRAGMA foreign_keys=ON` already installed for SQLite tests.

### Config

In `AdAccountConfig`:
- `uid_min: int = 10000`
- `uid_max: int = 60000`

### Functions (`backend/uid_allocator.py`)

```python
async def allocate_uid(user_id: uuid.UUID, sam_account: str) -> int
async def seed_from_ad(bind_username: str, bind_password: str) -> SeedResult
async def list_allocations(*, limit: int = 200) -> list[UidAllocationView]
```

- `allocate_uid` uses SQL `SELECT MIN(t.uid + 1) FROM uid_allocation t LEFT JOIN uid_allocation t2 ON t2.uid = t.uid + 1 WHERE t2.uid IS NULL AND t.uid >= :min` (gap-finder), bounded by `:max`. Falls back to `:min` when the table has no rows in the range. Single round-trip, no race-prone read-modify-write. On PK collision (unlikely; concurrent writers), raises and the caller can retry.
- `seed_from_ad` paged-searches AD for entries with `uidNumber`, inserts each as `source='seeded_from_ad'`, skipping any UID already present. Idempotent. Triggered manually from Admin → Settings → AD Accounts → "Lock existing AD UIDs from AD" button. Recommended on first deploy and after any out-of-band AD changes.

### Audit

Every `allocate_uid` call emits an `AuditEvent` with `category="ad"`, `action="allocate_uid"`, target=user_id, detail=`{"uid": N, "sam": sam}`. `seed_from_ad` emits one event with the seed counts.

## AdAccountConfig

New ConfigSection (`section("ad_account", AdAccountConfig, label="AD Accounts")`):

```python
class AdAccountConfig(BaseModel):
    uid_min: int = 10000
    uid_max: int = 60000
    default_gid_number: int = 10000
    default_login_shell: str = "/bin/bash"
    home_directory_template: str = "/home/{sam}"
    mail_template: str = "{first}.{last}@lpp.polytechnique.fr"
    users_ous: list[str] = []                          # editable list of OU DNs
    eligible_groups: list[str] = []                    # AD group DNs operators can pick from
    default_groups_by_status: dict[str, list[str]] = {}  # status → group DNs to pre-check
    password_length: int = 16
```

Rendered by the existing `admin_settings._render_form`. List/dict fields use `chip_list_editor` / `keyed_chip_editor` (no new widget code). Each field gets a Pydantic `description` rendered as a tooltip.

`AdAccountConfig` is consumed by:
- The create form (prefill templates, eligible groups, status defaults, OU list).
- `allocate_uid` (range bounds).
- The effects framework's params validation (groups must be in `eligible_groups`).
- The workflow editor's group picker.

## AD Account Creation Step

### Form (rendered by a new branch in `frontend/workflow_step.py`)

Prefilled from accumulated `request.data`:

| Field | Type | Source / behaviour |
|---|---|---|
| First name | read-only | from `newcomer_info.first_name` |
| Last name | read-only | from `newcomer_info.last_name` |
| sAMAccountName | text, editable | derived live by cascading rule: `{last}` → `{last}{first[0]}` → `{last}{first[:2]}` → … each candidate AD-checked via `ldap_user_exists_by_sam`; first non-conflicting wins. Names lowercased + accent-stripped. If operator types manually, auto-derivation pauses; an availability indicator runs against AD on blur. |
| UID | read-only display | shows "next available: NNNN" — informational; allocation happens server-side at submit |
| Primary GID | number, editable | prefilled from `default_gid_number` |
| Login shell | text, editable | prefilled from `default_login_shell` |
| Home directory | text, editable | prefilled by rendering `home_directory_template` against `{sam}`; re-renders as sAM changes |
| OU | combo-box | from `users_ous`; no default selection (no hidden init state) |
| Mail | text, editable | prefilled by rendering `mail_template` against normalized first/last |
| Description | textarea, editable | no prefill; written to AD `description` and mirrored to local `User.description` |
| Groups | chip multiselect | over `eligible_groups`; pre-checked from `default_groups_by_status[request.data["status"]]` (fallback `[]`) |
| Notes | textarea, editable | free-form; stored in `request.data`, never pushed to AD |

The new step type also forces `actions=["complete"]` and disables the Fields panel in the workflow editor — fields are hardcoded by the renderer, not user-defined.

### Submit flow (server-side, in `workflow_service.submit_step` dispatch on `type=="ad_account_creation"`)

1. Validate form input (sAM non-empty + matches `USERNAME_RE`, OU in eligible list, GID in valid range, all chosen groups in `eligible_groups`).
2. Frontend prompts AD admin credentials before submit fires (same pattern as `_prompt_ad_credentials_then_save`); credentials forwarded as call args, never persisted.
3. Server re-checks sAM doesn't exist in AD; if it does, abort with a clear error (UI shows it next to the sAM field).
4. Generate temporary password (`secrets.token_urlsafe`, length per `AdAccountConfig.password_length`, made AD-policy compliant by adding required character classes if missing).
5. `allocate_uid(target_user_id, sam)` — commits a `uid_allocation` row.
6. `ldap_create_user(NewAdUser(...), bind_username, bind_password, ldap_cfg)` — see primitive details below. On failure: surface error, step stays pending, UID row stays committed (permanently consumed; "no reuse" invariant is absolute). Operator retries with different sAM/data; next attempt allocates a fresh UID.
7. On AD create success: write back `ldap_dn`, `uid_number`, `gid_number`, `ldap_username`, `mail`, `description` to the local `User` row resolved by `request.target_email`. Set `User.is_active = True`.
8. Apply chosen groups via `ldap_add_to_groups` — best-effort. Per-group failures collected and shown as a non-fatal toast + per-group audit entries; step still advances. Operator can retry failed adds via the directory edit dialog.
9. Mark step `complete`. Existing notification rule `event="complete", step="it_account_creation"` fires; the token-link email to target receives the new sAM + temp password (separately rendered template `account_created`).
10. Show the temp password to the operator in a one-time copyable dialog ("Copy" button + "Initial password — copy now, it will not be shown again").

### Failure semantics

- AD create failure: step stays pending; UID is permanently consumed (accepted waste).
- DB write-back failure after AD create succeeds (rare; local DB is in-process): operator sees the error, retries; the AD-existence precheck (step 3) catches the orphan AD user on retry, prompting the operator to either pick a different sAM or fix manually.
- Group-add failure: step still advances; per-group audit + non-fatal toast.

## AD Primitives (`backend/auth/ldap.py` extensions)

### `ldap_create_user`

```python
@dataclass(frozen=True)
class NewAdUser:
    sam_account: str
    given_name: str
    surname: str
    display_name: str
    mail: str
    description: str | None
    ou_dn: str
    uid_number: int
    gid_number: int
    login_shell: str
    home_directory: str
    initial_password: str
    must_change_password: bool = True

def ldap_create_user(
    new_user: NewAdUser,
    bind_username: str,
    bind_password: str,
    ldap_cfg: LdapConfig,
    connect: Callable[..., Connection] = default_ldap_connect,
) -> str:  # returns the created DN
```

Implementation steps:
1. `_ldap_bind(bind_username, bind_password, ldap_cfg, connect)` — raises `LdapModifyError` on bind failure (same pattern as `ldap_modify_user`).
2. Build `dn = f"CN={display_name},{ou_dn}"`.
3. `objectClass = ["top", "person", "organizationalPerson", "user"]`. POSIX attrs (`uidNumber`, `gidNumber`, `loginShell`, `unixHomeDirectory`) added directly — AD's RFC2307 schema is universally present in any AD env using Linux integration. If a deployment lacks it, `conn.add` fails clearly; we don't pre-probe.
4. Set `userAccountControl = 0x202` (NORMAL_ACCOUNT + ACCOUNTDISABLE — AD requires the account be disabled while password is unset).
5. `conn.add(dn, objectClass, attributes)` — on failure, raise `LdapModifyError` with `conn.result.description` + `message`.
6. `conn.modify(dn, {"unicodePwd": [(MODIFY_REPLACE, [_ad_encode_password(initial_password)])]})` — encoded as UTF-16LE quoted (AD quirk). On failure raise.
7. If `must_change_password`: `conn.modify(dn, {"pwdLastSet": [(MODIFY_REPLACE, ["0"])]})`.
8. Flip `userAccountControl` to `0x200` (enabled NORMAL_ACCOUNT — clears the disabled flag from step 4).
9. Return DN. Unbind in `finally`.

### Companion helpers

```python
def ldap_user_exists_by_sam(
    sam: str, bind_username: str, bind_password: str, ldap_cfg: LdapConfig,
    connect: Callable[..., Connection] = default_ldap_connect,
) -> bool

def ldap_add_to_groups(
    user_dn: str, group_dns: list[str],
    bind_username: str, bind_password: str, ldap_cfg: LdapConfig,
    connect: Callable[..., Connection] = default_ldap_connect,
) -> dict[str, str]  # group_dn → error message (empty dict = all succeeded)

def ldap_remove_from_groups(
    user_dn: str, group_dns: list[str], ...,
) -> dict[str, str]

@dataclass(frozen=True)
class GroupSummary:
    dn: str
    cn: str
    description: str | None

def ldap_list_groups(
    bind_username: str, bind_password: str, ldap_cfg: LdapConfig,
    *, base_dn: str | None = None,
    connect: Callable[..., Connection] = default_ldap_connect,
) -> list[GroupSummary]
```

`ldap_list_groups` paged-searches `(objectClass=group)` against the configured `base_dn` (defaults to `ldap_cfg.base_dn`). Used by the editor's group picker when the admin chooses to refresh `eligible_groups` from AD (a future enhancement; v1 only uses the helper for read-only group listing if surfaced).

`ldap_add_to_groups` and `ldap_remove_from_groups` iterate per group with `conn.modify(group_dn, {"member": [(MODIFY_ADD|MODIFY_DELETE, [user_dn])]})`. Each group attempted independently; the per-group failure dict is returned without raising. A bind that itself fails raises `LdapModifyError` (no per-group attempts).

## Workflow AD Effects Framework

### Config schema

`StepEffectConfig` (new in `backend/workflow_effects.py`):

```python
class StepEffectConfig(BaseModel):
    on_action: str                       # which action triggers it
    kind: Literal[
        "ad_add_to_groups",
        "ad_remove_from_groups",
        "ad_enable_account",
        "ad_disable_account",
    ]
    params: dict[str, Any] = {}          # kind-specific
```

Added to `WorkflowStepConfig`:

```python
effects: list[StepEffectConfig] = []
```

### Handler interface

```python
class BaseEffectHandler:
    kind: ClassVar[str]
    requires_ad_credentials: ClassVar[bool] = True

    def validate_params(self, params: dict) -> None: ...
    async def run(
        self,
        request: WorkflowRequest,
        step: WorkflowStepConfig,
        action: str,
        params: dict,
        ad_creds: tuple[str, str],
        actor: User,
    ) -> EffectResult: ...

@dataclass(frozen=True)
class EffectResult:
    succeeded: bool
    detail: dict[str, Any]
    failures: dict[str, str] = field(default_factory=dict)
```

`EFFECT_REGISTRY: dict[str, BaseEffectHandler]` populated at import time with the four v1 handlers.

### v1 handlers

- **`AdAddToGroupsHandler`** / **`AdRemoveFromGroupsHandler`**
  - `params = {"groups": ["DN", ...], "target": "target_person"}` (target defaults to `"target_person"` if omitted).
  - `validate_params` enforces each group DN ∈ `AdAccountConfig.eligible_groups`.
  - `run` resolves the target to a local `User` via `request.target_email` lookup; reads `User.ldap_dn`; calls `ldap_add_to_groups` / `ldap_remove_from_groups`; returns per-group failures in `EffectResult.failures`.

- **`AdEnableAccountHandler`** / **`AdDisableAccountHandler`**
  - `params = {"target": "target_person"}` (default).
  - `run` resolves target DN, calls `ldap_set_account_enabled(dn, enabled, ...)`.

Target is fixed to `target_person` for v1. Future expansion can add `"requester"` or `"target_email"` resolution. The editor never exposes `target` — it's filled by the handler at runtime.

### Integration in `workflow_service.submit_step`

After the engine validates the transition for the action but before commit:

1. Collect effects matching `on_action == action` for the step.
2. If any effect has `requires_ad_credentials=True` and credentials weren't provided: raise typed `AdCredentialsRequired` caught by the frontend, which prompts and re-submits with credentials attached. Same UX as today's `_prompt_ad_credentials_then_save`.
3. Persist the step transition (workflow advances).
4. Run effects in declared order. Each runs independently; results collected. Per-effect failures don't halt the chain.
5. Audit-log each outcome: `category="ad"`, `action=effect.kind`, target=resolved target user, detail=`{params, succeeded, failures}`.
6. Return aggregate result; frontend renders any failures as a non-fatal toast (consistent with `apply_bulk_ad_state`).

The new `ad_account_creation` step type does **not** route through the effects framework — it has its own bespoke submit handler (see above). It reuses the same credential-prompt UX and audit category, but its multi-stage flow (allocate UID, create user, write back, apply groups) is too specific to fit a generic post-action effect chain.

## Workflow Editor Surface

### Step-detail right pane

A new **Effects** expander, collapsed by default, between "Actions" and "Fields":

- Table with three columns: **On action** (select from this step's `actions`), **Kind** (select from 4 i18n-labeled kinds), **Params** (kind-specific renderer).
- "+ Add effect" button.
- Per-row delete.

`params` column dispatches by kind:
- `ad_add_to_groups` / `ad_remove_from_groups`: `chip_list_editor` over `AdAccountConfig.eligible_groups`. Group DNs shown as-is (no live AD CN resolution in v1).
- `ad_enable_account` / `ad_disable_account`: empty (no params).

### Step type select

The "Type" field on the step form gains `"ad_account_creation"` as an option. When selected, the editor:
- Hides/disables the Fields panel with a read-only banner: "This step renders the AD account creation form. Configure prefill defaults in Settings → AD Accounts."
- Forces `actions=["complete"]`.

### Cross-field warnings (`compute_warnings`)

Two new advisory warnings:
- Effect's `on_action` not present in the step's `actions` list → "Effect references unknown action: …"
- Effect's group DN not in `eligible_groups` → "Effect references group not in eligible_groups: …"

## Settings UI

New subpage **Settings → AD Accounts** (`frontend/admin_ad_account.py`, ~120 lines):

- Auto-rendered form over `AdAccountConfig` (uses existing `admin_settings._render_form`).
- "Lock existing AD UIDs from AD" button — prompts AD admin credentials, calls `seed_from_ad`, shows result toast: "Seeded N UIDs from AD, skipped M already present."
- Read-only allocations table (last 200 by `acquired_at` DESC): columns UID, sAMAccountName, user (link to directory), source, acquired_at.

Gated on `manage_settings` permission (same as other settings pages).

## i18n

New keys (EN + FR), grouped:
- 4 effect-kind labels (`effect_kind_ad_add_to_groups`, etc.).
- ~6 editor panel strings (`effects`, `add_effect`, `on_action`, `kind`, `params`, `groups`).
- ~10 create form strings: section title, field labels (`samaccountname`, `uid`, `primary_gid`, `login_shell`, `home_directory`, `ou`, `mail`, `description`, `notes`, `groups`).
- ~6 settings strings: `ad_accounts`, `uid_min`, `uid_max`, `lock_existing_ad_uids`, `lock_existing_ad_uids_result`, plus field descriptions.
- 2 temp-password dialog strings: `initial_password_copy_now`, `copied`.

Per `feedback-roles-are-pure-config.md`, no string mentions specific role/group names.

`test_i18n.py:shared_allowed` updated for identifier-style strings (`sAMAccountName`, `UID`, AD attribute names).

## Audit

New category `"ad"` covering all AD-touching operations from this work:

| Action | Detail |
|---|---|
| `allocate_uid` | `{uid, sam}` |
| `seed_uids` | `{seeded, skipped}` |
| `create_user` | `{dn, sam, uid, gid, ou}` |
| `add_to_groups` | `{user_dn, groups, failures}` |
| `remove_from_groups` | `{user_dn, groups, failures}` |
| `enable_account` | `{dn}` |
| `disable_account` | `{dn}` |

## Testing

Target ~30 new tests (project baseline ~708, post-implementation ~738).

**UID allocator** (`tests/test_uid_allocator.py`, ~10 tests):
- empty range → returns `uid_min`
- existing rows → returns smallest gap
- contiguous from `uid_min` → returns `max(uid)+1`
- range exhausted → raises `UidRangeExhausted`
- PK collision (simulated race) → raises cleanly, no partial state
- `seed_from_ad` idempotent (re-running doesn't duplicate)
- audit event emitted with correct fields

**`ldap_create_user`** (`tests/test_ldap_create_user.py`, ~6 tests, fake `connect`):
- happy path: correct `conn.add` payload, password set, `pwdLastSet=0`, UAC sequence (`0x202` then `0x200`)
- add fails → `LdapModifyError`, no password attempt
- password modify fails → raises
- `must_change_password=False` → no `pwdLastSet=0` call
- AD-encoded password is UTF-16LE quoted

**Effects framework** (`tests/test_workflow_effects.py`, ~8 tests):
- `validate_params` rejects groups not in `eligible_groups`
- `ad_add_to_groups` happy path
- per-group failure collected (one fails, other succeeds → `EffectResult.failures` populated, no exception)
- `AdCredentialsRequired` raised when missing
- effect on non-matching action → no-op
- unknown kind in config → flagged by editor warning + skipped at runtime with audit entry

**`ad_account_creation` step** (`tests/test_ad_account_creation.py`, ~6 tests):
- sAM derivation cascade (`{last}` taken → tries `{last}{first[0]}`)
- mail + home-dir template rendering
- happy submit: UID allocated, AD called, local User updated, step advances
- AD create failure → step stays pending, UID row still committed
- group-add failure after successful create → step advances, failures audit-logged
- sAM pre-existing in AD → submit rejected before allocation

**Editor regression** (`tests/test_workflow_editor.py`, +3):
- effects panel renders existing rows
- `compute_warnings` flags effect referencing unknown action
- `compute_warnings` flags effect referencing group not in `eligible_groups`

The conftest's `PRAGMA foreign_keys=ON` is already in place — new FK `uid_allocation.user_id → users.id` is enforced in tests for free.

## Backwards Compatibility

- Existing onboarding requests sitting on the old `notes`-only `it_account_creation` step see the new form on next render. Their `data` lacks the new form fields — the form treats them as empty and the operator fills them in. The step `key` stays `it_account_creation` so the existing notification rule (`event="complete", step="it_account_creation"`) keeps firing.
- `WorkflowStepConfig.effects` defaults to `[]` — every persisted workflow JSON is forwards-compatible without migration.
- No changes to `User`, `WorkflowRequest`, `WorkflowEvent`, or any other existing model.

## Rollout Order (implementation phasing)

1. UID allocator: model + Alembic 0013 + `allocate_uid` + `seed_from_ad` + tests.
2. `AdAccountConfig` ConfigSection.
3. `ldap_create_user` + `ldap_user_exists_by_sam` + `ldap_add_to_groups` / `ldap_remove_from_groups` + `ldap_list_groups` + tests.
4. `ad_account_creation` step type: frontend form + `workflow_service` submit handler + tests.
5. Effects framework: `StepEffectConfig`, registry, four handlers, `run_effects` integration in `submit_step` + tests.
6. Workflow editor: effects panel + step-type select entry + `compute_warnings` extensions.
7. Settings UI: AD Accounts subpage + "Lock existing AD UIDs from AD" button.
8. Seed updates: onboarding's last step → new type; demonstration effect on VPN workflow.
9. i18n EN+FR; audit category wiring; full-suite test pass.

Each phase is independently testable. Phases 1–3 form a foundation that ships value (UID management + the create primitive available to any future flow) before phase 4 wires them into onboarding.
