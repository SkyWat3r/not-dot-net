# Resource lifecycle & safe deletion — design

**Date:** 2026-06-27
**Status:** Approved for planning
**Area:** Booking system (`booking_models.py`, `booking_service.py`, `frontend/bookings.py`)

## Problem

Two gaps in the booking system:

1. **Deleting a resource is too easy and irreversible.** `delete_resource()` is a hard
   cascade delete (resource + every booking gone instantly) with no confirmation in the
   backend or the UI.
2. **No physical-handoff lifecycle.** IT has no way to track or signal where a machine is
   in its handoff cycle (prepped, picked up, returned, broken), and users are never told
   when their machine is ready or that their return was registered.

## Goals

- Make resource removal deliberate and recoverable.
- Give IT a one-click manual state machine for the physical handoff, with user-facing
  email notifications at the meaningful points.

## Non-goals

- No change to the date-range booking calendar (reservations, conflict detection,
  end-of-booking reminders stay exactly as they are).
- No automatic / time-driven transitions — every status change is an explicit IT click.
- No per-booking state machine; the lifecycle lives on the **resource**.

## Decisions (resolved during brainstorming)

- The lifecycle is **one status field on `Resource`**, an overlay on the *current* booking.
- Transitions are **fully manual**, driven by `manage_bookings` users ("IT").
- Status is **decoupled** from booking validation: only `active=False` (retired) blocks new
  bookings. `OUT_OF_SERVICE` is temporary and does **not** block future bookings.
- Deletion is **two-stage**: active resources can only be *Retired*; only a retired resource
  can be *Restored* or *hard-deleted*.

## 1. Data model

`Resource` keeps `active: bool` (now meaning **retired/decommissioned**) and gains one column.
`active` and `status` are orthogonal.

```python
class ResourceStatus(str, PyEnum):          # mirrors RequestStatus house style
    AVAILABLE       = "available"           # nothing physical in flight
    BOOKED          = "booked"              # reserved, IT hasn't prepped it
    READY           = "ready"               # prepped → ready for pickup
    IN_USE          = "in_use"              # picked up by the user
    RETURNED        = "returned"            # brought back, awaiting IT checkup
    OUT_OF_SERVICE  = "out_of_service"      # broken / cleanup

status: Mapped[str] = mapped_column(
    String(20), default="available", server_default="available"
)
```

- **Alembic migration `0016`** (`down_revision="0015"`): add `resource.status` with
  `server_default="available"`. Dev mode (`create_all`) picks it up automatically.
- **No status-history table** — transitions are recorded in `audit_event`.

## 2. The FSM (data-driven transition table)

A plain dict in `booking_service.py` (or alongside the enum) drives validation and the UI.
IT only ever sees buttons for valid next states.

```python
ALLOWED_TRANSITIONS = {
    AVAILABLE:      {BOOKED, READY, OUT_OF_SERVICE},
    BOOKED:         {READY, AVAILABLE, OUT_OF_SERVICE},
    READY:          {IN_USE, AVAILABLE, OUT_OF_SERVICE},
    IN_USE:         {RETURNED, OUT_OF_SERVICE},
    RETURNED:       {AVAILABLE, OUT_OF_SERVICE},
    OUT_OF_SERVICE: {AVAILABLE},
}
```

New service function:

```python
async def set_resource_status(resource_id, new_status, actor) -> Resource
```

1. `check_permission(actor, MANAGE_BOOKINGS)`
2. load resource with a row lock; **validate** `new_status in ALLOWED_TRANSITIONS[current]`,
   else raise `BookingValidationError`
3. write `status`; `log_audit("resource", "status", target_type="resource",
   target_id=resource_id, detail=f"{old}→{new}")`
4. resolve the **current booking** + its user and queue the email for this transition (§4)
5. commit

**Current-booking resolution** (for notifications): the resource's booking with
`start_date ≤ today < end_date`; if none is active, fall back to the nearest not-yet-ended
upcoming booking; if still none, the status flips but **no user email** is sent.

## 3. Deletion (two-stage, server-enforced)

- **Active resource** → only removal action is **Retire** = `update_resource(active=False)`,
  behind a confirmation dialog. Hard delete is impossible while active.
- `delete_resource()` gains a guard: **raises if `resource.active` is True**
  ("retire before deleting") — enforced in the service, not only the UI.
- **Retired resource** (`active=False`) offers two actions:
  - **Restore** → `update_resource(active=True)` **and reset `status="available"`**
    (any prior physical state is stale). One-click, no confirmation.
  - **Delete permanently** → existing hard cascade delete, behind its own confirmation,
    available to `manage_bookings`.

## 4. Notifications

Reuse the booking-email pattern: `send_mail()` → durable outbox, small `render_*_body`
HTML helpers. Rules are hardcoded (no config knob).

| Transition         | Recipient                                                            |
|--------------------|---------------------------------------------------------------------|
| → READY            | current booking's user                                              |
| → IN_USE           | current booking's user (receipt)                                   |
| → RETURNED         | current booking's user (receipt)                                   |
| → OUT_OF_SERVICE   | `manage_bookings` users + superusers, **plus** affected current-booking user if any |

Recipient lookup for the `OUT_OF_SERVICE` admin set reuses the existing
get-users-by-permission helper used by the workflow notification system.

## 5. Frontend (`frontend/bookings.py`)

- **Status badge** (colored) on every resource card and detail view, visible to all users.
- **IT controls** (`manage_bookings`): a row of **one-click** transition buttons for the
  valid next states — no confirmation on status flips ("change the state in one click").
- **Retire** button (confirm dialog) replaces today's Delete on active resources.
- On retired resources: **Restore** (one-click) and **Delete permanently** (confirm dialog).

## 6. Tests (TDD — written first)

- Transition table: each legal transition allowed; illegal transitions raise
  `BookingValidationError`.
- `set_resource_status`: updates `status`, writes the audit row.
- Notifications: correct recipient per transition; current-booking resolution picks the
  active user; no active/upcoming booking → no user email queued.
- `OUT_OF_SERVICE` notifies the `manage_bookings`/superuser set (+ affected user).
- `delete_resource` raises when `active=True`; succeeds (cascade) when `active=False`.
- Retire sets `active=False`; Restore sets `active=True` and `status="available"`.
- Permission enforcement: non-`manage_bookings` actor rejected on status change, retire,
  restore, and delete.
- Status is decoupled from booking validation: a booking can still be created against an
  `OUT_OF_SERVICE` (but `active`) resource.
```
