# AD Account Creation + Workflow AD Effects Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the placeholder `it_account_creation` onboarding step with a real AD account creation flow backed by a centralized UID allocator, and introduce a generic "workflow step → AD effect" framework (add/remove from groups, enable/disable account) wired into the workflow editor.

**Architecture:**
- New `uid_allocation` table with PK-enforced no-reuse; `allocate_uid()` picks the smallest free integer in a configured range via SQL gap-finder.
- New `ldap_create_user` primitive on top of existing `ldap_modify_user` patterns; AD admin credentials prompted at action time (no service account stored).
- Dedicated `ad_account_creation` workflow step type for the rich create form; declarative `effects: list[StepEffectConfig]` on other workflow steps, dispatched through a small handler registry, for simpler AD operations.

**Tech Stack:** Python 3.10+, SQLAlchemy 2.x async, FastAPI, NiceGUI 3.4+, ldap3, Pydantic v2, Alembic, pytest with the `nicegui.testing.user_plugin`.

**Reference spec:** `docs/superpowers/specs/2026-05-11-ad-account-creation-design.md`

---

## File Structure

**New files:**
- `not_dot_net/backend/uid_allocator.py` — `UidAllocation` model, `allocate_uid`, `seed_from_ad`, `list_allocations`, `UidRangeExhausted`.
- `not_dot_net/backend/ad_account_config.py` — `AdAccountConfig` Pydantic model + ConfigSection registration.
- `not_dot_net/backend/workflow_effects.py` — `StepEffectConfig`, `BaseEffectHandler`, four handlers, `EFFECT_REGISTRY`, `run_effects`, `AdCredentialsRequired`, `EffectResult`.
- `not_dot_net/frontend/ad_credentials.py` — reusable AD admin credentials prompt dialog (factored out of `directory.py:_prompt_ad_credentials_then_save`).
- `not_dot_net/frontend/admin_ad_account.py` — Settings → AD Accounts page (settings form + "Lock existing AD UIDs" button + allocations table).
- `alembic/versions/0013_uid_allocation.py` — migration.
- `tests/test_uid_allocator.py`
- `tests/test_ldap_create_user.py`
- `tests/test_workflow_effects.py`
- `tests/test_ad_account_creation.py`

**Modified files:**
- `not_dot_net/backend/auth/ldap.py` — add `ldap_user_exists_by_sam`, `ldap_create_user`, `ldap_add_to_groups`, `ldap_remove_from_groups`, `ldap_list_groups`, `NewAdUser` dataclass, `GroupSummary` dataclass, `_ad_encode_password` helper.
- `not_dot_net/backend/workflow_service.py` — dispatch on `type=="ad_account_creation"` in `submit_step`; call `run_effects` after step commit when the step has `effects`; update default `WorkflowsConfig.workflows` (onboarding last step, VPN demo effect).
- `not_dot_net/config.py` — add `effects` field to `WorkflowStepConfig`; add `"ad_account_creation"` to step-type literal.
- `not_dot_net/frontend/workflow_step.py` — new render branch for `type=="ad_account_creation"`.
- `not_dot_net/frontend/workflow_editor.py` — Effects panel per step + step-type entry + warnings + lock Fields panel for `ad_account_creation`.
- `not_dot_net/frontend/workflow_editor_options.py` — effect-kind options helper.
- `not_dot_net/frontend/directory.py` — replace local `_prompt_ad_credentials_then_save` with import from `frontend/ad_credentials.py`.
- `not_dot_net/frontend/shell.py` — wire the new Admin → AD Accounts menu entry.
- `not_dot_net/frontend/i18n.py` — new EN + FR keys.
- `tests/test_workflow_editor.py` — extend with effects panel regression tests.
- `tests/test_i18n.py` — extend `shared_allowed` for new identifier-style strings (e.g. `sAMAccountName`, `UID`).

---

## Task 1: UidAllocation model + Alembic migration

**Files:**
- Create: `not_dot_net/backend/uid_allocator.py`
- Create: `alembic/versions/0013_uid_allocation.py`
- Modify: `tests/conftest.py:30-40` (add `import not_dot_net.backend.uid_allocator` to the model-import block so `Base.metadata.create_all` sees it)

- [ ] **Step 1.1: Write the model + migration**

Create `not_dot_net/backend/uid_allocator.py`:

```python
"""Centralized Unix UID allocator. PK enforces no-reuse."""
from __future__ import annotations
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import String, DateTime, ForeignKey, select, func
from sqlalchemy.orm import Mapped, mapped_column, MappedAsDataclass

from not_dot_net.backend.db import Base


class UidRangeExhausted(Exception):
    """No free UID left in the configured range."""


class UidAllocation(MappedAsDataclass, Base, kw_only=True):
    __tablename__ = "uid_allocation"

    uid: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # 'allocated' | 'seeded_from_ad'
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True, default=None,
    )
    sam_account: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default_factory=lambda: datetime.now(timezone.utc),
    )
    note: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)


@dataclass(frozen=True)
class UidAllocationView:
    uid: int
    source: str
    user_id: uuid.UUID | None
    sam_account: str | None
    acquired_at: datetime
    note: str | None
```

Create `alembic/versions/0013_uid_allocation.py`:

```python
"""Add uid_allocation table for centralized UID management.

Revision ID: 0013
Revises: 0012
"""
from alembic import op
import sqlalchemy as sa


revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "uid_allocation",
        sa.Column("uid", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("sam_account", sa.String(64), nullable=True),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("note", sa.String(255), nullable=True),
    )
    op.create_index("ix_uid_allocation_acquired_at", "uid_allocation", ["acquired_at"])
    op.create_index("ix_uid_allocation_user_id", "uid_allocation", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_uid_allocation_user_id", table_name="uid_allocation")
    op.drop_index("ix_uid_allocation_acquired_at", table_name="uid_allocation")
    op.drop_table("uid_allocation")
```

- [ ] **Step 1.2: Wire conftest to import the new model**

Open `tests/conftest.py`, locate the block of `import not_dot_net.backend.*` lines after `Base.metadata.create_all`, and add:

```python
import not_dot_net.backend.uid_allocator  # noqa: F401
```

- [ ] **Step 1.3: Run the existing suite to confirm the model loads**

Run: `uv run pytest -x -q`
Expected: all tests pass (708 baseline). No regressions from adding a table.

- [ ] **Step 1.4: Commit**

```bash
git add not_dot_net/backend/uid_allocator.py alembic/versions/0013_uid_allocation.py tests/conftest.py
git commit -m "feat(uid): add uid_allocation table + model"
```

---

## Task 2: AdAccountConfig section

**Files:**
- Create: `not_dot_net/backend/ad_account_config.py`
- Test: `tests/test_config_sections.py` (extend if it asserts registry contents)

- [ ] **Step 2.1: Write the ConfigSection**

Create `not_dot_net/backend/ad_account_config.py`:

```python
"""Settings for AD account creation: UID range, OUs, eligible groups, templates."""
from __future__ import annotations
from pydantic import BaseModel, Field

from not_dot_net.backend.app_config import section


class AdAccountConfig(BaseModel):
    uid_min: int = Field(
        default=10000,
        description="Lowest UID that the allocator may hand out.",
    )
    uid_max: int = Field(
        default=60000,
        description="Highest UID (inclusive) that the allocator may hand out.",
    )
    default_gid_number: int = Field(
        default=10000,
        description="Default primary GID for new accounts (operator can override).",
    )
    default_login_shell: str = Field(
        default="/bin/bash",
        description="Default loginShell for new accounts.",
    )
    home_directory_template: str = Field(
        default="/home/{sam}",
        description="Template for unixHomeDirectory. {sam} is replaced with the sAMAccountName.",
    )
    mail_template: str = Field(
        default="{first}.{last}@lpp.polytechnique.fr",
        description="Template for the mail attribute. {first}/{last} are normalized (lowercased, accent-stripped).",
    )
    users_ous: list[str] = Field(
        default_factory=list,
        description="Distinguished names of OUs in which new users may be created.",
    )
    eligible_groups: list[str] = Field(
        default_factory=list,
        description="AD group DNs that may be picked in the create form and in step effects.",
    )
    default_groups_by_status: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Per-employment-status pre-selected groups, e.g. {'Intern': ['CN=...']}.",
    )
    password_length: int = Field(
        default=16,
        description="Length of the auto-generated initial password.",
    )


ad_account_config = section("ad_account", AdAccountConfig, label="AD Accounts")
```

- [ ] **Step 2.2: Wire the import so the section registers**

Add to `not_dot_net/app.py` near the top, alongside the other config-section imports:

```python
from not_dot_net.backend import ad_account_config  # noqa: F401  # registers AdAccountConfig
```

(If `app.py` imports a `__init__` module that already pulls in sections, follow that pattern instead — `grep -n "import .*_config" not_dot_net/app.py` first; add the import where the others sit.)

- [ ] **Step 2.3: Write a sanity test**

Append to `tests/test_config_sections.py` (or create it if it doesn't exist — `find tests -name "test_config_sections.py"`):

```python
import pytest


@pytest.mark.asyncio
async def test_ad_account_config_defaults():
    from not_dot_net.backend.ad_account_config import ad_account_config
    cfg = await ad_account_config.get()
    assert cfg.uid_min == 10000
    assert cfg.uid_max == 60000
    assert cfg.mail_template.endswith("@lpp.polytechnique.fr")
    assert cfg.eligible_groups == []
```

- [ ] **Step 2.4: Run the test**

Run: `uv run pytest tests/test_config_sections.py -v`
Expected: the new test passes (existing ones too).

- [ ] **Step 2.5: Commit**

```bash
git add not_dot_net/backend/ad_account_config.py not_dot_net/app.py tests/test_config_sections.py
git commit -m "feat(config): add AdAccountConfig section (UID range, OUs, groups, templates)"
```

---

## Task 3: `allocate_uid` — gap-finder

**Files:**
- Modify: `not_dot_net/backend/uid_allocator.py`
- Create: `tests/test_uid_allocator.py`

- [ ] **Step 3.1: Write the failing tests**

Create `tests/test_uid_allocator.py`:

```python
import uuid
import pytest
from sqlalchemy import select

from not_dot_net.backend.db import session_scope, User, AuthMethod
from not_dot_net.backend.uid_allocator import (
    allocate_uid, UidAllocation, UidRangeExhausted,
)
from not_dot_net.backend.ad_account_config import ad_account_config


async def _make_user(email: str = "u@example.com") -> uuid.UUID:
    async with session_scope() as session:
        u = User(
            email=email,
            full_name="U",
            hashed_password="x",
            auth_method=AuthMethod.LOCAL,
            role="",
            is_active=True,
        )
        session.add(u)
        await session.commit()
        await session.refresh(u)
        return u.id


@pytest.mark.asyncio
async def test_allocate_uid_empty_returns_uid_min():
    uid_user = await _make_user()
    uid = await allocate_uid(uid_user, "alpha")
    assert uid == 10000


@pytest.mark.asyncio
async def test_allocate_uid_fills_smallest_gap():
    uid_user = await _make_user()
    async with session_scope() as session:
        session.add(UidAllocation(uid=10000, source="allocated", sam_account="a"))
        session.add(UidAllocation(uid=10002, source="allocated", sam_account="b"))
        await session.commit()
    uid = await allocate_uid(uid_user, "c")
    assert uid == 10001


@pytest.mark.asyncio
async def test_allocate_uid_contiguous_returns_max_plus_one():
    uid_user = await _make_user()
    async with session_scope() as session:
        for n in (10000, 10001, 10002):
            session.add(UidAllocation(uid=n, source="allocated", sam_account=f"u{n}"))
        await session.commit()
    uid = await allocate_uid(uid_user, "z")
    assert uid == 10003


@pytest.mark.asyncio
async def test_allocate_uid_range_exhausted_raises():
    uid_user = await _make_user()
    cfg = await ad_account_config.get()
    await ad_account_config.set(cfg.model_copy(update={"uid_min": 10, "uid_max": 11}))
    async with session_scope() as session:
        session.add(UidAllocation(uid=10, source="allocated", sam_account="a"))
        session.add(UidAllocation(uid=11, source="allocated", sam_account="b"))
        await session.commit()
    with pytest.raises(UidRangeExhausted):
        await allocate_uid(uid_user, "c")


@pytest.mark.asyncio
async def test_allocate_uid_writes_row_with_metadata():
    uid_user = await _make_user("metadata@example.com")
    uid = await allocate_uid(uid_user, "metaman")
    async with session_scope() as session:
        row = (await session.execute(select(UidAllocation).where(UidAllocation.uid == uid))).scalar_one()
    assert row.source == "allocated"
    assert row.user_id == uid_user
    assert row.sam_account == "metaman"


@pytest.mark.asyncio
async def test_allocate_uid_writes_audit_event():
    from not_dot_net.backend.audit import list_audit_events
    uid_user = await _make_user("audit@example.com")
    uid = await allocate_uid(uid_user, "audited")
    events = await list_audit_events(category="ad", action="allocate_uid")
    assert any(ev.target_id == str(uid_user) and str(uid) in str(ev.detail) for ev in events)
```

- [ ] **Step 3.2: Run tests, confirm they fail**

Run: `uv run pytest tests/test_uid_allocator.py -v`
Expected: `ImportError: cannot import name 'allocate_uid'` (or all six fail with AttributeError).

- [ ] **Step 3.3: Implement `allocate_uid`**

Append to `not_dot_net/backend/uid_allocator.py`:

```python
from sqlalchemy import literal


async def allocate_uid(user_id: uuid.UUID, sam_account: str) -> int:
    """Allocate the smallest free UID in the configured [uid_min, uid_max] range.

    Inserts a row marking the UID consumed; raises UidRangeExhausted if no free slot.
    """
    from not_dot_net.backend.db import session_scope
    from not_dot_net.backend.ad_account_config import ad_account_config
    from not_dot_net.backend.audit import audit_log

    cfg = await ad_account_config.get()
    lo, hi = cfg.uid_min, cfg.uid_max

    async with session_scope() as session:
        # Smallest UID in range that has no row.
        # Strategy: SELECT the smallest n in [lo, hi] such that n not in allocations.
        # Cross-DB portable approach: pull existing UIDs in range, find first gap in Python.
        # Range size is bounded (typically <100k), this is fine and avoids dialect-specific SQL.
        rows = (await session.execute(
            select(UidAllocation.uid).where(
                UidAllocation.uid >= lo, UidAllocation.uid <= hi,
            ).order_by(UidAllocation.uid.asc())
        )).scalars().all()

        used = set(rows)
        chosen: int | None = None
        for n in range(lo, hi + 1):
            if n not in used:
                chosen = n
                break
        if chosen is None:
            raise UidRangeExhausted(f"No free UID in [{lo}, {hi}]")

        session.add(UidAllocation(
            uid=chosen, source="allocated",
            user_id=user_id, sam_account=sam_account,
        ))
        await session.commit()

    await audit_log(
        category="ad", action="allocate_uid",
        actor_id=None, target_id=str(user_id),
        detail={"uid": chosen, "sam": sam_account},
    )
    return chosen
```

Note: the Python-side gap scan is intentionally simple and dialect-portable. For ranges this size (~50k integers), the in-memory scan is negligible. If the range later grows to millions, swap in the SQL gap-finder from the spec.

- [ ] **Step 3.4: Verify `audit_log` signature**

Run: `grep -n "^async def audit_log\|^def audit_log" not_dot_net/backend/audit.py`
Match arg names (`category`, `action`, `actor_id`, `target_id`, `detail`) to what the helper actually takes — adjust the call above if signatures differ (e.g. some projects use `actor` and `target` user objects).

- [ ] **Step 3.5: Run tests**

Run: `uv run pytest tests/test_uid_allocator.py -v`
Expected: all 6 pass.

- [ ] **Step 3.6: Commit**

```bash
git add not_dot_net/backend/uid_allocator.py tests/test_uid_allocator.py
git commit -m "feat(uid): allocate_uid picks smallest free in range + audit"
```

---

## Task 4: `seed_from_ad`

**Files:**
- Modify: `not_dot_net/backend/uid_allocator.py`
- Modify: `tests/test_uid_allocator.py`

- [ ] **Step 4.1: Write the failing tests**

Append to `tests/test_uid_allocator.py`:

```python
class _FakeEntry:
    def __init__(self, uid_number, sam):
        from types import SimpleNamespace
        self.uidNumber = SimpleNamespace(value=uid_number)
        self.sAMAccountName = SimpleNamespace(value=sam)
        self.entry_dn = f"CN={sam},OU=Users,DC=example,DC=com"


class _FakeConn:
    def __init__(self, entries):
        self.entries = entries
        self.bound = True

    def search(self, *args, **kwargs):
        return True

    def unbind(self):
        self.bound = False


def _fake_connect_factory(entries):
    def _connect(cfg, username, password):
        return _FakeConn(entries)
    return _connect


@pytest.mark.asyncio
async def test_seed_from_ad_inserts_seeded_rows(monkeypatch):
    from not_dot_net.backend.uid_allocator import seed_from_ad

    entries = [_FakeEntry(20000, "alice"), _FakeEntry(20001, "bob")]
    monkeypatch.setattr(
        "not_dot_net.backend.uid_allocator._search_ad_uids",
        lambda cfg, user, pw: entries,
    )
    result = await seed_from_ad("admin", "secret")
    assert result.seeded == 2
    assert result.skipped == 0

    async with session_scope() as session:
        rows = (await session.execute(select(UidAllocation))).scalars().all()
    assert {r.uid for r in rows} == {20000, 20001}
    assert all(r.source == "seeded_from_ad" for r in rows)


@pytest.mark.asyncio
async def test_seed_from_ad_is_idempotent(monkeypatch):
    from not_dot_net.backend.uid_allocator import seed_from_ad

    entries = [_FakeEntry(30000, "x"), _FakeEntry(30001, "y")]
    monkeypatch.setattr(
        "not_dot_net.backend.uid_allocator._search_ad_uids",
        lambda cfg, user, pw: entries,
    )
    first = await seed_from_ad("admin", "secret")
    second = await seed_from_ad("admin", "secret")
    assert first.seeded == 2 and first.skipped == 0
    assert second.seeded == 0 and second.skipped == 2
```

- [ ] **Step 4.2: Run, confirm failure**

Run: `uv run pytest tests/test_uid_allocator.py -v -k seed`
Expected: ImportError on `seed_from_ad`.

- [ ] **Step 4.3: Implement `seed_from_ad`**

Append to `not_dot_net/backend/uid_allocator.py`:

```python
@dataclass(frozen=True)
class SeedResult:
    seeded: int
    skipped: int


def _search_ad_uids(ldap_cfg, bind_username: str, bind_password: str):
    """Bind and paged-search AD for entries with uidNumber. Returns list of ldap3 entries.

    Wrapped in its own function so tests can monkeypatch it.
    """
    from ldap3 import SUBTREE
    from not_dot_net.backend.auth.ldap import _ldap_bind, get_ldap_connect

    conn = _ldap_bind(bind_username, bind_password, ldap_cfg, get_ldap_connect())
    try:
        ok = conn.search(
            search_base=ldap_cfg.base_dn,
            search_filter="(&(objectClass=user)(uidNumber=*))",
            search_scope=SUBTREE,
            attributes=["uidNumber", "sAMAccountName"],
            paged_size=500,
        )
        if not ok:
            return []
        return list(conn.entries)
    finally:
        conn.unbind()


async def seed_from_ad(bind_username: str, bind_password: str) -> SeedResult:
    """Lock all existing AD UIDs into the allocation table. Idempotent."""
    from not_dot_net.backend.db import session_scope
    from not_dot_net.backend.ad_account_config import ad_account_config
    from not_dot_net.backend.auth.ldap import ldap_config
    from not_dot_net.backend.audit import audit_log

    ldap_cfg = await ldap_config.get()
    _ = await ad_account_config.get()  # ensure section materialized

    entries = _search_ad_uids(ldap_cfg, bind_username, bind_password)
    seeded = 0
    skipped = 0
    async with session_scope() as session:
        existing = set(
            (await session.execute(select(UidAllocation.uid))).scalars().all()
        )
        for entry in entries:
            uid_val = entry.uidNumber.value
            if uid_val is None:
                continue
            uid_int = int(uid_val)
            if uid_int in existing:
                skipped += 1
                continue
            sam = entry.sAMAccountName.value if entry.sAMAccountName.value else None
            session.add(UidAllocation(
                uid=uid_int,
                source="seeded_from_ad",
                user_id=None,
                sam_account=sam,
            ))
            existing.add(uid_int)
            seeded += 1
        await session.commit()

    await audit_log(
        category="ad", action="seed_uids",
        actor_id=None, target_id=None,
        detail={"seeded": seeded, "skipped": skipped},
    )
    return SeedResult(seeded=seeded, skipped=skipped)
```

- [ ] **Step 4.4: Run tests**

Run: `uv run pytest tests/test_uid_allocator.py -v`
Expected: all tests pass (originals + 2 new).

- [ ] **Step 4.5: Commit**

```bash
git add not_dot_net/backend/uid_allocator.py tests/test_uid_allocator.py
git commit -m "feat(uid): seed_from_ad locks existing AD UIDs (idempotent)"
```

---

## Task 5: `list_allocations` + `UidAllocationView`

**Files:**
- Modify: `not_dot_net/backend/uid_allocator.py`
- Modify: `tests/test_uid_allocator.py`

- [ ] **Step 5.1: Write the failing test**

Append to `tests/test_uid_allocator.py`:

```python
@pytest.mark.asyncio
async def test_list_allocations_returns_views_desc_by_acquired():
    from not_dot_net.backend.uid_allocator import list_allocations
    uid_user = await _make_user("list@example.com")
    await allocate_uid(uid_user, "first")
    await allocate_uid(uid_user, "second")
    views = await list_allocations(limit=10)
    assert len(views) >= 2
    # Most recent first
    assert views[0].acquired_at >= views[1].acquired_at
    assert all(hasattr(v, "uid") and hasattr(v, "sam_account") for v in views)
```

- [ ] **Step 5.2: Implement `list_allocations`**

Append to `not_dot_net/backend/uid_allocator.py`:

```python
async def list_allocations(*, limit: int = 200) -> list[UidAllocationView]:
    from not_dot_net.backend.db import session_scope

    async with session_scope() as session:
        rows = (await session.execute(
            select(UidAllocation).order_by(UidAllocation.acquired_at.desc()).limit(limit)
        )).scalars().all()
    return [
        UidAllocationView(
            uid=r.uid, source=r.source, user_id=r.user_id,
            sam_account=r.sam_account, acquired_at=r.acquired_at, note=r.note,
        )
        for r in rows
    ]
```

- [ ] **Step 5.3: Run tests**

Run: `uv run pytest tests/test_uid_allocator.py -v`
Expected: all tests pass.

- [ ] **Step 5.4: Commit**

```bash
git add not_dot_net/backend/uid_allocator.py tests/test_uid_allocator.py
git commit -m "feat(uid): list_allocations for admin UI"
```

---

## Task 6: `ldap_user_exists_by_sam`

**Files:**
- Modify: `not_dot_net/backend/auth/ldap.py` (after `ldap_set_account_enabled`)
- Create: `tests/test_ldap_create_user.py`

- [ ] **Step 6.1: Write the failing test**

Create `tests/test_ldap_create_user.py`:

```python
import pytest
from ldap3 import MODIFY_REPLACE, MODIFY_ADD, MODIFY_DELETE


class _Result(dict):
    def __init__(self):
        super().__init__({"description": "success", "message": ""})


class _FakeEntry:
    def __init__(self, attrs):
        from types import SimpleNamespace
        for k, v in attrs.items():
            setattr(self, k, SimpleNamespace(value=v))
        self.entry_dn = attrs.get("_dn", "CN=fake,DC=x")


class _FakeConn:
    def __init__(self, search_returns_entries=None, add_ok=True, modify_ok=True):
        self.search_returns = list(search_returns_entries or [])
        self.entries = []
        self.calls = []  # list of (op_name, args)
        self.add_ok = add_ok
        self.modify_ok = modify_ok
        self.result = _Result()
        self.bound = True

    def search(self, *args, **kwargs):
        self.calls.append(("search", (args, kwargs)))
        self.entries = self.search_returns
        return bool(self.search_returns)

    def add(self, dn, object_class, attributes):
        self.calls.append(("add", (dn, object_class, attributes)))
        self.result = _Result()
        if not self.add_ok:
            self.result["description"] = "alreadyExists"
            self.result["message"] = "exists"
        return self.add_ok

    def modify(self, dn, changes):
        self.calls.append(("modify", (dn, changes)))
        self.result = _Result()
        if not self.modify_ok:
            self.result["description"] = "constraintViolation"
            self.result["message"] = "nope"
        return self.modify_ok

    def unbind(self):
        self.bound = False


def _fake_connect_returning(conn):
    def _connect(cfg, username, password):
        return conn
    return _connect


def test_ldap_user_exists_by_sam_true():
    from not_dot_net.backend.auth.ldap import ldap_user_exists_by_sam, LdapConfig
    conn = _FakeConn(search_returns_entries=[_FakeEntry({"sAMAccountName": "alice"})])
    cfg = LdapConfig(base_dn="DC=x,DC=y")
    assert ldap_user_exists_by_sam("alice", "admin", "pw", cfg, _fake_connect_returning(conn)) is True


def test_ldap_user_exists_by_sam_false():
    from not_dot_net.backend.auth.ldap import ldap_user_exists_by_sam, LdapConfig
    conn = _FakeConn(search_returns_entries=[])
    cfg = LdapConfig(base_dn="DC=x,DC=y")
    assert ldap_user_exists_by_sam("nope", "admin", "pw", cfg, _fake_connect_returning(conn)) is False
```

- [ ] **Step 6.2: Run, confirm failure**

Run: `uv run pytest tests/test_ldap_create_user.py::test_ldap_user_exists_by_sam_true -v`
Expected: ImportError on `ldap_user_exists_by_sam`.

- [ ] **Step 6.3: Implement**

Append to `not_dot_net/backend/auth/ldap.py` (after `ldap_set_account_enabled`):

```python
def ldap_user_exists_by_sam(
    sam: str,
    bind_username: str,
    bind_password: str,
    ldap_cfg: LdapConfig,
    connect: Callable[..., Connection] = default_ldap_connect,
) -> bool:
    """Return True if a user with this sAMAccountName exists in AD."""
    conn = _ldap_bind(bind_username, bind_password, ldap_cfg, connect)
    try:
        ok = conn.search(
            ldap_cfg.base_dn,
            f"(sAMAccountName={sam})",
            attributes=["sAMAccountName"],
        )
        return bool(ok and conn.entries)
    finally:
        conn.unbind()
```

- [ ] **Step 6.4: Run tests**

Run: `uv run pytest tests/test_ldap_create_user.py -v`
Expected: both new tests pass.

- [ ] **Step 6.5: Commit**

```bash
git add not_dot_net/backend/auth/ldap.py tests/test_ldap_create_user.py
git commit -m "feat(ldap): ldap_user_exists_by_sam precheck"
```

---

## Task 7: `ldap_create_user` primitive

**Files:**
- Modify: `not_dot_net/backend/auth/ldap.py`
- Modify: `tests/test_ldap_create_user.py`

- [ ] **Step 7.1: Write the failing tests**

Append to `tests/test_ldap_create_user.py`:

```python
def _new_user_kwargs(**overrides):
    base = dict(
        sam_account="alice",
        given_name="Alice",
        surname="Smith",
        display_name="Alice Smith",
        mail="alice.smith@example.com",
        description="newcomer",
        ou_dn="OU=Users,DC=x,DC=y",
        uid_number=10000,
        gid_number=10000,
        login_shell="/bin/bash",
        home_directory="/home/alice",
        initial_password="Init!Pass1234",
        must_change_password=True,
    )
    base.update(overrides)
    return base


def test_ldap_create_user_happy_path():
    from not_dot_net.backend.auth.ldap import ldap_create_user, NewAdUser, LdapConfig
    conn = _FakeConn()
    cfg = LdapConfig(base_dn="DC=x,DC=y")
    new_user = NewAdUser(**_new_user_kwargs())

    dn = ldap_create_user(new_user, "admin", "pw", cfg, _fake_connect_returning(conn))
    assert dn == "CN=Alice Smith,OU=Users,DC=x,DC=y"

    ops = [c[0] for c in conn.calls]
    # Expected order: add → modify (password) → modify (pwdLastSet=0) → modify (UAC=0x200)
    assert ops == ["add", "modify", "modify", "modify"]

    _, (added_dn, oc, attrs) = conn.calls[0]
    assert added_dn == dn
    assert set(["top", "person", "organizationalPerson", "user"]).issubset(set(oc))
    assert attrs["sAMAccountName"] == "alice"
    assert attrs["uidNumber"] == 10000
    assert attrs["gidNumber"] == 10000
    assert attrs["loginShell"] == "/bin/bash"
    assert attrs["unixHomeDirectory"] == "/home/alice"
    assert attrs["mail"] == "alice.smith@example.com"
    assert attrs["description"] == "newcomer"
    assert attrs["userAccountControl"] == "514"  # 0x202

    # Password is UTF-16LE quoted
    _, (_, pwd_changes) = conn.calls[1]
    pwd_value = pwd_changes["unicodePwd"][0][1][0]
    assert pwd_value == ('"Init!Pass1234"').encode("utf-16-le")

    # pwdLastSet=0
    _, (_, pls_changes) = conn.calls[2]
    assert pls_changes["pwdLastSet"][0][1] == ["0"]

    # Final UAC enable
    _, (_, uac_changes) = conn.calls[3]
    assert uac_changes["userAccountControl"][0][1] == ["512"]  # 0x200


def test_ldap_create_user_add_failure_raises_before_password():
    from not_dot_net.backend.auth.ldap import (
        ldap_create_user, NewAdUser, LdapConfig, LdapModifyError,
    )
    conn = _FakeConn(add_ok=False)
    cfg = LdapConfig(base_dn="DC=x,DC=y")
    with pytest.raises(LdapModifyError):
        ldap_create_user(NewAdUser(**_new_user_kwargs()), "a", "p", cfg, _fake_connect_returning(conn))
    assert [c[0] for c in conn.calls] == ["add"]


def test_ldap_create_user_password_failure_raises():
    from not_dot_net.backend.auth.ldap import (
        ldap_create_user, NewAdUser, LdapConfig, LdapModifyError,
    )
    conn = _FakeConn(modify_ok=False)
    cfg = LdapConfig(base_dn="DC=x,DC=y")
    with pytest.raises(LdapModifyError):
        ldap_create_user(NewAdUser(**_new_user_kwargs()), "a", "p", cfg, _fake_connect_returning(conn))
    assert [c[0] for c in conn.calls] == ["add", "modify"]  # add ok, password modify fails


def test_ldap_create_user_no_force_change_skips_pwdlastset():
    from not_dot_net.backend.auth.ldap import ldap_create_user, NewAdUser, LdapConfig
    conn = _FakeConn()
    cfg = LdapConfig(base_dn="DC=x,DC=y")
    new_user = NewAdUser(**_new_user_kwargs(must_change_password=False))
    ldap_create_user(new_user, "a", "p", cfg, _fake_connect_returning(conn))
    ops = [c[0] for c in conn.calls]
    # add → unicodePwd modify → UAC enable (no pwdLastSet)
    assert ops == ["add", "modify", "modify"]
```

- [ ] **Step 7.2: Run, confirm failure**

Run: `uv run pytest tests/test_ldap_create_user.py -v`
Expected: 4 new tests fail with ImportError on `ldap_create_user` / `NewAdUser`.

- [ ] **Step 7.3: Implement**

Append to `not_dot_net/backend/auth/ldap.py`:

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


def _ad_encode_password(plain: str) -> bytes:
    """AD requires unicodePwd as UTF-16LE of the quoted password."""
    return f'"{plain}"'.encode("utf-16-le")


_UAC_NORMAL_ACCOUNT = 0x200
_UAC_NORMAL_ACCOUNT_DISABLED = 0x202


def ldap_create_user(
    new_user: NewAdUser,
    bind_username: str,
    bind_password: str,
    ldap_cfg: LdapConfig,
    connect: Callable[..., Connection] = default_ldap_connect,
) -> str:
    """Create a new AD user and return its DN.

    Order: add disabled → set password → optionally set pwdLastSet=0 → enable account.
    Raises LdapModifyError on any failure.
    """
    dn = f"CN={new_user.display_name},{new_user.ou_dn}"
    object_class = ["top", "person", "organizationalPerson", "user"]
    attrs = {
        "sAMAccountName": new_user.sam_account,
        "userPrincipalName": f"{new_user.sam_account}@{ldap_cfg.domain}",
        "givenName": new_user.given_name,
        "sn": new_user.surname,
        "displayName": new_user.display_name,
        "cn": new_user.display_name,
        "mail": new_user.mail,
        "uidNumber": new_user.uid_number,
        "gidNumber": new_user.gid_number,
        "loginShell": new_user.login_shell,
        "unixHomeDirectory": new_user.home_directory,
        "userAccountControl": str(_UAC_NORMAL_ACCOUNT_DISABLED),
    }
    if new_user.description:
        attrs["description"] = new_user.description

    conn = _ldap_bind(bind_username, bind_password, ldap_cfg, connect)
    try:
        ok = conn.add(dn, object_class, attrs)
        if not ok:
            raise LdapModifyError(
                f"add failed: {conn.result.get('description')} ({conn.result.get('message')})"
            )

        ok = conn.modify(dn, {"unicodePwd": [(MODIFY_REPLACE, [_ad_encode_password(new_user.initial_password)])]})
        if not ok:
            raise LdapModifyError(
                f"set password failed: {conn.result.get('description')} ({conn.result.get('message')})"
            )

        if new_user.must_change_password:
            ok = conn.modify(dn, {"pwdLastSet": [(MODIFY_REPLACE, ["0"])]})
            if not ok:
                raise LdapModifyError(
                    f"pwdLastSet failed: {conn.result.get('description')} ({conn.result.get('message')})"
                )

        ok = conn.modify(dn, {"userAccountControl": [(MODIFY_REPLACE, [str(_UAC_NORMAL_ACCOUNT)])]})
        if not ok:
            raise LdapModifyError(
                f"enable failed: {conn.result.get('description')} ({conn.result.get('message')})"
            )
    finally:
        conn.unbind()
    return dn
```

- [ ] **Step 7.4: Run tests**

Run: `uv run pytest tests/test_ldap_create_user.py -v`
Expected: all tests pass.

- [ ] **Step 7.5: Commit**

```bash
git add not_dot_net/backend/auth/ldap.py tests/test_ldap_create_user.py
git commit -m "feat(ldap): ldap_create_user + NewAdUser dataclass"
```

---

## Task 8: `ldap_add_to_groups` / `ldap_remove_from_groups`

**Files:**
- Modify: `not_dot_net/backend/auth/ldap.py`
- Modify: `tests/test_ldap_create_user.py`

- [ ] **Step 8.1: Write the failing tests**

Append to `tests/test_ldap_create_user.py`:

```python
def test_ldap_add_to_groups_calls_modify_per_group():
    from not_dot_net.backend.auth.ldap import ldap_add_to_groups, LdapConfig
    conn = _FakeConn()
    cfg = LdapConfig(base_dn="DC=x")
    failures = ldap_add_to_groups(
        "CN=alice,DC=x",
        ["CN=g1,DC=x", "CN=g2,DC=x"],
        "admin", "pw", cfg, _fake_connect_returning(conn),
    )
    assert failures == {}
    assert [c[0] for c in conn.calls] == ["modify", "modify"]
    # Each modify uses MODIFY_ADD on member with the user DN
    for op, (gdn, changes) in conn.calls:
        assert "member" in changes
        action, value = changes["member"][0]
        assert action == MODIFY_ADD
        assert value == ["CN=alice,DC=x"]


def test_ldap_add_to_groups_collects_per_group_failures():
    from not_dot_net.backend.auth.ldap import ldap_add_to_groups, LdapConfig
    # Modify always fails — both groups fail, neither raises.
    conn = _FakeConn(modify_ok=False)
    cfg = LdapConfig(base_dn="DC=x")
    failures = ldap_add_to_groups(
        "CN=alice,DC=x",
        ["CN=g1,DC=x"],
        "admin", "pw", cfg, _fake_connect_returning(conn),
    )
    assert "CN=g1,DC=x" in failures
    assert "constraintViolation" in failures["CN=g1,DC=x"] or "nope" in failures["CN=g1,DC=x"]


def test_ldap_remove_from_groups_uses_modify_delete():
    from not_dot_net.backend.auth.ldap import ldap_remove_from_groups, LdapConfig
    conn = _FakeConn()
    cfg = LdapConfig(base_dn="DC=x")
    ldap_remove_from_groups(
        "CN=alice,DC=x", ["CN=g1,DC=x"], "admin", "pw", cfg, _fake_connect_returning(conn),
    )
    op, (gdn, changes) = conn.calls[0]
    action, value = changes["member"][0]
    assert action == MODIFY_DELETE
```

- [ ] **Step 8.2: Run, confirm failure**

Run: `uv run pytest tests/test_ldap_create_user.py -v -k groups`
Expected: ImportError on `ldap_add_to_groups`.

- [ ] **Step 8.3: Implement**

Append to `not_dot_net/backend/auth/ldap.py`:

```python
def _modify_group_member(
    op_kind,  # MODIFY_ADD or MODIFY_DELETE
    user_dn: str,
    group_dns: list[str],
    bind_username: str,
    bind_password: str,
    ldap_cfg: LdapConfig,
    connect: Callable[..., Connection] = default_ldap_connect,
) -> dict[str, str]:
    failures: dict[str, str] = {}
    if not group_dns:
        return failures
    conn = _ldap_bind(bind_username, bind_password, ldap_cfg, connect)
    try:
        for gdn in group_dns:
            ok = conn.modify(gdn, {"member": [(op_kind, [user_dn])]})
            if not ok:
                failures[gdn] = (
                    f"{conn.result.get('description')} ({conn.result.get('message')})"
                )
    finally:
        conn.unbind()
    return failures


def ldap_add_to_groups(
    user_dn: str,
    group_dns: list[str],
    bind_username: str,
    bind_password: str,
    ldap_cfg: LdapConfig,
    connect: Callable[..., Connection] = default_ldap_connect,
) -> dict[str, str]:
    """Add user_dn to each group's 'member' attribute. Returns {failed_group_dn: msg}."""
    return _modify_group_member(MODIFY_ADD, user_dn, group_dns, bind_username, bind_password, ldap_cfg, connect)


def ldap_remove_from_groups(
    user_dn: str,
    group_dns: list[str],
    bind_username: str,
    bind_password: str,
    ldap_cfg: LdapConfig,
    connect: Callable[..., Connection] = default_ldap_connect,
) -> dict[str, str]:
    """Remove user_dn from each group's 'member' attribute. Returns {failed_group_dn: msg}."""
    return _modify_group_member(MODIFY_DELETE, user_dn, group_dns, bind_username, bind_password, ldap_cfg, connect)
```

- [ ] **Step 8.4: Run tests**

Run: `uv run pytest tests/test_ldap_create_user.py -v`
Expected: all tests pass.

- [ ] **Step 8.5: Commit**

```bash
git add not_dot_net/backend/auth/ldap.py tests/test_ldap_create_user.py
git commit -m "feat(ldap): ldap_add_to_groups / ldap_remove_from_groups"
```

---

## Task 9: `ldap_list_groups` + `GroupSummary`

**Files:**
- Modify: `not_dot_net/backend/auth/ldap.py`
- Modify: `tests/test_ldap_create_user.py`

- [ ] **Step 9.1: Write the failing test**

Append to `tests/test_ldap_create_user.py`:

```python
def test_ldap_list_groups_returns_summaries():
    from not_dot_net.backend.auth.ldap import ldap_list_groups, LdapConfig
    entries = [
        _FakeEntry({"cn": "g1", "description": "team", "_dn": "CN=g1,OU=Groups,DC=x"}),
        _FakeEntry({"cn": "g2", "description": None, "_dn": "CN=g2,OU=Groups,DC=x"}),
    ]
    conn = _FakeConn(search_returns_entries=entries)
    cfg = LdapConfig(base_dn="DC=x,DC=y")
    groups = ldap_list_groups("admin", "pw", cfg, connect=_fake_connect_returning(conn))
    assert len(groups) == 2
    dns = {g.dn for g in groups}
    assert dns == {"CN=g1,OU=Groups,DC=x", "CN=g2,OU=Groups,DC=x"}
```

- [ ] **Step 9.2: Implement**

Append to `not_dot_net/backend/auth/ldap.py`:

```python
@dataclass(frozen=True)
class GroupSummary:
    dn: str
    cn: str
    description: str | None


def ldap_list_groups(
    bind_username: str,
    bind_password: str,
    ldap_cfg: LdapConfig,
    *,
    base_dn: str | None = None,
    connect: Callable[..., Connection] = default_ldap_connect,
) -> list[GroupSummary]:
    """Paged search for (objectClass=group). Returns [{dn, cn, description}]."""
    search_base = base_dn or ldap_cfg.base_dn
    conn = _ldap_bind(bind_username, bind_password, ldap_cfg, connect)
    try:
        ok = conn.search(
            search_base,
            "(objectClass=group)",
            attributes=["cn", "description"],
            paged_size=500,
        )
        if not ok:
            return []
        return [
            GroupSummary(
                dn=entry.entry_dn,
                cn=_attr_value(entry, "cn") or entry.entry_dn,
                description=_attr_value(entry, "description"),
            )
            for entry in conn.entries
        ]
    finally:
        conn.unbind()
```

- [ ] **Step 9.3: Run tests**

Run: `uv run pytest tests/test_ldap_create_user.py -v`
Expected: all tests pass.

- [ ] **Step 9.4: Commit**

```bash
git add not_dot_net/backend/auth/ldap.py tests/test_ldap_create_user.py
git commit -m "feat(ldap): ldap_list_groups for picker UIs"
```

---

## Task 10: Factor AD credentials prompt into a shared module

**Files:**
- Create: `not_dot_net/frontend/ad_credentials.py`
- Modify: `not_dot_net/frontend/directory.py:297-332` (delete local `_prompt_ad_credentials_then_save`, replace with import)

- [ ] **Step 10.1: Create the shared module**

Create `not_dot_net/frontend/ad_credentials.py`:

```python
"""Reusable AD admin credentials prompt dialog."""
from __future__ import annotations
from typing import Awaitable, Callable

from nicegui import ui

from not_dot_net.backend.auth.ldap import (
    ldap_config, get_ldap_connect, _ldap_bind, LdapModifyError, store_user_connection,
)
from not_dot_net.frontend.i18n import t


async def prompt_ad_credentials(current_user, on_bind: Callable[[str, str], Awaitable[None]]) -> None:
    """Show a credentials dialog. On successful bind, call on_bind(username, password).

    The bound connection is cached via store_user_connection.
    """
    dialog = ui.dialog()
    with dialog, ui.card():
        ui.label(t("confirm_password_to_save_ad"))
        username_input = ui.input(t("ad_admin_username")).props("outlined dense")
        password_input = ui.input(t("password"), password=True).props("outlined dense")
        error_label = ui.label("").classes("text-negative")

        async def submit():
            bind_user = username_input.value.strip()
            if not bind_user or not password_input.value:
                return
            cfg = await ldap_config.get()
            try:
                conn = _ldap_bind(bind_user, password_input.value, cfg, get_ldap_connect())
            except LdapModifyError as e:
                msg = str(e)
                error_label.set_text(
                    t("ad_bind_failed") if "bind" in msg.lower() else t("ad_write_failed", error=msg)
                )
                return
            store_user_connection(str(current_user.id), conn)
            dialog.close()
            await on_bind(bind_user, password_input.value)

        with ui.row():
            ui.button(t("submit"), on_click=submit).props("flat color=primary")
            ui.button(t("cancel"), on_click=dialog.close).props("flat")

    dialog.open()
```

- [ ] **Step 10.2: Replace the directory.py local function**

Open `not_dot_net/frontend/directory.py` and replace the local `_prompt_ad_credentials_then_save` definition (lines ~297-332) with a thin adapter:

```python
from not_dot_net.frontend.ad_credentials import prompt_ad_credentials


def _prompt_ad_credentials_then_save(person, current_user, save_callback):
    async def _on_bind(bind_user, bind_pw):
        # Existing save_callback expects ad_conn kwarg — fetch from cache.
        from not_dot_net.backend.auth.ldap import get_user_connection
        await save_callback(ad_conn=get_user_connection(str(current_user.id)))
    import asyncio
    asyncio.create_task(prompt_ad_credentials(current_user, _on_bind))
```

(Adjust the adapter to match the existing save_callback signature. If `directory.py:506` is the only call site, inline-replace it with a direct `prompt_ad_credentials` call instead and delete the adapter.)

- [ ] **Step 10.3: Run the full suite**

Run: `uv run pytest -x -q`
Expected: all existing tests pass (no behavior change).

- [ ] **Step 10.4: Commit**

```bash
git add not_dot_net/frontend/ad_credentials.py not_dot_net/frontend/directory.py
git commit -m "refactor(frontend): extract AD credentials prompt to shared module"
```

---

## Task 11: Effects framework — types + handlers + registry

**Files:**
- Create: `not_dot_net/backend/workflow_effects.py`
- Modify: `not_dot_net/config.py` (extend `WorkflowStepConfig` with `effects`)
- Create: `tests/test_workflow_effects.py`

- [ ] **Step 11.1: Add `effects` to WorkflowStepConfig**

Open `not_dot_net/config.py`. Locate `class WorkflowStepConfig(BaseModel):`. Add the field using a string forward reference:

```python
    effects: list["StepEffectConfig"] = Field(default_factory=list)
```

(No new import needed at the top — `StepEffectConfig` is resolved later via `model_rebuild()` called from `workflow_effects.py`. Pydantic v2 accepts string forward references; the model becomes usable once rebuild has run.)

- [ ] **Step 11.2: Write the failing tests**

Create `tests/test_workflow_effects.py`:

```python
import pytest
from unittest.mock import MagicMock


@pytest.mark.asyncio
async def test_step_effect_config_round_trip():
    from not_dot_net.backend.workflow_effects import StepEffectConfig
    cfg = StepEffectConfig(
        on_action="approve",
        kind="ad_add_to_groups",
        params={"groups": ["CN=vpn,DC=x"]},
    )
    assert cfg.model_dump()["kind"] == "ad_add_to_groups"


@pytest.mark.asyncio
async def test_registry_has_four_handlers():
    from not_dot_net.backend.workflow_effects import EFFECT_REGISTRY
    assert set(EFFECT_REGISTRY) == {
        "ad_add_to_groups",
        "ad_remove_from_groups",
        "ad_enable_account",
        "ad_disable_account",
    }


@pytest.mark.asyncio
async def test_add_to_groups_validates_against_eligible():
    from not_dot_net.backend.workflow_effects import EFFECT_REGISTRY
    from not_dot_net.backend.ad_account_config import ad_account_config
    cfg = await ad_account_config.get()
    await ad_account_config.set(cfg.model_copy(update={"eligible_groups": ["CN=ok,DC=x"]}))

    handler = EFFECT_REGISTRY["ad_add_to_groups"]
    handler.validate_params({"groups": ["CN=ok,DC=x"]})
    with pytest.raises(ValueError):
        handler.validate_params({"groups": ["CN=not-listed,DC=x"]})


@pytest.mark.asyncio
async def test_add_to_groups_runs_against_target(monkeypatch):
    from not_dot_net.backend.workflow_effects import EFFECT_REGISTRY, EffectResult
    from not_dot_net.backend.ad_account_config import ad_account_config
    cfg = await ad_account_config.get()
    await ad_account_config.set(cfg.model_copy(update={"eligible_groups": ["CN=ok,DC=x"]}))

    captured = {}

    def fake_add(user_dn, group_dns, bu, bp, lc):
        captured["user_dn"] = user_dn
        captured["group_dns"] = group_dns
        return {}

    monkeypatch.setattr("not_dot_net.backend.workflow_effects._ldap_add_to_groups", fake_add)

    # Make a target user with an LDAP DN
    from not_dot_net.backend.db import session_scope, User, AuthMethod
    async with session_scope() as session:
        u = User(
            email="target@example.com", full_name="Target", hashed_password="x",
            auth_method=AuthMethod.LDAP, role="", is_active=True,
            ldap_dn="CN=target,DC=x",
        )
        session.add(u)
        await session.commit()

    request = MagicMock(target_email="target@example.com")
    step = MagicMock()
    actor = MagicMock()
    handler = EFFECT_REGISTRY["ad_add_to_groups"]
    result = await handler.run(
        request, step, action="approve",
        params={"groups": ["CN=ok,DC=x"]},
        ad_creds=("admin", "pw"),
        actor=actor,
    )
    assert isinstance(result, EffectResult)
    assert result.succeeded
    assert captured["user_dn"] == "CN=target,DC=x"
    assert captured["group_dns"] == ["CN=ok,DC=x"]


@pytest.mark.asyncio
async def test_add_to_groups_partial_failure_returned():
    from not_dot_net.backend.workflow_effects import EFFECT_REGISTRY
    from not_dot_net.backend.ad_account_config import ad_account_config
    cfg = await ad_account_config.get()
    await ad_account_config.set(cfg.model_copy(update={"eligible_groups": ["CN=g1,DC=x", "CN=g2,DC=x"]}))

    import not_dot_net.backend.workflow_effects as we

    def fake_add(user_dn, group_dns, bu, bp, lc):
        return {"CN=g2,DC=x": "no rights"}
    we._ldap_add_to_groups = fake_add  # type: ignore

    from not_dot_net.backend.db import session_scope, User, AuthMethod
    async with session_scope() as session:
        u = User(email="t2@example.com", full_name="T", hashed_password="x",
                 auth_method=AuthMethod.LDAP, role="", is_active=True, ldap_dn="CN=t,DC=x")
        session.add(u); await session.commit()

    request = MagicMock(target_email="t2@example.com")
    handler = EFFECT_REGISTRY["ad_add_to_groups"]
    result = await handler.run(request, MagicMock(), action="approve",
                                params={"groups": ["CN=g1,DC=x", "CN=g2,DC=x"]},
                                ad_creds=("a", "p"), actor=MagicMock())
    assert not result.succeeded
    assert result.failures == {"CN=g2,DC=x": "no rights"}
```

- [ ] **Step 11.3: Implement**

Create `not_dot_net/backend/workflow_effects.py`:

```python
"""Generic 'workflow step → AD effect' framework.

Each WorkflowStepConfig may declare `effects: list[StepEffectConfig]`.
At step transition time, matching effects fire in declared order.
Failures are collected and audit-logged; they do not abort the chain.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from not_dot_net.backend.auth.ldap import (
    ldap_config,
    ldap_add_to_groups as _ldap_add_to_groups,
    ldap_remove_from_groups as _ldap_remove_from_groups,
    ldap_set_account_enabled as _ldap_set_account_enabled,
    LdapModifyError,
)
from not_dot_net.backend.ad_account_config import ad_account_config


class AdCredentialsRequired(Exception):
    """Raised by submit_step if effects need AD admin credentials and none were provided."""


class StepEffectConfig(BaseModel):
    on_action: str
    kind: Literal[
        "ad_add_to_groups",
        "ad_remove_from_groups",
        "ad_enable_account",
        "ad_disable_account",
    ]
    params: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class EffectResult:
    kind: str
    succeeded: bool
    detail: dict[str, Any] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)


async def _resolve_target_dn(request, target_key: str) -> str | None:
    """Resolve target spec to an LDAP DN. v1 supports 'target_person' only."""
    from not_dot_net.backend.db import session_scope, User
    from sqlalchemy import select, func

    if target_key != "target_person":
        return None
    if not request.target_email:
        return None
    async with session_scope() as session:
        u = (await session.execute(
            select(User).where(func.lower(User.email) == request.target_email.lower())
        )).scalar_one_or_none()
    return u.ldap_dn if u else None


class BaseEffectHandler:
    kind: ClassVar[str] = ""
    requires_ad_credentials: ClassVar[bool] = True

    def validate_params(self, params: dict) -> None:
        return None

    async def run(self, request, step, action, params, ad_creds, actor) -> EffectResult:
        raise NotImplementedError


class _GroupOpHandler(BaseEffectHandler):
    """Common base for add/remove from groups."""
    op_fn = None  # set by subclass

    async def _eligible_groups(self) -> list[str]:
        cfg = await ad_account_config.get()
        return list(cfg.eligible_groups)

    def validate_params(self, params: dict) -> None:
        groups = params.get("groups") or []
        if not isinstance(groups, list) or not all(isinstance(g, str) for g in groups):
            raise ValueError("params.groups must be a list of DNs")
        # Eligibility check is async, so do it at run-time too. Here, just type-check.

    async def run(self, request, step, action, params, ad_creds, actor) -> EffectResult:
        eligible = set(await self._eligible_groups())
        groups = params.get("groups") or []
        bad = [g for g in groups if g not in eligible]
        if bad:
            raise ValueError(f"groups not in eligible_groups: {bad}")

        target_key = params.get("target", "target_person")
        target_dn = await _resolve_target_dn(request, target_key)
        if not target_dn:
            return EffectResult(kind=self.kind, succeeded=False,
                                detail={"reason": "target has no ldap_dn", "target_key": target_key})

        bind_user, bind_pw = ad_creds
        cfg = await ldap_config.get()
        try:
            failures = self.op_fn(target_dn, groups, bind_user, bind_pw, cfg)  # type: ignore
        except LdapModifyError as e:
            return EffectResult(kind=self.kind, succeeded=False,
                                detail={"target_dn": target_dn, "groups": groups},
                                failures={"_bind": str(e)})
        return EffectResult(
            kind=self.kind,
            succeeded=not failures,
            detail={"target_dn": target_dn, "groups": groups},
            failures=failures,
        )


class AdAddToGroupsHandler(_GroupOpHandler):
    kind = "ad_add_to_groups"
    op_fn = staticmethod(_ldap_add_to_groups)


class AdRemoveFromGroupsHandler(_GroupOpHandler):
    kind = "ad_remove_from_groups"
    op_fn = staticmethod(_ldap_remove_from_groups)


class _EnableHandler(BaseEffectHandler):
    kind = "ad_enable_account"
    enable: ClassVar[bool] = True

    async def run(self, request, step, action, params, ad_creds, actor) -> EffectResult:
        target_dn = await _resolve_target_dn(request, params.get("target", "target_person"))
        if not target_dn:
            return EffectResult(kind=self.kind, succeeded=False,
                                detail={"reason": "target has no ldap_dn"})
        cfg = await ldap_config.get()
        bind_user, bind_pw = ad_creds
        try:
            _ldap_set_account_enabled(target_dn, self.enable, bind_user, bind_pw, cfg)
        except LdapModifyError as e:
            return EffectResult(kind=self.kind, succeeded=False,
                                detail={"target_dn": target_dn},
                                failures={"_modify": str(e)})
        return EffectResult(kind=self.kind, succeeded=True, detail={"target_dn": target_dn})


class AdEnableAccountHandler(_EnableHandler):
    kind = "ad_enable_account"
    enable = True


class AdDisableAccountHandler(_EnableHandler):
    kind = "ad_disable_account"
    enable = False


EFFECT_REGISTRY: dict[str, BaseEffectHandler] = {
    h.kind: h
    for h in [
        AdAddToGroupsHandler(),
        AdRemoveFromGroupsHandler(),
        AdEnableAccountHandler(),
        AdDisableAccountHandler(),
    ]
}


# Resolve the forward reference now that StepEffectConfig is defined in this module.
from not_dot_net.config import WorkflowStepConfig
WorkflowStepConfig.model_rebuild()
```

- [ ] **Step 11.3b: Ensure the rebuild fires early at startup**

Add to `not_dot_net/app.py` near the other config-section imports:

```python
from not_dot_net.backend import workflow_effects  # noqa: F401  # triggers WorkflowStepConfig.model_rebuild
```

This guarantees that any code constructing `WorkflowStepConfig(effects=[...])` (e.g. seed defaults in Task 17) does so against a rebuilt model.

- [ ] **Step 11.4: Run tests**

Run: `uv run pytest tests/test_workflow_effects.py -v`
Expected: all 5 tests pass.

- [ ] **Step 11.5: Commit**

```bash
git add not_dot_net/backend/workflow_effects.py not_dot_net/config.py tests/test_workflow_effects.py
git commit -m "feat(effects): workflow AD effects framework with four handlers"
```

---

## Task 12: `run_effects` + integration into `submit_step`

**Files:**
- Modify: `not_dot_net/backend/workflow_effects.py` (add `run_effects`)
- Modify: `not_dot_net/backend/workflow_service.py:submit_step` (call `run_effects`)
- Modify: `tests/test_workflow_effects.py`

- [ ] **Step 12.1: Write the failing tests**

Append to `tests/test_workflow_effects.py`:

```python
@pytest.mark.asyncio
async def test_run_effects_skips_non_matching_actions(monkeypatch):
    from not_dot_net.backend.workflow_effects import run_effects, StepEffectConfig

    calls = []

    async def fake_handler_run(self, request, step, action, params, ad_creds, actor):
        from not_dot_net.backend.workflow_effects import EffectResult
        calls.append((self.kind, action))
        return EffectResult(kind=self.kind, succeeded=True)

    monkeypatch.setattr(
        "not_dot_net.backend.workflow_effects.BaseEffectHandler.run", fake_handler_run,
    )
    step = MagicMock(effects=[
        StepEffectConfig(on_action="approve", kind="ad_enable_account", params={}),
        StepEffectConfig(on_action="reject", kind="ad_disable_account", params={}),
    ])
    results = await run_effects(
        request=MagicMock(), step=step, action="approve",
        ad_creds=("a", "p"), actor=MagicMock(),
    )
    assert len(results) == 1
    assert results[0].kind == "ad_enable_account"


@pytest.mark.asyncio
async def test_run_effects_raises_when_creds_missing():
    from not_dot_net.backend.workflow_effects import (
        run_effects, StepEffectConfig, AdCredentialsRequired,
    )
    step = MagicMock(effects=[
        StepEffectConfig(on_action="approve", kind="ad_enable_account", params={}),
    ])
    with pytest.raises(AdCredentialsRequired):
        await run_effects(
            request=MagicMock(), step=step, action="approve",
            ad_creds=None, actor=MagicMock(),
        )


@pytest.mark.asyncio
async def test_run_effects_unknown_kind_records_failure(monkeypatch):
    from not_dot_net.backend.workflow_effects import run_effects, StepEffectConfig
    # Pydantic validates kind, but a persisted bad value can sneak through if config edited externally.
    step = MagicMock()
    # Construct a "raw" effect with an invalid kind by bypassing validation.
    raw = StepEffectConfig.model_construct(on_action="approve", kind="ad_unknown_kind", params={})
    step.effects = [raw]
    results = await run_effects(
        request=MagicMock(), step=step, action="approve",
        ad_creds=("a", "p"), actor=MagicMock(),
    )
    assert results[0].succeeded is False
    assert "unknown effect kind" in str(results[0].failures.get("_kind", "")).lower()
```

- [ ] **Step 12.2: Implement `run_effects`**

Append to `not_dot_net/backend/workflow_effects.py`:

```python
async def run_effects(
    *,
    request,
    step,
    action: str,
    ad_creds: tuple[str, str] | None,
    actor,
) -> list[EffectResult]:
    """Fire all effects on this step whose on_action matches.

    Raises AdCredentialsRequired if any matching effect needs creds and none were given.
    Audit-logs each effect's outcome.
    """
    from not_dot_net.backend.audit import audit_log

    matching = [e for e in (getattr(step, "effects", None) or []) if e.on_action == action]
    if not matching:
        return []
    if any(EFFECT_REGISTRY.get(e.kind) and EFFECT_REGISTRY[e.kind].requires_ad_credentials for e in matching):
        if not ad_creds:
            raise AdCredentialsRequired(
                f"Step '{getattr(step, 'key', '?')}' action '{action}' requires AD admin credentials"
            )

    results: list[EffectResult] = []
    for effect in matching:
        handler = EFFECT_REGISTRY.get(effect.kind)
        if not handler:
            res = EffectResult(
                kind=effect.kind, succeeded=False,
                failures={"_kind": f"unknown effect kind: {effect.kind}"},
            )
            results.append(res)
            await audit_log(
                category="ad", action=effect.kind,
                actor_id=str(getattr(actor, "id", None)) if actor else None,
                target_id=None,
                detail={"unknown_kind": effect.kind, "params": effect.params},
            )
            continue
        try:
            res = await handler.run(request, step, action, effect.params, ad_creds, actor)
        except ValueError as e:
            res = EffectResult(kind=effect.kind, succeeded=False, failures={"_validation": str(e)})
        results.append(res)
        await audit_log(
            category="ad", action=effect.kind,
            actor_id=str(getattr(actor, "id", None)) if actor else None,
            target_id=None,
            detail={
                "succeeded": res.succeeded,
                "params": effect.params,
                "result_detail": res.detail,
                "failures": res.failures,
            },
        )
    return results
```

- [ ] **Step 12.3: Wire into `workflow_service.submit_step`**

Open `not_dot_net/backend/workflow_service.py`. Locate `submit_step(...)`. After the existing engine transition is validated and persisted (look for the `await session.commit()` that finalizes the event/transition — typically near the end of the function), and **before** the function returns success, insert:

```python
    # Fire any AD effects declared on the step for this action.
    if getattr(step_cfg, "effects", None):
        from not_dot_net.backend.workflow_effects import run_effects
        effect_results = await run_effects(
            request=request, step=step_cfg, action=action,
            ad_creds=ad_creds, actor=actor_user,
        )
        # Caller may inspect for non-fatal failures via the return value.
        return SubmitResult(request=request, effect_results=effect_results)
```

If `submit_step` currently returns a different shape, replace `SubmitResult` accordingly (or add a `SubmitResult` dataclass — match whatever the surrounding code uses). `ad_creds` must be added as a new kwarg to `submit_step`:

```python
async def submit_step(..., ad_creds: tuple[str, str] | None = None, ...):
```

- [ ] **Step 12.4: Run tests**

Run: `uv run pytest tests/test_workflow_effects.py -v`
Expected: all tests pass.

Run: `uv run pytest -x -q`
Expected: full suite passes; no regression in existing `submit_step` callers (the new kwarg defaults to `None`).

- [ ] **Step 12.5: Commit**

```bash
git add not_dot_net/backend/workflow_effects.py not_dot_net/backend/workflow_service.py tests/test_workflow_effects.py
git commit -m "feat(effects): run_effects + submit_step integration"
```

---

## Task 13: `ad_account_creation` step type — server handler

**Files:**
- Modify: `not_dot_net/backend/workflow_service.py` (add the dispatch + a new function `_handle_ad_account_creation`)
- Create: `tests/test_ad_account_creation.py`
- Modify: `not_dot_net/config.py` (add `"ad_account_creation"` to the step `type` literal if it's currently constrained)

- [ ] **Step 13.1: Loosen step `type` literal (if constrained)**

In `not_dot_net/config.py`, find `WorkflowStepConfig.type`. If it's `Literal["form", "approval"]`, change to:

```python
type: Literal["form", "approval", "ad_account_creation"] = "form"
```

If it's a free `str`, no change needed.

- [ ] **Step 13.2: Add helpers for derivation**

In `not_dot_net/backend/workflow_service.py` (or a small new module `not_dot_net/backend/ad_account_form.py` if you prefer it isolated — pick the smaller-file outcome), add:

```python
import unicodedata
import re
import secrets
import string


def _normalize_name(s: str) -> str:
    """Lowercase + accent-strip + drop non-alphanumeric."""
    if not s:
        return ""
    decomposed = unicodedata.normalize("NFKD", s)
    no_accent = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", no_accent.lower())


def derive_sam_candidates(first_name: str, last_name: str, max_steps: int = 5) -> list[str]:
    """Yield sAM candidates in cascading order: {last}, {last}{first[0]}, {last}{first[:2]}, …"""
    last = _normalize_name(last_name)
    first = _normalize_name(first_name)
    candidates = [last]
    for i in range(1, min(len(first), max_steps) + 1):
        candidates.append(f"{last}{first[:i]}")
    # Deduplicate while preserving order
    seen, out = set(), []
    for c in candidates:
        if c and c not in seen:
            seen.add(c); out.append(c)
    return out


def render_mail(template: str, first_name: str, last_name: str) -> str:
    return template.format(first=_normalize_name(first_name), last=_normalize_name(last_name))


def render_home(template: str, sam: str) -> str:
    return template.format(sam=sam)


def generate_initial_password(length: int = 16) -> str:
    """Strong password with at least one upper, lower, digit, symbol — passes AD complexity."""
    alpha = string.ascii_letters
    digits = string.digits
    symbols = "!@#$%^&*-_=+"
    pool = alpha + digits + symbols
    while True:
        pwd = "".join(secrets.choice(pool) for _ in range(length))
        if (any(c.islower() for c in pwd) and any(c.isupper() for c in pwd)
                and any(c.isdigit() for c in pwd) and any(c in symbols for c in pwd)):
            return pwd
```

- [ ] **Step 13.3: Write failing tests for the helpers**

Create `tests/test_ad_account_creation.py`:

```python
import pytest


def test_derive_sam_cascade():
    from not_dot_net.backend.workflow_service import derive_sam_candidates
    assert derive_sam_candidates("Alice", "Smith")[:3] == ["smith", "smitha", "smithal"]


def test_derive_sam_strips_accents():
    from not_dot_net.backend.workflow_service import derive_sam_candidates
    assert derive_sam_candidates("Éloïse", "Béranger")[0] == "beranger"


def test_render_mail_uses_template():
    from not_dot_net.backend.workflow_service import render_mail
    assert render_mail("{first}.{last}@x.y", "Alice", "Smith") == "alice.smith@x.y"


def test_generate_initial_password_meets_complexity():
    from not_dot_net.backend.workflow_service import generate_initial_password
    pw = generate_initial_password(16)
    assert len(pw) == 16
    assert any(c.islower() for c in pw)
    assert any(c.isupper() for c in pw)
    assert any(c.isdigit() for c in pw)
```

- [ ] **Step 13.4: Run, confirm pass (helpers are already implemented)**

Run: `uv run pytest tests/test_ad_account_creation.py -v`
Expected: all 4 helper tests pass.

- [ ] **Step 13.5: Add the server-side submit handler**

Continue in `not_dot_net/backend/workflow_service.py`. Add:

```python
@dataclass(frozen=True)
class AdAccountCreationResult:
    request_id: uuid.UUID
    new_dn: str
    sam_account: str
    uid: int
    initial_password: str
    group_failures: dict[str, str]


async def _handle_ad_account_creation(
    request,
    form_data: dict,
    ad_creds: tuple[str, str],
    actor_user,
) -> AdAccountCreationResult:
    """Allocate UID → create AD user → write back → apply groups.

    Raises on AD create failure (step stays pending). Group-add failures are returned, not raised.
    """
    from not_dot_net.backend.uid_allocator import allocate_uid
    from not_dot_net.backend.ad_account_config import ad_account_config
    from not_dot_net.backend.auth.ldap import (
        ldap_config, ldap_user_exists_by_sam, ldap_create_user, ldap_add_to_groups,
        NewAdUser, LdapModifyError, get_ldap_connect,
    )
    from not_dot_net.backend.db import session_scope, User
    from not_dot_net.backend.audit import audit_log
    from sqlalchemy import func, select

    ad_cfg = await ad_account_config.get()
    ldap_cfg = await ldap_config.get()
    bind_user, bind_pw = ad_creds

    sam = form_data["sam_account"].strip()
    if ldap_user_exists_by_sam(sam, bind_user, bind_pw, ldap_cfg, get_ldap_connect()):
        raise ValueError(f"sAMAccountName already exists in AD: {sam}")

    ou_dn = form_data["ou_dn"]
    if ou_dn not in ad_cfg.users_ous:
        raise ValueError(f"OU not in eligible list: {ou_dn}")

    chosen_groups = list(form_data.get("groups") or [])
    bad_groups = [g for g in chosen_groups if g not in ad_cfg.eligible_groups]
    if bad_groups:
        raise ValueError(f"groups not in eligible_groups: {bad_groups}")

    # Resolve target User
    async with session_scope() as session:
        target = (await session.execute(
            select(User).where(func.lower(User.email) == (request.target_email or "").lower())
        )).scalar_one_or_none()
    if not target:
        raise ValueError(f"No local User for target_email={request.target_email!r}")

    # Allocate UID (commits a row).
    uid = await allocate_uid(target.id, sam)

    first = form_data["first_name"]
    last = form_data["last_name"]
    display_name = form_data.get("display_name") or f"{first} {last}"
    initial_password = generate_initial_password(ad_cfg.password_length)

    new_user = NewAdUser(
        sam_account=sam,
        given_name=first,
        surname=last,
        display_name=display_name,
        mail=form_data["mail"],
        description=form_data.get("description"),
        ou_dn=ou_dn,
        uid_number=uid,
        gid_number=int(form_data.get("gid_number") or ad_cfg.default_gid_number),
        login_shell=form_data.get("login_shell") or ad_cfg.default_login_shell,
        home_directory=form_data["home_directory"],
        initial_password=initial_password,
        must_change_password=True,
    )
    try:
        new_dn = ldap_create_user(new_user, bind_user, bind_pw, ldap_cfg, get_ldap_connect())
    except LdapModifyError as e:
        await audit_log(
            category="ad", action="create_user",
            actor_id=str(actor_user.id) if actor_user else None,
            target_id=str(target.id),
            detail={"sam": sam, "uid": uid, "error": str(e), "succeeded": False},
        )
        raise

    # Mirror back to local User.
    async with session_scope() as session:
        u = await session.get(User, target.id)
        if u is not None:
            u.ldap_dn = new_dn
            u.ldap_username = sam
            u.uid_number = uid
            u.gid_number = new_user.gid_number
            u.mail = new_user.mail
            u.description = new_user.description
            u.is_active = True
            await session.commit()

    await audit_log(
        category="ad", action="create_user",
        actor_id=str(actor_user.id) if actor_user else None,
        target_id=str(target.id),
        detail={"sam": sam, "uid": uid, "dn": new_dn, "ou": ou_dn, "succeeded": True},
    )

    group_failures = {}
    if chosen_groups:
        group_failures = ldap_add_to_groups(new_dn, chosen_groups, bind_user, bind_pw, ldap_cfg, get_ldap_connect())
        await audit_log(
            category="ad", action="add_to_groups",
            actor_id=str(actor_user.id) if actor_user else None,
            target_id=str(target.id),
            detail={"groups": chosen_groups, "failures": group_failures},
        )

    # Notify the newcomer: their account is ready, here's how to log in.
    # Goes to the contact_email (where they were reached earlier in the workflow);
    # they'll be forced to change the temp password on first AD login.
    from not_dot_net.backend.mail import send_mail
    from not_dot_net.backend.notifications import render_email
    contact_email = (request.target_email or "").strip()
    if contact_email:
        subject, body = render_email(
            "account_created",
            workflow_label="Onboarding",
            sam=sam, initial_password=initial_password,
            display_name=display_name, mail=new_user.mail,
        )
        await send_mail(contact_email, subject, body)

    return AdAccountCreationResult(
        request_id=request.id, new_dn=new_dn, sam_account=sam,
        uid=uid, initial_password=initial_password, group_failures=group_failures,
    )
```

- [ ] **Step 13.5b: Register the `account_created` email template**

In `not_dot_net/backend/notifications.py`, locate the email template registry (search for `render_email` and the dict/match it consults). Add a new template `account_created` with EN + FR variants. EN sample:

```python
"account_created": {
    "subject": "{workflow_label}: your AD account is ready",
    "body": (
        "<p>Hello {display_name},</p>"
        "<p>Your account has been created. You can now log in with:</p>"
        "<ul>"
        "<li><strong>Login:</strong> {sam}</li>"
        "<li><strong>Initial password:</strong> {initial_password}</li>"
        "<li><strong>Email:</strong> {mail}</li>"
        "</ul>"
        "<p>You will be asked to change this password on first login.</p>"
    ),
},
```

FR sample:

```python
"account_created": {
    "subject": "{workflow_label} : votre compte AD est prêt",
    "body": (
        "<p>Bonjour {display_name},</p>"
        "<p>Votre compte a été créé. Vous pouvez maintenant vous connecter avec :</p>"
        "<ul>"
        "<li><strong>Identifiant :</strong> {sam}</li>"
        "<li><strong>Mot de passe initial :</strong> {initial_password}</li>"
        "<li><strong>Email :</strong> {mail}</li>"
        "</ul>"
        "<p>Il vous sera demandé de changer ce mot de passe à la première connexion.</p>"
    ),
},
```

Match the existing template format exactly — read the file first to see how other templates are structured (`render_email("token_link", ...)` is a known caller; mirror that shape).

- [ ] **Step 13.6: Dispatch in `submit_step`**

In `submit_step`, when the current step's `type == "ad_account_creation"` and `action == "complete"`, route the call through `_handle_ad_account_creation` before the standard engine transition is finalized. The simplest insertion point: at the top of `submit_step`, after resolving `step_cfg`:

```python
    if step_cfg.type == "ad_account_creation" and action == "complete":
        if not ad_creds:
            from not_dot_net.backend.workflow_effects import AdCredentialsRequired
            raise AdCredentialsRequired("ad_account_creation step requires AD admin credentials")
        result = await _handle_ad_account_creation(
            request=request, form_data=data or {}, ad_creds=ad_creds, actor_user=actor_user,
        )
        # Continue with the normal step transition below; `result` is attached to SubmitResult.
        # (Plumb `result` through whatever return shape the function already uses.)
```

Adjust `SubmitResult` (or whatever return type) to optionally carry `ad_account_creation: AdAccountCreationResult | None`.

- [ ] **Step 13.7: Write submit-flow integration tests**

Append to `tests/test_ad_account_creation.py`:

```python
@pytest.mark.asyncio
async def test_ad_account_creation_happy_submit(monkeypatch):
    """End-to-end: allocate UID, create user (mocked), mirror back to local User, advance step."""
    from not_dot_net.backend.workflow_service import _handle_ad_account_creation
    from not_dot_net.backend.ad_account_config import ad_account_config
    from not_dot_net.backend.db import session_scope, User, AuthMethod
    from sqlalchemy import select
    from unittest.mock import MagicMock

    cfg = await ad_account_config.get()
    await ad_account_config.set(cfg.model_copy(update={
        "users_ous": ["OU=Users,DC=x,DC=y"],
        "eligible_groups": ["CN=g1,DC=x,DC=y"],
    }))

    monkeypatch.setattr(
        "not_dot_net.backend.auth.ldap.ldap_user_exists_by_sam",
        lambda *a, **kw: False,
    )
    monkeypatch.setattr(
        "not_dot_net.backend.workflow_service.ldap_user_exists_by_sam",
        lambda *a, **kw: False,
        raising=False,
    )
    monkeypatch.setattr(
        "not_dot_net.backend.auth.ldap.ldap_create_user",
        lambda new_user, bu, bp, cfg, connect=None: f"CN={new_user.display_name},{new_user.ou_dn}",
    )
    monkeypatch.setattr(
        "not_dot_net.backend.workflow_service.ldap_create_user",
        lambda new_user, bu, bp, cfg, connect=None: f"CN={new_user.display_name},{new_user.ou_dn}",
        raising=False,
    )
    monkeypatch.setattr(
        "not_dot_net.backend.auth.ldap.ldap_add_to_groups",
        lambda *a, **kw: {},
    )
    monkeypatch.setattr(
        "not_dot_net.backend.workflow_service.ldap_add_to_groups",
        lambda *a, **kw: {},
        raising=False,
    )

    async with session_scope() as session:
        target = User(
            email="t@example.com", full_name="T", hashed_password="x",
            auth_method=AuthMethod.LOCAL, role="", is_active=False,
        )
        session.add(target); await session.commit(); await session.refresh(target)
        target_id = target.id

    request = MagicMock(target_email="t@example.com", id="req-1")
    form = {
        "first_name": "Alice", "last_name": "Smith",
        "sam_account": "smith", "ou_dn": "OU=Users,DC=x,DC=y",
        "mail": "alice.smith@x.y", "home_directory": "/home/smith",
        "groups": ["CN=g1,DC=x,DC=y"],
    }
    actor = MagicMock(id="actor-1")

    result = await _handle_ad_account_creation(request, form, ("admin", "pw"), actor)
    assert result.sam_account == "smith"
    assert result.uid == 10000
    assert result.group_failures == {}

    async with session_scope() as session:
        u = await session.get(User, target_id)
    assert u.uid_number == 10000
    assert u.ldap_dn == "CN=Alice Smith,OU=Users,DC=x,DC=y"
    assert u.is_active is True


@pytest.mark.asyncio
async def test_ad_account_creation_rejects_existing_sam(monkeypatch):
    from not_dot_net.backend.workflow_service import _handle_ad_account_creation
    from not_dot_net.backend.ad_account_config import ad_account_config
    from not_dot_net.backend.db import session_scope, User, AuthMethod
    from unittest.mock import MagicMock

    cfg = await ad_account_config.get()
    await ad_account_config.set(cfg.model_copy(update={
        "users_ous": ["OU=Users,DC=x,DC=y"], "eligible_groups": [],
    }))
    monkeypatch.setattr(
        "not_dot_net.backend.workflow_service.ldap_user_exists_by_sam",
        lambda *a, **kw: True, raising=False,
    )

    async with session_scope() as session:
        session.add(User(email="t2@example.com", full_name="T2", hashed_password="x",
                         auth_method=AuthMethod.LOCAL, role="", is_active=False))
        await session.commit()

    request = MagicMock(target_email="t2@example.com", id="req-2")
    form = {"first_name": "A", "last_name": "S", "sam_account": "taken",
            "ou_dn": "OU=Users,DC=x,DC=y", "mail": "a@b.c", "home_directory": "/h"}
    with pytest.raises(ValueError, match="already exists"):
        await _handle_ad_account_creation(request, form, ("a", "p"), MagicMock())


@pytest.mark.asyncio
async def test_ad_account_creation_group_failures_returned_not_raised(monkeypatch):
    from not_dot_net.backend.workflow_service import _handle_ad_account_creation
    from not_dot_net.backend.ad_account_config import ad_account_config
    from not_dot_net.backend.db import session_scope, User, AuthMethod
    from unittest.mock import MagicMock

    cfg = await ad_account_config.get()
    await ad_account_config.set(cfg.model_copy(update={
        "users_ous": ["OU=Users,DC=x"], "eligible_groups": ["CN=g,DC=x"],
    }))
    monkeypatch.setattr("not_dot_net.backend.workflow_service.ldap_user_exists_by_sam",
                        lambda *a, **kw: False, raising=False)
    monkeypatch.setattr("not_dot_net.backend.workflow_service.ldap_create_user",
                        lambda new, *a, **kw: f"CN=x,{new.ou_dn}", raising=False)
    monkeypatch.setattr("not_dot_net.backend.workflow_service.ldap_add_to_groups",
                        lambda *a, **kw: {"CN=g,DC=x": "denied"}, raising=False)

    async with session_scope() as session:
        session.add(User(email="t3@example.com", full_name="T3", hashed_password="x",
                         auth_method=AuthMethod.LOCAL, role="", is_active=False))
        await session.commit()

    request = MagicMock(target_email="t3@example.com", id="req-3")
    form = {"first_name": "A", "last_name": "S", "sam_account": "as",
            "ou_dn": "OU=Users,DC=x", "mail": "a@b.c", "home_directory": "/h",
            "groups": ["CN=g,DC=x"]}
    result = await _handle_ad_account_creation(request, form, ("a", "p"), MagicMock())
    assert result.group_failures == {"CN=g,DC=x": "denied"}
```

Note: the monkeypatches target both `not_dot_net.backend.auth.ldap.X` and `not_dot_net.backend.workflow_service.X` because the service does `from ... import X` (binds in its own namespace). Set `raising=False` on the workflow_service side until you confirm the import style — if you import the names inside `_handle_ad_account_creation` (as written above), drop the workflow_service patches and keep only the `auth.ldap` ones.

- [ ] **Step 13.8: Run tests**

Run: `uv run pytest tests/test_ad_account_creation.py -v`
Expected: all tests pass.

- [ ] **Step 13.9: Commit**

```bash
git add not_dot_net/backend/workflow_service.py not_dot_net/config.py tests/test_ad_account_creation.py
git commit -m "feat(workflow): ad_account_creation step type server handler"
```

---

## Task 14: Frontend create form

**Files:**
- Modify: `not_dot_net/frontend/workflow_step.py` (new render branch for `type=="ad_account_creation"`)
- Modify: `not_dot_net/frontend/workflow_detail.py` and/or `frontend/workflow_token.py` callers if they pass `ad_creds` to `submit_step`

- [ ] **Step 14.1: Add the form renderer**

In `not_dot_net/frontend/workflow_step.py`, inside `render_step_form` (or alongside it), add:

```python
async def _render_ad_account_creation_form(step, prefill, on_submit):
    """Bespoke form for the AD account creation step."""
    from not_dot_net.backend.ad_account_config import ad_account_config
    from not_dot_net.backend.workflow_service import (
        derive_sam_candidates, render_mail, render_home, _normalize_name,
    )
    from not_dot_net.frontend.i18n import t
    from nicegui import ui

    cfg = await ad_account_config.get()

    first = prefill.get("first_name", "")
    last = prefill.get("last_name", "")
    status = prefill.get("status", "")

    state = {
        "sam_account": (derive_sam_candidates(first, last) or [""])[0],
        "ou_dn": "",
        "gid_number": cfg.default_gid_number,
        "login_shell": cfg.default_login_shell,
        "home_directory": "",
        "mail": render_mail(cfg.mail_template, first, last),
        "description": "",
        "notes": "",
        "groups": list(cfg.default_groups_by_status.get(status, [])),
    }
    state["home_directory"] = render_home(cfg.home_directory_template, state["sam_account"])

    with ui.column().classes("w-full gap-3"):
        ui.label(f"{t('first_name')}: {first}").classes("text-sm")
        ui.label(f"{t('last_name')}: {last}").classes("text-sm")

        sam_input = ui.input(t("samaccountname"), value=state["sam_account"]).props("outlined dense stack-label")
        def _on_sam_change(e):
            state["sam_account"] = (e.value or "").strip()
            state["home_directory"] = render_home(cfg.home_directory_template, state["sam_account"])
            home_input.set_value(state["home_directory"])
        sam_input.on_value_change(_on_sam_change)

        ui.label(f"{t('uid')}: {t('uid_allocated_at_submit')}").classes("text-sm text-grey")

        gid_input = ui.number(t("primary_gid"), value=state["gid_number"]).props("outlined dense stack-label")
        gid_input.on_value_change(lambda e: state.update({"gid_number": int(e.value or cfg.default_gid_number)}))

        shell_input = ui.input(t("login_shell"), value=state["login_shell"]).props("outlined dense stack-label")
        shell_input.on_value_change(lambda e: state.update({"login_shell": e.value or cfg.default_login_shell}))

        home_input = ui.input(t("home_directory"), value=state["home_directory"]).props("outlined dense stack-label")
        home_input.on_value_change(lambda e: state.update({"home_directory": e.value or ""}))

        ou_select = ui.select(
            options={dn: dn for dn in cfg.users_ous},
            value=None, label=t("ou"),
        ).props("outlined dense stack-label")
        ou_select.on_value_change(lambda e: state.update({"ou_dn": e.value or ""}))

        mail_input = ui.input(t("mail"), value=state["mail"]).props("outlined dense stack-label")
        mail_input.on_value_change(lambda e: state.update({"mail": e.value or ""}))

        desc_input = ui.textarea(t("description"), value=state["description"]).props("outlined dense stack-label")
        desc_input.on_value_change(lambda e: state.update({"description": e.value or ""}))

        groups_select = ui.select(
            options={dn: dn for dn in cfg.eligible_groups},
            value=state["groups"], multiple=True, label=t("groups"),
        ).props("outlined dense stack-label use-chips")
        groups_select.on_value_change(lambda e: state.update({"groups": list(e.value or [])}))

        notes_input = ui.textarea(t("notes"), value=state["notes"]).props("outlined dense stack-label")
        notes_input.on_value_change(lambda e: state.update({"notes": e.value or ""}))

        async def submit():
            if not state["ou_dn"]:
                ui.notify(t("ou_required"), type="warning"); return
            payload = {
                **prefill,           # carry first/last/status through
                "sam_account": state["sam_account"],
                "ou_dn": state["ou_dn"],
                "gid_number": state["gid_number"],
                "login_shell": state["login_shell"],
                "home_directory": state["home_directory"],
                "mail": state["mail"],
                "description": state["description"],
                "groups": state["groups"],
                "notes": state["notes"],
                "first_name": first,
                "last_name": last,
            }
            await on_submit("complete", payload)

        ui.button(t("complete"), on_click=submit).props("color=primary")
```

In `render_step_form`, add a dispatch at the top:

```python
async def render_step_form(step, prefill, on_submit):
    if step.type == "ad_account_creation":
        return await _render_ad_account_creation_form(step, prefill, on_submit)
    # … existing logic …
```

- [ ] **Step 14.2: Wire credential prompt + temp-password reveal at the caller**

In `not_dot_net/frontend/workflow_detail.py`, the `handle_submit` (or whatever the existing in-app submit callback is named) for an `ad_account_creation` step needs to:

1. Call `prompt_ad_credentials(current_user, on_bind=...)` before invoking `submit_step`.
2. Pass `ad_creds=(bind_user, bind_pw)` to `submit_step`.
3. On success, if `result.ad_account_creation` is present, show a one-time copyable dialog with `result.ad_account_creation.initial_password`. Use a `ui.dialog` with a code label + a "Copy" `ui.button` that calls `ui.clipboard.write(...)`.
4. If `result.ad_account_creation.group_failures` is non-empty, `ui.notify` with a non-fatal warning listing failed groups.

Concretely, at the top of `handle_submit`:

```python
    if step_config.type == "ad_account_creation":
        from not_dot_net.frontend.ad_credentials import prompt_ad_credentials
        async def _on_bind(bind_user, bind_pw):
            try:
                result = await submit_step(..., ad_creds=(bind_user, bind_pw))
            except Exception as e:
                ui.notify(f"AD create failed: {e}", type="negative"); return
            ad_res = getattr(result, "ad_account_creation", None)
            if ad_res:
                _show_temp_password_dialog(ad_res.initial_password)
                if ad_res.group_failures:
                    failed = ", ".join(ad_res.group_failures)
                    ui.notify(t("group_add_failures", groups=failed), type="warning")
        await prompt_ad_credentials(current_user, _on_bind)
        return
```

And the dialog helper:

```python
def _show_temp_password_dialog(password: str):
    from nicegui import ui
    dlg = ui.dialog()
    with dlg, ui.card():
        ui.label(t("initial_password_copy_now")).classes("text-bold")
        code = ui.label(password).classes("font-mono text-lg p-2 bg-grey-2")
        def _copy():
            ui.run_javascript(f'navigator.clipboard.writeText({password!r})')
            ui.notify(t("copied"), type="positive")
        with ui.row():
            ui.button(t("copy"), on_click=_copy).props("color=primary")
            ui.button(t("close"), on_click=dlg.close).props("flat")
    dlg.open()
```

- [ ] **Step 14.3: Manual smoke test**

Run: `uv run python -m not_dot_net.cli serve --host localhost --port 8088`

Browse to a request sitting on `it_account_creation`. Confirm:
- form renders with all fields
- editing `sam_account` updates `home_directory` live
- OU dropdown shows the configured DNs
- clicking Complete prompts for AD credentials

(No need to wire AD; the bind will fail and surface an error, proving the credential prompt is firing.) Then stop the server.

- [ ] **Step 14.4: Commit**

```bash
git add not_dot_net/frontend/workflow_step.py not_dot_net/frontend/workflow_detail.py
git commit -m "feat(frontend): ad_account_creation form + temp-password dialog"
```

---

## Task 15: Workflow editor — step type entry + effects panel

**Files:**
- Modify: `not_dot_net/frontend/workflow_editor.py`
- Modify: `not_dot_net/frontend/workflow_editor_options.py`
- Modify: `tests/test_workflow_editor.py`

- [ ] **Step 15.1: Add effect-kind options helper**

In `not_dot_net/frontend/workflow_editor_options.py`, add:

```python
def effect_kind_options() -> list[dict]:
    """Labeled options for the four AD effect kinds. i18n-driven labels."""
    from not_dot_net.frontend.i18n import t
    return [
        {"value": "ad_add_to_groups", "label": t("effect_kind_ad_add_to_groups")},
        {"value": "ad_remove_from_groups", "label": t("effect_kind_ad_remove_from_groups")},
        {"value": "ad_enable_account", "label": t("effect_kind_ad_enable_account")},
        {"value": "ad_disable_account", "label": t("effect_kind_ad_disable_account")},
    ]
```

- [ ] **Step 15.2: Add the Effects table to the step editor**

In `not_dot_net/frontend/workflow_editor.py`, around the step-detail right pane (search for "Effects" placement — between Actions and Fields, as called out in the spec), add a new method analogous to `_render_notification_table`:

```python
def _render_effects_table(self, wf_key: str, step) -> None:
    from not_dot_net.frontend.widgets import chip_list_editor
    from not_dot_net.frontend.workflow_editor_options import effect_kind_options
    from not_dot_net.backend.workflow_effects import StepEffectConfig

    kind_opts = {o["value"]: o["label"] for o in effect_kind_options()}
    action_opts = {a: a for a in (step.actions or [])}

    if not step.effects:
        ui.label(t("empty_effects")).classes("text-grey text-sm")

    for idx, effect in enumerate(step.effects):
        with ui.row().classes("w-full items-center gap-2 no-wrap"):
            ui.select(
                options=action_opts, value=effect.on_action, label=t("on_action"),
                on_change=lambda e, i=idx, k=wf_key, sk=step.key:
                    self.set_effect_field(k, sk, i, "on_action", e.value or ""),
            ).props("dense outlined stack-label").classes("w-32")

            ui.select(
                options=kind_opts, value=effect.kind, label=t("kind"),
                on_change=lambda e, i=idx, k=wf_key, sk=step.key:
                    self.set_effect_field(k, sk, i, "kind", e.value or ""),
            ).props("dense outlined stack-label").classes("w-56")

            # Params renderer dispatches by kind.
            if effect.kind in ("ad_add_to_groups", "ad_remove_from_groups"):
                eligible = self._eligible_groups_snapshot
                chip_list_editor(
                    label=t("groups"),
                    options=eligible,
                    value=list(effect.params.get("groups", [])),
                    on_change=lambda v, i=idx, k=wf_key, sk=step.key:
                        self.set_effect_param(k, sk, i, "groups", v),
                )
            else:
                ui.label("—").classes("text-grey")

            ui.button(
                icon="delete",
                on_click=lambda i=idx, k=wf_key, sk=step.key: self.delete_effect(k, sk, i),
            ).props("flat dense round color=negative")

    ui.button(
        f"+ {t('add_effect')}",
        on_click=lambda k=wf_key, sk=step.key: self.add_effect(k, sk),
    ).props("flat dense color=primary")
```

Add the supporting state-mutation methods in the `WorkflowEditorDialog` class:

```python
def add_effect(self, wf_key: str, step_key: str) -> None:
    step = self._find_step(wf_key, step_key)
    if step is None: return
    step.effects = list(step.effects or []) + [
        StepEffectConfig(on_action=(step.actions or [""])[0] or "submit",
                         kind="ad_add_to_groups", params={"groups": []})
    ]
    self._dirty = True; self._rerender()

def delete_effect(self, wf_key: str, step_key: str, idx: int) -> None:
    step = self._find_step(wf_key, step_key)
    if step is None: return
    effs = list(step.effects or [])
    if 0 <= idx < len(effs):
        effs.pop(idx); step.effects = effs
        self._dirty = True; self._rerender()

def set_effect_field(self, wf_key, step_key, idx, field, value):
    step = self._find_step(wf_key, step_key)
    if step is None: return
    eff = step.effects[idx]
    setattr(eff, field, value)
    self._dirty = True; self._rerender()

def set_effect_param(self, wf_key, step_key, idx, key, value):
    step = self._find_step(wf_key, step_key)
    if step is None: return
    params = dict(step.effects[idx].params or {})
    params[key] = value
    step.effects[idx].params = params
    self._dirty = True; self._rerender()
```

`_find_step` and `_eligible_groups_snapshot` should match existing helpers in the editor — read `workflow_editor.py` first to follow naming conventions. `_eligible_groups_snapshot` should be loaded once when the editor opens via `await ad_account_config.get()` and stored on `self`.

Call `_render_effects_table` from the step-detail render method, between Actions and Fields.

- [ ] **Step 15.3: Add `ad_account_creation` to the type select**

In the step-editor's `type` select (search for "step.type" in workflow_editor.py), add the value:

```python
type_opts = {"form": t("step_type_form"), "approval": t("step_type_approval"),
             "ad_account_creation": t("step_type_ad_account_creation")}
```

When `step.type == "ad_account_creation"`, also:
- Hide/disable the Fields panel (replace with a banner: `ui.label(t("ad_account_creation_fields_locked")).classes("text-grey italic")`).
- Force `step.actions = ["complete"]` if not already.

- [ ] **Step 15.4: Extend `compute_warnings`**

Find `compute_warnings` in `workflow_editor.py`. Add two checks:

```python
    # Effects referencing unknown actions
    for step in wf.steps:
        valid_actions = set(step.actions or [])
        for eff in (step.effects or []):
            if eff.on_action not in valid_actions:
                warnings.append(t("warning_effect_unknown_action",
                                  step=step.key, action=eff.on_action))
            if eff.kind in ("ad_add_to_groups", "ad_remove_from_groups"):
                groups = eff.params.get("groups") or []
                bad = [g for g in groups if g not in self._eligible_groups_snapshot]
                if bad:
                    warnings.append(t("warning_effect_groups_not_eligible",
                                      step=step.key, groups=", ".join(bad)))
```

- [ ] **Step 15.5: Tests**

Append to `tests/test_workflow_editor.py`:

```python
def test_compute_warnings_flags_effect_unknown_action():
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    from not_dot_net.config import WorkflowConfig, WorkflowStepConfig
    from not_dot_net.backend.workflow_effects import StepEffectConfig

    step = WorkflowStepConfig(key="s", type="approval", actions=["approve"], effects=[
        StepEffectConfig(on_action="nonexistent", kind="ad_enable_account", params={}),
    ])
    wf = WorkflowConfig(label="x", steps=[step], notifications=[])
    dlg = WorkflowEditorDialog.__new__(WorkflowEditorDialog)
    dlg._eligible_groups_snapshot = {}
    warns = dlg.compute_warnings(wf)
    assert any("nonexistent" in w for w in warns)


def test_compute_warnings_flags_effect_groups_not_eligible():
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    from not_dot_net.config import WorkflowConfig, WorkflowStepConfig
    from not_dot_net.backend.workflow_effects import StepEffectConfig

    step = WorkflowStepConfig(key="s", type="approval", actions=["approve"], effects=[
        StepEffectConfig(on_action="approve", kind="ad_add_to_groups",
                         params={"groups": ["CN=rogue,DC=x"]}),
    ])
    wf = WorkflowConfig(label="x", steps=[step], notifications=[])
    dlg = WorkflowEditorDialog.__new__(WorkflowEditorDialog)
    dlg._eligible_groups_snapshot = {"CN=ok,DC=x": "ok"}
    warns = dlg.compute_warnings(wf)
    assert any("rogue" in w for w in warns)
```

If `compute_warnings` isn't an instance method (some implementations make it free-standing), adjust the test to call it as the actual code does.

- [ ] **Step 15.6: Run tests**

Run: `uv run pytest tests/test_workflow_editor.py -v`
Expected: all tests pass.

- [ ] **Step 15.7: Commit**

```bash
git add not_dot_net/frontend/workflow_editor.py not_dot_net/frontend/workflow_editor_options.py tests/test_workflow_editor.py
git commit -m "feat(editor): effects panel + ad_account_creation step type"
```

---

## Task 16: Settings → AD Accounts page

**Files:**
- Create: `not_dot_net/frontend/admin_ad_account.py`
- Modify: `not_dot_net/frontend/shell.py` (add the menu entry)

- [ ] **Step 16.1: Create the page**

Create `not_dot_net/frontend/admin_ad_account.py`:

```python
"""Admin page: AD Accounts settings + UID allocations table + Lock-from-AD button."""
from __future__ import annotations
from nicegui import ui

from not_dot_net.frontend.i18n import t
from not_dot_net.frontend.ad_credentials import prompt_ad_credentials
from not_dot_net.backend.permissions import check_permission, MANAGE_SETTINGS


async def render(current_user) -> None:
    try:
        await check_permission(current_user, MANAGE_SETTINGS)
    except PermissionError:
        ui.notify(t("permission_denied"), type="negative")
        return

    from not_dot_net.backend.ad_account_config import ad_account_config
    from not_dot_net.backend.uid_allocator import list_allocations, seed_from_ad
    from not_dot_net.frontend.admin_settings import _render_form

    ui.label(t("ad_accounts")).classes("text-h5 mb-3")

    cfg = await ad_account_config.get()
    await _render_form(ad_account_config, cfg)  # reuse existing settings form renderer

    ui.separator().classes("my-4")
    ui.label(t("lock_existing_ad_uids_intro")).classes("text-sm text-grey")

    async def _on_lock():
        async def _on_bind(bind_user, bind_pw):
            try:
                result = await seed_from_ad(bind_user, bind_pw)
            except Exception as e:
                ui.notify(str(e), type="negative"); return
            ui.notify(t("lock_existing_ad_uids_result",
                        seeded=result.seeded, skipped=result.skipped),
                      type="positive")
            await _refresh_table()
        await prompt_ad_credentials(current_user, _on_bind)

    ui.button(t("lock_existing_ad_uids"), on_click=_on_lock).props("color=primary")

    ui.separator().classes("my-4")
    ui.label(t("recent_uid_allocations")).classes("text-h6")
    table_container = ui.column().classes("w-full")

    async def _refresh_table():
        table_container.clear()
        rows = await list_allocations(limit=200)
        with table_container:
            ui.table(
                columns=[
                    {"name": "uid", "label": "UID", "field": "uid"},
                    {"name": "sam", "label": t("samaccountname"), "field": "sam_account"},
                    {"name": "source", "label": t("source"), "field": "source"},
                    {"name": "acquired_at", "label": t("acquired_at"), "field": "acquired_at"},
                ],
                rows=[{"uid": r.uid, "sam_account": r.sam_account or "",
                       "source": r.source, "acquired_at": r.acquired_at.isoformat()}
                      for r in rows],
            ).props("dense")

    await _refresh_table()
```

- [ ] **Step 16.2: Wire menu entry in shell**

In `not_dot_net/frontend/shell.py`, find the Settings menu/tab section. Add an entry that calls `admin_ad_account.render(current_user)`, gated on `MANAGE_SETTINGS` permission. Follow the surrounding pattern exactly.

- [ ] **Step 16.3: Smoke test**

Run: `uv run python -m not_dot_net.cli serve --host localhost --port 8088`
Browse to Settings → AD Accounts. Confirm:
- form renders all `AdAccountConfig` fields
- "Lock existing AD UIDs" button shows credentials prompt (bind will fail without real AD, that's fine — the prompt firing is what we're verifying)
- allocations table is empty initially, populated after manual `allocate_uid` via CLI

Stop the server.

- [ ] **Step 16.4: Commit**

```bash
git add not_dot_net/frontend/admin_ad_account.py not_dot_net/frontend/shell.py
git commit -m "feat(frontend): Settings → AD Accounts page"
```

---

## Task 17: Update default seeded workflows + demonstration effect on VPN

**Files:**
- Modify: `not_dot_net/backend/workflow_service.py` (the `WorkflowsConfig.workflows` defaults)

- [ ] **Step 17.1: Change onboarding's last step to the new type**

In `WorkflowsConfig.workflows["onboarding"].steps`, replace the existing `it_account_creation` step (currently `type="form"` with a `notes` field) with:

```python
WorkflowStepConfig(
    key="it_account_creation",
    type="ad_account_creation",
    assignee_permission="manage_users",
    fields=[],                # fields are hardcoded by the renderer
    actions=["complete"],
),
```

- [ ] **Step 17.2: Add a demonstration effect on VPN's existing approval step**

In `WorkflowsConfig.workflows["vpn_access"].steps`, the `approval` step gains:

```python
WorkflowStepConfig(
    key="approval",
    type="approval",
    assignee_role="director",
    assignee_permission="approve_workflows",
    actions=["approve", "reject"],
    effects=[
        StepEffectConfig(
            on_action="approve",
            kind="ad_add_to_groups",
            params={"groups": []},  # admin fills via the editor before relying on it
        ),
    ],
),
```

Add the import at the top of `workflow_service.py`:

```python
from not_dot_net.backend.workflow_effects import StepEffectConfig
```

- [ ] **Step 17.3: Run the full suite**

Run: `uv run pytest -x -q`
Expected: all tests pass. If any onboarding e2e tests broke because they expected `type="form"` on the last step, update them to use the new type (or pass `ad_creds` + monkeypatch the AD primitives).

- [ ] **Step 17.4: Commit**

```bash
git add not_dot_net/backend/workflow_service.py tests/
git commit -m "feat(seed): onboarding final step → ad_account_creation; VPN demo effect"
```

---

## Task 18: i18n keys EN + FR

**Files:**
- Modify: `not_dot_net/frontend/i18n.py`
- Modify: `tests/test_i18n.py` (extend `shared_allowed`)

- [ ] **Step 18.1: Inventory the keys used by this work**

Grep for `t("` and `t('` in all files modified by previous tasks and assemble the full set of keys that need i18n entries. Expected list (verify by grep):

- `effect_kind_ad_add_to_groups`, `effect_kind_ad_remove_from_groups`, `effect_kind_ad_enable_account`, `effect_kind_ad_disable_account`
- `effects`, `add_effect`, `on_action`, `kind`, `empty_effects`
- `samaccountname`, `uid`, `primary_gid`, `login_shell`, `home_directory`, `ou`, `mail`, `groups`, `notes`
- `uid_allocated_at_submit`, `complete`
- `ad_accounts`, `lock_existing_ad_uids`, `lock_existing_ad_uids_intro`, `lock_existing_ad_uids_result`, `recent_uid_allocations`, `source`, `acquired_at`
- `step_type_form`, `step_type_approval`, `step_type_ad_account_creation`, `ad_account_creation_fields_locked`
- `warning_effect_unknown_action`, `warning_effect_groups_not_eligible`
- `initial_password_copy_now`, `copy`, `copied`, `close`
- `group_add_failures`, `ou_required`, `permission_denied`

- [ ] **Step 18.2: Add keys to both languages**

In `not_dot_net/frontend/i18n.py`, locate the `EN` and `FR` dicts (or wherever translation tables live; `grep -n '"effects"' not_dot_net/frontend/i18n.py` for orientation). Add each key with:

EN sample:
```python
"effects": "Effects",
"add_effect": "Add effect",
"on_action": "On action",
"kind": "Kind",
"empty_effects": "No effects",
"effect_kind_ad_add_to_groups": "Add to AD groups",
"effect_kind_ad_remove_from_groups": "Remove from AD groups",
"effect_kind_ad_enable_account": "Enable AD account",
"effect_kind_ad_disable_account": "Disable AD account",
"samaccountname": "Login (sAMAccountName)",
"uid": "UID",
"primary_gid": "Primary GID",
"login_shell": "Login shell",
"home_directory": "Home directory",
"ou": "OU",
"mail": "Email",
"groups": "Groups",
"notes": "Notes",
"uid_allocated_at_submit": "next available — allocated on submit",
"ad_accounts": "AD Accounts",
"lock_existing_ad_uids": "Lock existing AD UIDs",
"lock_existing_ad_uids_intro": "Reserve every uidNumber currently in AD so we never reuse one.",
"lock_existing_ad_uids_result": "Seeded {seeded} UIDs, skipped {skipped} already present.",
"recent_uid_allocations": "Recent UID allocations",
"source": "Source",
"acquired_at": "Acquired at",
"step_type_form": "Form",
"step_type_approval": "Approval",
"step_type_ad_account_creation": "AD account creation",
"ad_account_creation_fields_locked": "Fields are built-in for this step type. Configure prefill in Settings → AD Accounts.",
"warning_effect_unknown_action": "Step '{step}': effect references unknown action '{action}'",
"warning_effect_groups_not_eligible": "Step '{step}': effect references groups not in eligible_groups: {groups}",
"initial_password_copy_now": "Initial password — copy now, it will not be shown again",
"copy": "Copy",
"copied": "Copied",
"close": "Close",
"group_add_failures": "Could not add to: {groups}",
"ou_required": "Please pick an OU",
"permission_denied": "Permission denied",
```

FR sample (translate each, keeping identifier-style strings unchanged):
```python
"effects": "Effets",
"add_effect": "Ajouter un effet",
"on_action": "Sur l'action",
"kind": "Type",
"empty_effects": "Aucun effet",
"effect_kind_ad_add_to_groups": "Ajouter aux groupes AD",
"effect_kind_ad_remove_from_groups": "Retirer des groupes AD",
"effect_kind_ad_enable_account": "Activer le compte AD",
"effect_kind_ad_disable_account": "Désactiver le compte AD",
"samaccountname": "Login (sAMAccountName)",
"uid": "UID",
"primary_gid": "GID principal",
"login_shell": "Shell de connexion",
"home_directory": "Répertoire personnel",
"ou": "Unité d'organisation",
"mail": "Email",
"groups": "Groupes",
"notes": "Notes",
"uid_allocated_at_submit": "prochain disponible — réservé à la soumission",
"ad_accounts": "Comptes AD",
"lock_existing_ad_uids": "Verrouiller les UIDs AD existants",
"lock_existing_ad_uids_intro": "Réserver tout uidNumber présent dans AD pour qu'aucun ne soit réutilisé.",
"lock_existing_ad_uids_result": "{seeded} UIDs ajoutés, {skipped} déjà présents.",
"recent_uid_allocations": "Allocations d'UID récentes",
"source": "Source",
"acquired_at": "Date d'acquisition",
"step_type_form": "Formulaire",
"step_type_approval": "Approbation",
"step_type_ad_account_creation": "Création de compte AD",
"ad_account_creation_fields_locked": "Les champs de cette étape sont intégrés. Configurez le pré-remplissage dans Paramètres → Comptes AD.",
"warning_effect_unknown_action": "Étape '{step}' : effet référence une action inconnue '{action}'",
"warning_effect_groups_not_eligible": "Étape '{step}' : effet référence des groupes hors eligible_groups : {groups}",
"initial_password_copy_now": "Mot de passe initial — copiez maintenant, il ne sera plus affiché",
"copy": "Copier",
"copied": "Copié",
"close": "Fermer",
"group_add_failures": "Impossible d'ajouter à : {groups}",
"ou_required": "Veuillez choisir une OU",
"permission_denied": "Permission refusée",
```

- [ ] **Step 18.3: Extend `shared_allowed` in `test_i18n.py`**

The i18n test verifies EN/FR have the same keys and certain identifier-style strings (like `"UID"`, `"sAMAccountName"`) are allowed to be identical in both. Open `tests/test_i18n.py`, find `shared_allowed`, append:

```python
    "sAMAccountName", "UID", "OU",
```

(Run the test once first to see what it actually complains about — only add what's needed.)

- [ ] **Step 18.4: Run the i18n test**

Run: `uv run pytest tests/test_i18n.py -v`
Expected: passes after additions.

- [ ] **Step 18.5: Commit**

```bash
git add not_dot_net/frontend/i18n.py tests/test_i18n.py
git commit -m "feat(i18n): EN+FR keys for AD account creation + effects framework"
```

---

## Task 19: Full-suite verification + final polish

- [ ] **Step 19.1: Run the entire test suite**

Run: `uv run pytest -x -q`
Expected: all tests pass — baseline ~708 + ~30 new = ~738.

If any test fails, fix it and re-run. Common pitfalls:
- `monkeypatch` target mismatch when service code uses `from X import Y` vs `import X` (patch the module that *holds* the binding, not where it was defined).
- Pydantic v2 model_rebuild ordering for `StepEffectConfig` ↔ `WorkflowStepConfig` (Task 11.1).
- `PRAGMA foreign_keys=ON` is already enforced — any test that creates a `UidAllocation` referencing a non-existent `user_id` will FK-violate. Fix by inserting the User first.

- [ ] **Step 19.2: Manual end-to-end smoke**

Run: `uv run python -m not_dot_net.cli serve --host localhost --port 8088`

In a browser:
1. Sign in as a superuser.
2. Settings → AD Accounts: edit `users_ous` to add at least one OU DN, set `eligible_groups` to a couple of test group DNs, save.
3. Settings → Workflows: open the workflow editor. Confirm the `it_account_creation` step shows the locked-fields banner and the new effect panel renders. Add an effect on the VPN approval step, picking a group from the eligible list.
4. New onboarding request → walk through initiation → newcomer_info (use a test target email mapped to an existing local User row so `target_email` resolution succeeds) → admin_validation → it_account_creation: confirm form is prefilled, edit OU, submit. Credentials prompt should fire.
5. Cancel the credentials prompt — confirm the step stays pending without side effects.

Stop the server.

- [ ] **Step 19.3: Check for unfinished work**

Run: `git status` and `git log --oneline origin/main..HEAD`. Each task in this plan should be one commit; verify the count matches (~18 commits). Run `grep -RIn "TODO\|FIXME\|XXX" not_dot_net/` and confirm no new debt was introduced.

- [ ] **Step 19.4: Memory update**

Per the CLAUDE.md auto-memory system, after the work is committed:
- Update `architecture-patterns.md` with the new "Workflow AD effects" section and the "UID allocator (centralized, no-reuse)" pattern.
- Add a new entry to `development-history.md` for this phase.
- No new feedback memories unless something surprising came up.

Use direct `Write`/`Edit` against files under `/home/jeandet/.claude/projects/-var-home-jeandet-Documents-prog-not-dot-net/memory/`.

- [ ] **Step 19.5: Final commit if anything trailed**

```bash
git status
# If anything is uncommitted, commit it with an appropriate message.
```

---

## Self-Review Notes

- All four effect kinds defined in spec are implemented and tested (Task 11).
- UID allocator no-reuse guarantee comes from PK + never-delete rows (Tasks 1, 3); seeding handles existing AD state (Task 4).
- AD admin credentials never persisted: prompted at action time via shared dialog (Task 10), passed through as a tuple to `submit_step` and effect handlers (Tasks 11–13).
- sAM cascade implemented as documented (Task 13.2); accent-stripping is shared with mail normalization.
- AD create failure semantics: UID stays consumed, step stays pending (covered in Task 13.7 negative test for sAM-exists; the create-failure path is exercised in the LDAP primitive tests in Task 7).
- Group-add best-effort: failures returned, not raised (Tasks 8 + 13).
- Editor exposure (full form) covered in Task 15; warnings extended; `ad_account_creation` step type integrated.
- Backwards compat preserved by defaulting `effects=[]` (Task 11) and keeping step key `it_account_creation` (Task 17).
- i18n EN + FR + test wiring (Task 18).
- No placeholders, no "TODO add validation" — every step shows code or commands.
