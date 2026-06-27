# Resource Lifecycle & Safe Deletion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give resources a manual IT-driven status lifecycle (with user emails) and make resource deletion a deliberate, recoverable two-stage operation.

**Architecture:** A single `status` column on `Resource` driven by a data-defined FSM (`ALLOWED_TRANSITIONS`). A new `set_resource_status` service validates transitions, audits, and queues notification emails to the current booking's user (or to IT for out-of-service). Deletion becomes two-stage: active resources can only be retired; only retired resources can be restored or hard-deleted. All transitions are explicit, manual, and gated on `manage_bookings`.

**Tech Stack:** Python 3.13, SQLAlchemy 2.x async, Alembic, NiceGUI, pytest (`nicegui.testing` plugin). Email via the existing `send_mail` durable outbox.

## Global Constraints

- KISS / YAGNI — simplest thing that works; no speculative abstractions (project owner strongly prefers this).
- TDD — write the failing test first for every backend behavior.
- Status is **decoupled** from booking validation: only `active=False` blocks new bookings. `OUT_OF_SERVICE` must NOT block bookings.
- Permission gate for every mutation: `MANAGE_BOOKINGS` (already defined in `booking_service.py`).
- Status transitions have **no confirmation dialog** ("one click"); Retire and Delete-permanently DO have confirmation dialogs.
- i18n is bilingual — every new UI string gets an `en` AND an `fr` entry in `not_dot_net/frontend/i18n.py`.
- Audit every mutation via `log_audit("resource", <action>, ...)` mirroring `create_resource`.
- Closure-in-loop rule: any callback defined inside a `for` loop must capture loop variables as default args.

---

### Task 1: ResourceStatus enum + status column + migration

**Files:**
- Modify: `not_dot_net/backend/booking_models.py`
- Create: `alembic/versions/0016_add_resource_status.py`
- Test: `tests/test_booking_service.py`

**Interfaces:**
- Produces: `ResourceStatus(str, PyEnum)` with members `AVAILABLE="available"`, `BOOKED="booked"`, `READY="ready"`, `IN_USE="in_use"`, `RETURNED="returned"`, `OUT_OF_SERVICE="out_of_service"`; `Resource.status: str` column, default `"available"`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_booking_service.py`:

```python
async def test_new_resource_defaults_to_available_status():
    r = await _create_test_resource(name="PC-STATUS")
    fetched = await get_resource_by_id(r.id)
    assert fetched.status == "available"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_booking_service.py::test_new_resource_defaults_to_available_status -v`
Expected: FAIL — `AttributeError: 'Resource' object has no attribute 'status'`.

- [ ] **Step 3: Add the enum and column**

In `not_dot_net/backend/booking_models.py`, add the import and enum at top, and the column on `Resource`:

```python
import uuid
from datetime import date, datetime
from enum import Enum as PyEnum

from sqlalchemy import Date, ForeignKey, JSON, String, Text, func
from sqlalchemy.orm import Mapped, MappedAsDataclass, mapped_column

from not_dot_net.backend.db import Base


class ResourceStatus(str, PyEnum):
    AVAILABLE = "available"           # nothing physical in flight
    BOOKED = "booked"                 # reserved, IT hasn't prepped it
    READY = "ready"                   # prepped → ready for pickup
    IN_USE = "in_use"                 # picked up by the user
    RETURNED = "returned"             # brought back, awaiting IT checkup
    OUT_OF_SERVICE = "out_of_service" # broken / cleanup
```

On the `Resource` class, add this line directly after the `active` column:

```python
    status: Mapped[str] = mapped_column(
        String(20), default="available", server_default="available"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_booking_service.py::test_new_resource_defaults_to_available_status -v`
Expected: PASS (dev/test uses `create_all`, so the column appears automatically).

- [ ] **Step 5: Write the Alembic migration (production)**

Create `alembic/versions/0016_add_resource_status.py`:

```python
"""Add resource status lifecycle column.

Revision ID: 0016
Revises: 0015
"""
import sqlalchemy as sa
from alembic import op


revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "resource",
        sa.Column("status", sa.String(length=20), nullable=False,
                  server_default="available"),
    )


def downgrade() -> None:
    op.drop_column("resource", "status")
```

- [ ] **Step 6: Run the full booking suite to confirm nothing broke**

Run: `uv run pytest tests/test_booking_service.py -q`
Expected: PASS (all existing tests + the new one).

- [ ] **Step 7: Commit**

```bash
git add not_dot_net/backend/booking_models.py alembic/versions/0016_add_resource_status.py tests/test_booking_service.py
git commit -m "feat(booking): add ResourceStatus enum + status column (migration 0016)"
```

---

### Task 2: FSM transition table + set_resource_status (no emails yet)

**Files:**
- Modify: `not_dot_net/backend/booking_service.py`
- Test: `tests/test_booking_service.py`

**Interfaces:**
- Consumes: `ResourceStatus` from `booking_models`; `MANAGE_BOOKINGS`, `BookingValidationError`, `check_permission`, `session_scope`, `log_audit`.
- Produces:
  - `ALLOWED_TRANSITIONS: dict[ResourceStatus, set[ResourceStatus]]`
  - `available_transitions(status: str) -> list[str]` — sorted-by-value list of legal next status strings.
  - `async def set_resource_status(resource_id, new_status, actor=None, today=None) -> Resource` — validates the transition, writes `status`, audits, (emails wired in Task 3). Raises `BookingValidationError` on an illegal transition; `PermissionError` via `check_permission` when actor lacks `manage_bookings`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_booking_service.py` (and extend the import block from `booking_service` to include `set_resource_status`, `available_transitions`; import `ResourceStatus` from `not_dot_net.backend.booking_models`):

```python
async def test_set_resource_status_legal_transition():
    await _setup_roles()
    admin = await _create_user(email="it@test.com", role="admin")
    r = await _create_test_resource(name="PC-FSM")
    updated = await set_resource_status(r.id, "ready", actor=admin)
    assert updated.status == "ready"


async def test_set_resource_status_illegal_transition_raises():
    await _setup_roles()
    admin = await _create_user(email="it2@test.com", role="admin")
    r = await _create_test_resource(name="PC-FSM2")
    # available → in_use is not allowed (must go via ready)
    with pytest.raises(BookingValidationError):
        await set_resource_status(r.id, "in_use", actor=admin)


async def test_set_resource_status_requires_permission():
    await _setup_roles()
    plain = await _create_user(email="plain@test.com", role="staff")
    r = await _create_test_resource(name="PC-FSM3")
    with pytest.raises(PermissionError):
        await set_resource_status(r.id, "ready", actor=plain)


def test_available_transitions_lists_legal_next_states():
    assert available_transitions("in_use") == ["out_of_service", "returned"]
    assert available_transitions("out_of_service") == ["available"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_booking_service.py -k "set_resource_status or available_transitions" -v`
Expected: FAIL — `ImportError: cannot import name 'set_resource_status'`.

- [ ] **Step 3: Implement the table, helper, and service**

In `not_dot_net/backend/booking_service.py`, add the import at top:

```python
from not_dot_net.backend.booking_models import Booking, Resource, ResourceStatus
```

Add, after the `MANAGE_BOOKINGS` definition:

```python
ALLOWED_TRANSITIONS: dict[ResourceStatus, set[ResourceStatus]] = {
    ResourceStatus.AVAILABLE:      {ResourceStatus.BOOKED, ResourceStatus.READY, ResourceStatus.OUT_OF_SERVICE},
    ResourceStatus.BOOKED:         {ResourceStatus.READY, ResourceStatus.AVAILABLE, ResourceStatus.OUT_OF_SERVICE},
    ResourceStatus.READY:          {ResourceStatus.IN_USE, ResourceStatus.AVAILABLE, ResourceStatus.OUT_OF_SERVICE},
    ResourceStatus.IN_USE:         {ResourceStatus.RETURNED, ResourceStatus.OUT_OF_SERVICE},
    ResourceStatus.RETURNED:       {ResourceStatus.AVAILABLE, ResourceStatus.OUT_OF_SERVICE},
    ResourceStatus.OUT_OF_SERVICE: {ResourceStatus.AVAILABLE},
}


def available_transitions(status: str) -> list[str]:
    """Legal next status values from the given status, sorted for stable UI."""
    nexts = ALLOWED_TRANSITIONS[ResourceStatus(status)]
    return sorted(s.value for s in nexts)
```

Add the service function (place it in the `# --- Resources ---` section, after `delete_resource`):

```python
async def set_resource_status(resource_id: uuid.UUID, new_status, actor=None,
                              today: date | None = None) -> Resource:
    if actor is not None:
        await check_permission(actor, MANAGE_BOOKINGS)
    target = ResourceStatus(new_status)
    today = today or date.today()
    async with session_scope() as session:
        resource = await session.get(Resource, resource_id, with_for_update=True)
        if resource is None:
            raise ValueError(f"Resource {resource_id} not found")
        current = ResourceStatus(resource.status)
        if target not in ALLOWED_TRANSITIONS[current]:
            raise BookingValidationError(
                f"Cannot change status from {current.value} to {target.value}"
            )
        old = resource.status
        resource.status = target.value
        await session.commit()
        await session.refresh(resource)

    from not_dot_net.backend.audit import log_audit
    await log_audit(
        "resource", "status",
        actor_id=(actor.id if actor else None),
        target_type="resource", target_id=resource_id,
        detail=f"{old}→{target.value}",
    )
    await _notify_status_change(resource, target, today)  # implemented in Task 3
    return resource
```

- [ ] **Step 4: Add a temporary no-op for the Task-3 hook so Task 2 runs green**

Add this stub directly above `set_resource_status` (Task 3 replaces its body):

```python
async def _notify_status_change(resource: Resource, new_status: ResourceStatus,
                                today: date) -> None:
    return None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_booking_service.py -k "set_resource_status or available_transitions" -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add not_dot_net/backend/booking_service.py tests/test_booking_service.py
git commit -m "feat(booking): resource status FSM + set_resource_status service"
```

---

### Task 3: Current-booking resolution + status-change notifications

**Files:**
- Modify: `not_dot_net/backend/booking_service.py`
- Test: `tests/test_booking_service.py`

**Interfaces:**
- Consumes: `set_resource_status` (Task 2); `send_mail`, `org_config`, `has_permissions`, `User`, `Booking`, `select`, `escape`.
- Produces (module-level, used by `set_resource_status`):
  - `async def _current_booking_user(session, resource_id, today) -> User | None`
  - `async def _out_of_service_recipients(session) -> list[User]`
  - `def render_resource_status_body(*, user, resource, headline) -> str`
  - `_STATUS_NOTICE: dict[ResourceStatus, tuple[str, str]]` (subject, headline)
  - `_notify_status_change` (real body, replacing the Task-2 stub)

- [ ] **Step 1: Write the failing tests**

Follow the house pattern used in `tests/test_booking_reminders.py`: patch `send_mail` with an `AsyncMock` and assert on the awaited recipient (`send.await_args.args[0]`). Add these imports near the top of `tests/test_booking_service.py`:

```python
from unittest.mock import AsyncMock, patch
```

Then add:

```python
def _recipients(send_mock) -> set[str]:
    return {call.args[0] for call in send_mock.await_args_list}


async def test_ready_notifies_current_booking_user():
    await _setup_roles()
    admin = await _create_user(email="it-r@test.com", role="admin")
    owner = await _create_user(email="owner-r@test.com", role="staff")
    r = await _create_test_resource(name="PC-NOTIFY")
    start = _valid_start()
    await create_booking(r.id, owner.id, start, start + timedelta(days=3), actor=owner)
    with patch("not_dot_net.backend.booking_service.send_mail", new_callable=AsyncMock) as send:
        # today inside the booking window so it is the "current" booking
        await set_resource_status(r.id, "ready", actor=admin, today=start)
    send.assert_awaited_once()
    assert send.await_args.args[0] == "owner-r@test.com"


async def test_ready_with_no_booking_sends_no_email():
    await _setup_roles()
    admin = await _create_user(email="it-n@test.com", role="admin")
    r = await _create_test_resource(name="PC-NOBOOK")
    with patch("not_dot_net.backend.booking_service.send_mail", new_callable=AsyncMock) as send:
        await set_resource_status(r.id, "ready", actor=admin)
    send.assert_not_awaited()


async def test_out_of_service_notifies_managers():
    await _setup_roles()
    admin = await _create_user(email="mgr@test.com", role="admin")
    r = await _create_test_resource(name="PC-OOS")
    with patch("not_dot_net.backend.booking_service.send_mail", new_callable=AsyncMock) as send:
        await set_resource_status(r.id, "out_of_service", actor=admin)
    assert "mgr@test.com" in _recipients(send)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_booking_service.py -k "notifies or out_of_service or no_booking" -v`
Expected: FAIL — `send_mail` is never awaited (the Task-2 `_notify_status_change` stub is a no-op), so `assert_awaited_once`/membership assertions fail.

- [ ] **Step 3: Implement resolution helpers, render helper, and notice table**

In `not_dot_net/backend/booking_service.py`, ensure `has_permissions` is imported (it already is). Add near the other render helpers:

```python
_STATUS_NOTICE: dict[ResourceStatus, tuple[str, str]] = {
    ResourceStatus.READY: (
        "Your resource is ready for pickup",
        "Your booked resource is ready for pickup.",
    ),
    ResourceStatus.IN_USE: (
        "Resource pickup confirmed",
        "We've recorded that you picked up your booked resource.",
    ),
    ResourceStatus.RETURNED: (
        "Resource return confirmed",
        "We've recorded the return of your booked resource. Thank you.",
    ),
}


def render_resource_status_body(*, user: User, resource: Resource, headline: str) -> str:
    display_name = user.full_name or user.email
    return (
        f"<p>Hello {escape(display_name)},</p>"
        f"<p>{escape(headline)}</p>"
        "<table>"
        f"<tr><td><strong>Resource</strong></td><td>{escape(resource.name)}</td></tr>"
        f"<tr><td><strong>Location</strong></td><td>{escape(resource.location or '-')}</td></tr>"
        "</table>"
    )


async def _current_booking_user(session, resource_id: uuid.UUID, today: date) -> User | None:
    """The user of the active booking (start ≤ today < end); else the nearest
    not-yet-ended upcoming booking; else None."""
    result = await session.execute(
        select(Booking, User)
        .join(User, Booking.user_id == User.id)
        .where(Booking.resource_id == resource_id, Booking.end_date > today)
        .order_by(Booking.start_date)
    )
    rows = list(result.all())
    for booking, user in rows:
        if booking.start_date <= today:
            return user
    return rows[0][1] if rows else None


async def _out_of_service_recipients(session) -> list[User]:
    result = await session.execute(select(User).where(User.is_active == True))  # noqa: E712
    users = list(result.scalars().all())
    return [u for u in users if u.is_superuser or await has_permissions(u, MANAGE_BOOKINGS)]
```

- [ ] **Step 4: Replace the `_notify_status_change` stub with the real body**

```python
async def _notify_status_change(resource: Resource, new_status: ResourceStatus,
                                today: date) -> None:
    cfg = await org_config.get()
    app_name = (cfg.app_name or "not-dot-net").strip() or "not-dot-net"
    async with session_scope() as session:
        booking_user = await _current_booking_user(session, resource.id, today)

        if new_status in _STATUS_NOTICE:
            if booking_user is None or not booking_user.email:
                return
            subject, headline = _STATUS_NOTICE[new_status]
            await send_mail(
                booking_user.email,
                f"[{app_name}] {subject}",
                render_resource_status_body(user=booking_user, resource=resource, headline=headline),
            )
            return

        if new_status is ResourceStatus.OUT_OF_SERVICE:
            targets = await _out_of_service_recipients(session)
            if booking_user is not None:
                targets.append(booking_user)
            seen: set[str] = set()
            headline = f"{resource.name} has been marked out of service."
            for u in targets:
                if not u.email or u.email in seen:
                    continue
                seen.add(u.email)
                await send_mail(
                    u.email,
                    f"[{app_name}] Resource out of service: {resource.name}",
                    render_resource_status_body(user=u, resource=resource, headline=headline),
                )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_booking_service.py -k "notifies or out_of_service or no_booking" -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Run the full booking suite**

Run: `uv run pytest tests/test_booking_service.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add not_dot_net/backend/booking_service.py tests/test_booking_service.py
git commit -m "feat(booking): notify users/IT on resource status transitions"
```

---

### Task 4: Two-stage deletion — delete guard + restore_resource

**Files:**
- Modify: `not_dot_net/backend/booking_service.py`
- Test: `tests/test_booking_service.py`

**Interfaces:**
- Produces:
  - `delete_resource` raises `BookingValidationError` when `resource.active` is True; audits `"delete"` on success.
  - `async def restore_resource(resource_id, actor=None) -> Resource` — sets `active=True` and `status="available"`, audits `"restore"`.
- Note: "Retire" needs no new backend — the UI calls the existing `update_resource(id, active=False, actor=user)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_booking_service.py` (extend the `booking_service` import to include `restore_resource`):

```python
async def test_delete_active_resource_is_blocked():
    await _setup_roles()
    admin = await _create_user(email="del1@test.com", role="admin")
    r = await _create_test_resource(name="PC-DEL")  # active by default
    with pytest.raises(BookingValidationError):
        await delete_resource(r.id, actor=admin)


async def test_delete_retired_resource_succeeds():
    await _setup_roles()
    admin = await _create_user(email="del2@test.com", role="admin")
    r = await _create_test_resource(name="PC-DEL2")
    await update_resource(r.id, active=False, actor=admin)  # retire
    await delete_resource(r.id, actor=admin)
    assert await get_resource_by_id(r.id) is None


async def test_restore_reactivates_and_resets_status():
    await _setup_roles()
    admin = await _create_user(email="res1@test.com", role="admin")
    r = await _create_test_resource(name="PC-RES")
    await set_resource_status(r.id, "out_of_service", actor=admin)
    await update_resource(r.id, active=False, actor=admin)  # retire
    restored = await restore_resource(r.id, actor=admin)
    assert restored.active is True
    assert restored.status == "available"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_booking_service.py -k "delete_active or delete_retired or restore_reactivates" -v`
Expected: FAIL — `delete_active` fails (no guard) and `restore_resource` import errors.

- [ ] **Step 3: Add the delete guard + audit**

Replace the body of `delete_resource` in `booking_service.py` with:

```python
async def delete_resource(resource_id: uuid.UUID, actor=None) -> None:
    if actor is not None:
        await check_permission(actor, MANAGE_BOOKINGS)
    async with session_scope() as session:
        resource = await session.get(Resource, resource_id)
        if resource is None:
            raise ValueError(f"Resource {resource_id} not found")
        if resource.active:
            raise BookingValidationError("Retire the resource before deleting it")
        await session.delete(resource)
        await session.commit()

    from not_dot_net.backend.audit import log_audit
    await log_audit(
        "resource", "delete",
        actor_id=(actor.id if actor else None),
        target_type="resource", target_id=resource_id,
        detail="",
    )
```

- [ ] **Step 4: Add restore_resource**

Add directly below `delete_resource`:

```python
async def restore_resource(resource_id: uuid.UUID, actor=None) -> Resource:
    if actor is not None:
        await check_permission(actor, MANAGE_BOOKINGS)
    async with session_scope() as session:
        resource = await session.get(Resource, resource_id)
        if resource is None:
            raise ValueError(f"Resource {resource_id} not found")
        resource.active = True
        resource.status = ResourceStatus.AVAILABLE.value
        await session.commit()
        await session.refresh(resource)

    from not_dot_net.backend.audit import log_audit
    await log_audit(
        "resource", "restore",
        actor_id=(actor.id if actor else None),
        target_type="resource", target_id=resource_id,
        detail="",
    )
    return resource
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_booking_service.py -k "delete_active or delete_retired or restore_reactivates" -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Confirm status stays decoupled from booking validation**

Add this regression test (the spec requires booking an `OUT_OF_SERVICE` but active resource to still work):

```python
async def test_out_of_service_does_not_block_booking():
    await _setup_roles()
    admin = await _create_user(email="oosb@test.com", role="admin")
    owner = await _create_user(email="oosu@test.com", role="staff")
    r = await _create_test_resource(name="PC-OOSB")
    await set_resource_status(r.id, "out_of_service", actor=admin)
    start = _valid_start()
    booking = await create_booking(r.id, owner.id, start, start + timedelta(days=2), actor=owner)
    assert booking.id is not None
```

Run: `uv run pytest tests/test_booking_service.py::test_out_of_service_does_not_block_booking -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add not_dot_net/backend/booking_service.py tests/test_booking_service.py
git commit -m "feat(booking): two-stage deletion guard + restore_resource"
```

---

### Task 5: Frontend — status badge, transition buttons, retire/restore/delete

**Files:**
- Modify: `not_dot_net/frontend/bookings.py`
- Modify: `not_dot_net/frontend/i18n.py`
- Test: `tests/test_booking_ui_helpers.py`

**Interfaces:**
- Consumes: `set_resource_status`, `restore_resource`, `update_resource`, `available_transitions`, `delete_resource` from `booking_service`; `ResourceStatus` from `booking_models`.
- Produces: `_status_color(status: str) -> str` (pure helper in `bookings.py`).

- [ ] **Step 1: Write the failing test for the pure helper**

Add to `tests/test_booking_ui_helpers.py` (extend the import from `not_dot_net.frontend.bookings` to include `_status_color`):

```python
def test_status_color_maps_known_states():
    assert _status_color("available") == "positive"
    assert _status_color("out_of_service") == "negative"
    assert _status_color("ready") == "primary"
    # unknown falls back to grey
    assert _status_color("nonsense") == "grey"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_booking_ui_helpers.py::test_status_color_maps_known_states -v`
Expected: FAIL — `ImportError: cannot import name '_status_color'`.

- [ ] **Step 3: Add imports + the color helper in bookings.py**

In `not_dot_net/frontend/bookings.py`, extend the `from not_dot_net.backend.booking_service import (...)` block to also import `set_resource_status`, `restore_resource`, `update_resource`, `available_transitions`. Add a new import line:

```python
from not_dot_net.backend.booking_models import ResourceStatus
```

Add this module-level helper (near `_get_resource_for_booking`):

```python
_STATUS_COLOR = {
    "available": "positive",
    "booked": "orange",
    "ready": "primary",
    "in_use": "blue",
    "returned": "purple",
    "out_of_service": "negative",
}


def _status_color(status: str) -> str:
    return _STATUS_COLOR.get(status, "grey")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_booking_ui_helpers.py::test_status_color_maps_known_states -v`
Expected: PASS.

- [ ] **Step 5: Show the status badge on every resource card**

In `_resource_card`, replace the existing `if not res.active:` inactive-badge block (around line 338) with a status badge plus the retired badge:

```python
        with ui.row().classes("items-center gap-1 mt-1"):
            ui.badge(t(f"status_{res.status}"), color=_status_color(res.status))
            if not res.active:
                ui.badge(t("retired"), color="grey")
```

- [ ] **Step 6: Replace the admin controls block in `_render_resource_detail`**

Replace the entire `# Admin controls` block (current lines ~508–530, the `if is_admin:` section ending at the delete button) with:

```python
    # Admin controls
    if is_admin:
        ui.separator().classes("mt-3")

        if res.active:
            with ui.row().classes("items-center gap-2 mt-2"):
                ui.label(t("status") + ":").classes("text-sm")
                ui.badge(t(f"status_{res.status}"), color=_status_color(res.status))
            with ui.row().classes("gap-2 mt-1 flex-wrap"):
                for nxt in available_transitions(res.status):
                    async def do_transition(target=nxt):
                        try:
                            await set_resource_status(res.id, target, actor=user)
                        except Exception as e:
                            ui.notify(str(e), color="negative")
                            return
                        ui.notify(t("status_updated"), color="positive")
                        await _render_bookings(outer_container, user)

                    ui.button(t(f"mark_{nxt}"), on_click=do_transition).props(
                        "flat dense color=primary"
                    )

        ui.separator().classes("mt-3")
        with ui.row().classes("gap-2 mt-2"):
            ui.button(
                t("edit_resource"), icon="edit",
                on_click=lambda: _show_resource_dialog(
                    outer_container, user, resource=res,
                ),
            ).props("flat dense color=primary")

            if res.active:
                async def do_retire():
                    with ui.dialog() as dlg, ui.card():
                        ui.label(t("retire_confirm"))

                        async def confirm():
                            dlg.close()
                            try:
                                await update_resource(res.id, active=False, actor=user)
                            except Exception as e:
                                ui.notify(str(e), color="negative")
                                return
                            ui.notify(t("resource_retired"), color="positive")
                            await _render_bookings(outer_container, user)

                        with ui.row():
                            ui.button(t("cancel"), on_click=dlg.close).props("flat")
                            ui.button(t("retire"), on_click=confirm).props("color=warning")
                    dlg.open()

                ui.button(t("retire"), icon="archive", on_click=do_retire).props(
                    "flat dense color=warning"
                )
            else:
                async def do_restore():
                    try:
                        await restore_resource(res.id, actor=user)
                    except Exception as e:
                        ui.notify(str(e), color="negative")
                        return
                    ui.notify(t("resource_restored"), color="positive")
                    await _render_bookings(outer_container, user)

                async def do_delete():
                    with ui.dialog() as dlg, ui.card():
                        ui.label(t("delete_confirm"))

                        async def confirm():
                            dlg.close()
                            try:
                                await delete_resource(res.id, actor=user)
                            except Exception as e:
                                ui.notify(str(e), color="negative")
                                return
                            ui.notify(t("resource_deleted"), color="positive")
                            await _render_bookings(outer_container, user)

                        with ui.row():
                            ui.button(t("cancel"), on_click=dlg.close).props("flat")
                            ui.button(t("delete"), on_click=confirm).props("color=negative")
                    dlg.open()

                ui.button(t("restore"), icon="unarchive", on_click=do_restore).props(
                    "flat dense color=positive"
                )
                ui.button(t("delete"), icon="delete", on_click=do_delete).props(
                    "flat dense color=negative"
                )
```

Note: the `for nxt in available_transitions(res.status)` loop defines `do_transition` with `target=nxt` as a default arg — this satisfies the closure-in-loop rule. `res`, `user`, and `outer_container` are stable (not loop variables).

- [ ] **Step 7: Add the i18n keys (en + fr)**

In `not_dot_net/frontend/i18n.py`, add these keys inside the `"en"` dict (near the other booking keys around line 258):

```python
        "retired": "Retired",
        "status_available": "Available",
        "status_booked": "Booked",
        "status_ready": "Ready for pickup",
        "status_in_use": "In use",
        "status_returned": "Returned",
        "status_out_of_service": "Out of service",
        "mark_available": "Mark available",
        "mark_booked": "Mark booked",
        "mark_ready": "Mark ready",
        "mark_in_use": "Mark picked up",
        "mark_returned": "Mark returned",
        "mark_out_of_service": "Mark out of service",
        "status_updated": "Status updated",
        "retire": "Retire",
        "retire_confirm": "Retire this resource? It will no longer be bookable, but its history is kept.",
        "resource_retired": "Resource retired",
        "restore": "Restore",
        "resource_restored": "Resource restored",
        "delete_confirm": "Permanently delete this retired resource and all its bookings? This cannot be undone.",
```

And the matching keys inside the `"fr"` dict (near line 748):

```python
        "retired": "Retirée",
        "status_available": "Disponible",
        "status_booked": "Réservée",
        "status_ready": "Prête à récupérer",
        "status_in_use": "En cours d'utilisation",
        "status_returned": "Rendue",
        "status_out_of_service": "Hors service",
        "mark_available": "Marquer disponible",
        "mark_booked": "Marquer réservée",
        "mark_ready": "Marquer prête",
        "mark_in_use": "Marquer récupérée",
        "mark_returned": "Marquer rendue",
        "mark_out_of_service": "Marquer hors service",
        "status_updated": "Statut mis à jour",
        "retire": "Retirer",
        "retire_confirm": "Retirer cette ressource ? Elle ne sera plus réservable, mais son historique est conservé.",
        "resource_retired": "Ressource retirée",
        "restore": "Restaurer",
        "resource_restored": "Ressource restaurée",
        "delete_confirm": "Supprimer définitivement cette ressource retirée et toutes ses réservations ? Action irréversible.",
```

- [ ] **Step 8: Run the UI-helper tests + a broad sanity check**

Run: `uv run pytest tests/test_booking_ui_helpers.py -q`
Expected: PASS.

- [ ] **Step 9: Manual verification (one pass in the dev app)**

Start: `uv run python -m not_dot_net.cli serve --host localhost --port 8088`
Confirm, as an admin: a resource shows a colored status badge; transition buttons reflect only legal next states; clicking one updates the badge and (for ready/in_use/returned with a current booking) the recipient would receive mail (dev catch-all); an **active** resource shows **Retire** (with confirm) and **no** Delete; after retiring, it shows **Restore** + **Delete permanently** (with confirm); Restore brings it back as Available.

- [ ] **Step 10: Commit**

```bash
git add not_dot_net/frontend/bookings.py not_dot_net/frontend/i18n.py tests/test_booking_ui_helpers.py
git commit -m "feat(booking): status badge, IT transition buttons, retire/restore/delete UI"
```

---

### Task 6: Full suite + wrap-up

- [ ] **Step 1: Run the entire test suite**

Run: `uv run pytest -q`
Expected: PASS — previous count (952) plus the new tests, no regressions.

- [ ] **Step 2: Commit any final adjustments** (only if Step 1 required fixes)

```bash
git add -A
git commit -m "test(booking): resource lifecycle suite green"
```

---

## Self-Review

**Spec coverage:**
- §1 data model → Task 1 (enum, column, migration 0016). ✓
- §2 FSM + `set_resource_status` + current-booking resolution → Tasks 2 & 3. ✓
- §3 two-stage deletion (retire via `update_resource`, delete guard, restore resets status) → Tasks 4 & 5. ✓
- §4 notifications (READY/IN_USE/RETURNED → user; OUT_OF_SERVICE → managers+superusers+affected user) → Task 3. ✓
- §5 frontend (status badge, one-click transitions, retire/restore/delete) → Task 5. ✓
- §6 tests → embedded in every task + Task 6 full run. ✓
- Decoupling invariant (OUT_OF_SERVICE doesn't block booking) → Task 4 Step 6. ✓

**Placeholder scan:** No TBD/TODO. Mail assertions use the verified house pattern (patch `send_mail`, assert on `await_args`), matching `tests/test_booking_reminders.py`. ✓

**Type consistency:** `ResourceStatus` member/value names, `set_resource_status(resource_id, new_status, actor, today)`, `available_transitions(status) -> list[str]`, `restore_resource`, `_status_color`, and the `status_*`/`mark_*` i18n key naming are used identically across tasks. ✓
