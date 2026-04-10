# Workflow Dashboard Improvements — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enrich the workflow dashboard with contextual actionable cards, a dedicated request detail page, notifications badge, and stale request highlighting.

**Architecture:** Cards become read-only summaries linking to a new `/workflow/request/{id}` detail page. Shared UI helpers (urgency badge, named step progress) are extracted into `workflow_step.py`. A new `DashboardConfig` section controls urgency thresholds. The browser tab title polls for actionable count.

**Tech Stack:** NiceGUI, SQLAlchemy async, Pydantic, FastAPI

---

## File Structure

| File | Role | Action |
|------|------|--------|
| `not_dot_net/config.py` | DashboardConfig model | Modify |
| `not_dot_net/backend/workflow_service.py` | `get_actionable_count()`, `resolve_actor_name()` | Modify |
| `not_dot_net/frontend/workflow_step.py` | Extract `render_urgency_badge()`, `render_step_progress()` | Modify |
| `not_dot_net/frontend/workflow_detail.py` | Detail page at `/workflow/request/{id}` | Create |
| `not_dot_net/backend/workflow_file_routes.py` | File download endpoint | Create |
| `not_dot_net/frontend/dashboard.py` | Enriched cards, table links, age column | Modify |
| `not_dot_net/frontend/shell.py` | Badge count on tab, browser title timer | Modify |
| `not_dot_net/frontend/i18n.py` | New translation keys | Modify |
| `not_dot_net/app.py` | Register detail page + file routes | Modify |
| `tests/test_dashboard_helpers.py` | Tests for urgency, step age, actionable count | Create |
| `tests/test_workflow_detail.py` | Tests for detail page access control + file route | Create |

---

### Task 1: DashboardConfig Section

**Files:**
- Modify: `not_dot_net/config.py:60-70`
- Test: `tests/test_dashboard_helpers.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dashboard_helpers.py
import pytest
from not_dot_net.config import DashboardConfig


async def test_dashboard_config_defaults():
    cfg = DashboardConfig()
    assert cfg.urgency_fresh_days == 2
    assert cfg.urgency_aging_days == 7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_dashboard_helpers.py::test_dashboard_config_defaults -v`
Expected: FAIL with `ImportError: cannot import name 'DashboardConfig'`

- [ ] **Step 3: Implement DashboardConfig**

Add to `not_dot_net/config.py` after `BookingsConfig`:

```python
class DashboardConfig(BaseModel):
    urgency_fresh_days: int = 2
    urgency_aging_days: int = 7


dashboard_config = section("dashboard", DashboardConfig, label="Dashboard")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_dashboard_helpers.py::test_dashboard_config_defaults -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/config.py tests/test_dashboard_helpers.py
git commit -m "feat: add DashboardConfig section with urgency thresholds"
```

---

### Task 2: Step Age Computation + Actionable Count

**Files:**
- Modify: `not_dot_net/backend/workflow_service.py`
- Test: `tests/test_dashboard_helpers.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_dashboard_helpers.py`:

```python
import uuid
from datetime import datetime, timezone, timedelta
from contextlib import asynccontextmanager

from not_dot_net.backend.workflow_service import (
    create_request,
    submit_step,
    list_events,
    get_actionable_count,
    compute_step_age_days,
)
from not_dot_net.backend.roles import RoleDefinition, roles_config
from not_dot_net.backend.db import User, get_async_session


async def _create_user(email="staff@test.com", role="staff") -> User:
    get_session = asynccontextmanager(get_async_session)
    async with get_session() as session:
        user = User(
            id=uuid.uuid4(),
            email=email,
            hashed_password="x",
            role=role,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _setup_roles():
    cfg = await roles_config.get()
    cfg.roles["admin"] = RoleDefinition(
        label="Admin",
        permissions=["manage_bookings", "manage_roles", "manage_settings",
                     "create_workflows", "approve_workflows", "view_audit_log", "manage_users"],
    )
    cfg.roles["staff"] = RoleDefinition(
        label="Staff",
        permissions=["create_workflows"],
    )
    cfg.roles["director"] = RoleDefinition(
        label="Director",
        permissions=["create_workflows", "approve_workflows"],
    )
    await roles_config.set(cfg)


async def test_compute_step_age_days():
    events = await _mock_events_with_ages()
    # compute_step_age_days takes list of events and current step key
    # returns number of days since the last event on the current step (or last transition to it)
    age = compute_step_age_days(events, "approval")
    assert isinstance(age, int)
    assert age >= 0


async def test_get_actionable_count():
    await _setup_roles()
    staff = await _create_user(email="staff2@test.com", role="staff")
    director = await _create_user(email="director2@test.com", role="director")
    req = await create_request(
        workflow_type="vpn_access",
        created_by=staff.id,
        data={"target_name": "A", "target_email": "a@test.com"},
    )
    await submit_step(req.id, staff.id, "submit", data={})
    count = await get_actionable_count(director)
    assert count == 1


async def test_get_actionable_count_zero():
    await _setup_roles()
    staff = await _create_user(email="staff3@test.com", role="staff")
    member_user = await _create_user(email="member3@test.com", role="member")
    await create_request(
        workflow_type="vpn_access",
        created_by=staff.id,
        data={"target_name": "A", "target_email": "a@test.com"},
    )
    count = await get_actionable_count(member_user)
    assert count == 0


async def _mock_events_with_ages():
    """Create a real request and return its events for age computation."""
    staff = await _create_user(email="age_staff@test.com", role="staff")
    req = await create_request(
        workflow_type="vpn_access",
        created_by=staff.id,
        data={"target_name": "X", "target_email": "x@test.com"},
    )
    await submit_step(req.id, staff.id, "submit", data={})
    return await list_events(req.id)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_dashboard_helpers.py -k "step_age or actionable_count" -v`
Expected: FAIL with `ImportError: cannot import name 'get_actionable_count'`

- [ ] **Step 3: Implement in workflow_service.py**

Add to `not_dot_net/backend/workflow_service.py` after `list_all_requests()`:

```python
def compute_step_age_days(events: list[WorkflowEvent], current_step: str) -> int:
    """Compute days since the last event that transitioned to the current step.

    Looks for the most recent event whose action moved the request into
    current_step (i.e., previous step's submit/approve), or the create event
    if still on the first step. Falls back to the last event overall.
    """
    if not events:
        return 0
    # Find the event that transitioned into current_step:
    # It's the last event with a different step_key whose action is submit/approve/create,
    # OR the last event on the current step itself.
    relevant = None
    for ev in events:
        if ev.step_key == current_step or ev.action in ("submit", "approve", "create"):
            relevant = ev
    if relevant is None:
        relevant = events[-1]
    if relevant.created_at is None:
        return 0
    now = datetime.now(timezone.utc)
    # Handle naive datetimes from SQLite
    created = relevant.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (now - created).days


async def get_actionable_count(user) -> int:
    """Return count of requests where user can act. Lightweight version of list_actionable."""
    cfg = await workflows_config.get()
    filters = []
    for wf_type, wf in cfg.workflows.items():
        for step in wf.steps:
            step_match = and_(
                WorkflowRequest.type == wf_type,
                WorkflowRequest.current_step == step.key,
            )
            if step.assignee_permission and await has_permissions(user, step.assignee_permission):
                filters.append(step_match)
            elif step.assignee == "target_person":
                filters.append(and_(step_match, WorkflowRequest.target_email == user.email))
            elif step.assignee == "requester":
                filters.append(and_(step_match, WorkflowRequest.created_by == user.id))

    if not filters:
        return 0

    from sqlalchemy import func as sa_func
    async with session_scope() as session:
        result = await session.execute(
            select(sa_func.count())
            .select_from(WorkflowRequest)
            .where(WorkflowRequest.status == RequestStatus.IN_PROGRESS, or_(*filters))
        )
        return result.scalar_one()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_dashboard_helpers.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/backend/workflow_service.py tests/test_dashboard_helpers.py
git commit -m "feat: add compute_step_age_days and get_actionable_count"
```

---

### Task 3: Shared UI Helpers — Urgency Badge + Named Step Progress

**Files:**
- Modify: `not_dot_net/frontend/workflow_step.py`

- [ ] **Step 1: Add render_urgency_badge function**

Add to `not_dot_net/frontend/workflow_step.py` after `render_status_badge()`:

```python
def render_urgency_badge(age_days: int, fresh_days: int = 2, aging_days: int = 7):
    """Render a colored urgency badge based on step age."""
    if age_days < fresh_days:
        color = "positive"
    elif age_days < aging_days:
        color = "warning"
    else:
        color = "negative"
    ui.badge(f"⏱ {age_days}d", color=color).props("outline")
```

- [ ] **Step 2: Add render_step_progress function**

Add to `not_dot_net/frontend/workflow_step.py` after `render_urgency_badge()`:

```python
def render_step_progress(current_step: str, status: str, steps: list):
    """Render a named step progress bar.

    Args:
        current_step: key of the current step
        status: request status (in_progress, completed, rejected)
        steps: list of WorkflowStepConfig from the workflow
    """
    step_keys = [s.key for s in steps]
    current_idx = step_keys.index(current_step) if current_step in step_keys else 0
    is_completed = status == "completed"

    with ui.row().classes("w-full gap-1 items-center"):
        for i, step in enumerate(steps):
            if is_completed or i < current_idx:
                color = "bg-positive"
            elif i == current_idx:
                color = "bg-primary"
            else:
                color = "bg-grey-4"
            height = "h-[6px]" if i == current_idx and not is_completed else "h-[4px]"
            ui.element("div").classes(f"flex-1 rounded {color} {height}")

    with ui.row().classes("w-full gap-1"):
        for i, step in enumerate(steps):
            if is_completed or i < current_idx:
                label = f"✓ {step.key}"
                cls = "text-[11px] text-grey flex-1"
            elif i == current_idx:
                label = f"● {step.key}"
                cls = "text-[11px] text-primary font-semibold flex-1"
            else:
                label = step.key
                cls = "text-[11px] text-grey-4 flex-1"
            ui.label(label).classes(cls)
```

- [ ] **Step 3: Run full test suite to verify no regressions**

Run: `uv run pytest -v`
Expected: ALL PASS (no behavior changes, only new functions added)

- [ ] **Step 4: Commit**

```bash
git add not_dot_net/frontend/workflow_step.py
git commit -m "feat: add render_urgency_badge and render_step_progress helpers"
```

---

### Task 4: i18n Keys

**Files:**
- Modify: `not_dot_net/frontend/i18n.py`

- [ ] **Step 1: Add new translation keys**

Add these keys to both `en` and `fr` dicts in `not_dot_net/frontend/i18n.py`:

English (add after `"confirm_delete_page"` line):

```python
        # Workflow detail
        "back_to_dashboard": "Back to dashboard",
        "requested_by": "Requested by",
        "via_token": "via token link",
        "show_data": "Show submitted data",
        "hide_data": "Hide data",
        "take_action": "Take Action",
        "waiting_since": "Waiting since",
        "your_action_needed": "Your action needed",
        "request_detail": "Request Detail",
        "view_detail": "View Detail",
        "age": "Age",
```

French (add after `"confirm_delete_page"` line):

```python
        # Workflow detail
        "back_to_dashboard": "Retour au tableau de bord",
        "requested_by": "Demandé par",
        "via_token": "via lien de jeton",
        "show_data": "Afficher les données",
        "hide_data": "Masquer les données",
        "take_action": "Agir",
        "waiting_since": "En attente depuis",
        "your_action_needed": "Action requise",
        "request_detail": "Détail de la demande",
        "view_detail": "Voir le détail",
        "age": "Ancienneté",
```

- [ ] **Step 2: Run i18n test to verify keys are balanced**

Run: `uv run pytest tests/test_i18n.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add not_dot_net/frontend/i18n.py
git commit -m "feat: add i18n keys for workflow detail page and dashboard"
```

---

### Task 5: File Download Endpoint

**Files:**
- Create: `not_dot_net/backend/workflow_file_routes.py`
- Modify: `not_dot_net/app.py:73-74`
- Test: `tests/test_workflow_detail.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_workflow_detail.py
import pytest
import uuid
from contextlib import asynccontextmanager

from not_dot_net.backend.db import User, get_async_session
from not_dot_net.backend.workflow_service import create_request, get_request_by_id
from not_dot_net.backend.roles import RoleDefinition, roles_config


async def _create_user(email="staff@test.com", role="staff") -> User:
    get_session = asynccontextmanager(get_async_session)
    async with get_session() as session:
        user = User(
            id=uuid.uuid4(),
            email=email,
            hashed_password="x",
            role=role,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _setup_roles():
    cfg = await roles_config.get()
    cfg.roles["admin"] = RoleDefinition(
        label="Admin",
        permissions=["manage_bookings", "manage_roles", "manage_settings",
                     "create_workflows", "approve_workflows", "view_audit_log", "manage_users"],
    )
    cfg.roles["staff"] = RoleDefinition(
        label="Staff",
        permissions=["create_workflows"],
    )
    cfg.roles["director"] = RoleDefinition(
        label="Director",
        permissions=["create_workflows", "approve_workflows"],
    )
    await roles_config.set(cfg)


async def test_can_view_request_creator():
    """Request creator can view their own request."""
    from not_dot_net.backend.workflow_file_routes import can_view_request
    await _setup_roles()
    staff = await _create_user(email="creator@test.com", role="staff")
    req = await create_request(
        workflow_type="vpn_access",
        created_by=staff.id,
        data={"target_name": "A", "target_email": "a@test.com"},
    )
    assert await can_view_request(staff, req) is True


async def test_can_view_request_admin():
    """Admin with view_audit_log can view any request."""
    from not_dot_net.backend.workflow_file_routes import can_view_request
    await _setup_roles()
    staff = await _create_user(email="creator2@test.com", role="staff")
    admin = await _create_user(email="admin2@test.com", role="admin")
    req = await create_request(
        workflow_type="vpn_access",
        created_by=staff.id,
        data={"target_name": "A", "target_email": "a@test.com"},
    )
    assert await can_view_request(admin, req) is True


async def test_cannot_view_request_unrelated():
    """Unrelated user without permissions cannot view."""
    from not_dot_net.backend.workflow_file_routes import can_view_request
    await _setup_roles()
    staff = await _create_user(email="creator3@test.com", role="staff")
    other = await _create_user(email="other3@test.com", role="member")
    cfg = await roles_config.get()
    cfg.roles["member"] = RoleDefinition(label="Member", permissions=[])
    await roles_config.set(cfg)
    req = await create_request(
        workflow_type="vpn_access",
        created_by=staff.id,
        data={"target_name": "A", "target_email": "a@test.com"},
    )
    assert await can_view_request(other, req) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_workflow_detail.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'not_dot_net.backend.workflow_file_routes'`

- [ ] **Step 3: Create workflow_file_routes.py**

```python
# not_dot_net/backend/workflow_file_routes.py
"""File download endpoint + request access control helpers."""

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.permissions import has_permissions
from not_dot_net.backend.users import current_active_user
from not_dot_net.backend.workflow_engine import can_user_act
from not_dot_net.backend.workflow_models import WorkflowFile, WorkflowRequest
from not_dot_net.backend.workflow_service import workflows_config

router = APIRouter(prefix="/workflow", tags=["workflow"])


async def can_view_request(user: User, req: WorkflowRequest) -> bool:
    """Check if user is allowed to view this request."""
    if str(user.id) == str(req.created_by):
        return True
    if await has_permissions(user, "view_audit_log"):
        return True
    cfg = await workflows_config.get()
    wf = cfg.workflows.get(req.type)
    if wf and can_user_act(user, req, wf):
        return True
    return False


@router.get("/file/{file_id}")
async def download_file(
    file_id: uuid.UUID,
    user: User = Depends(current_active_user),
):
    async with session_scope() as session:
        wf_file = await session.get(WorkflowFile, file_id)
        if wf_file is None:
            raise HTTPException(status_code=404, detail="File not found")
        req = await session.get(WorkflowRequest, wf_file.request_id)
        if req is None:
            raise HTTPException(status_code=404, detail="Request not found")

    if not await can_view_request(user, req):
        raise HTTPException(status_code=403, detail="Access denied")

    path = Path(wf_file.storage_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")
    return FileResponse(path, filename=wf_file.filename)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_workflow_detail.py -v`
Expected: ALL PASS

- [ ] **Step 5: Register router in app.py**

Add to `not_dot_net/app.py` after `app.include_router(login_router)` (line 74):

```python
from not_dot_net.backend.workflow_file_routes import router as workflow_file_router
app.include_router(workflow_file_router)
```

- [ ] **Step 6: Run full test suite**

Run: `uv run pytest -v`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add not_dot_net/backend/workflow_file_routes.py not_dot_net/app.py tests/test_workflow_detail.py
git commit -m "feat: add file download endpoint with access control"
```

---

### Task 6: Request Detail Page

**Files:**
- Create: `not_dot_net/frontend/workflow_detail.py`
- Modify: `not_dot_net/app.py`

- [ ] **Step 1: Create the detail page**

```python
# not_dot_net/frontend/workflow_detail.py
"""Request detail page — timeline-centered view with action panel."""

import uuid
from typing import Optional

from fastapi import Depends
from nicegui import ui

from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.permissions import has_permissions
from not_dot_net.backend.users import current_active_user_optional
from not_dot_net.backend.workflow_engine import can_user_act, get_current_step_config
from not_dot_net.backend.workflow_file_routes import can_view_request
from not_dot_net.backend.workflow_models import WorkflowFile
from not_dot_net.backend.workflow_service import (
    compute_step_age_days,
    get_request_by_id,
    list_events,
    submit_step,
    workflows_config,
)
from not_dot_net.config import dashboard_config
from not_dot_net.frontend.i18n import t
from not_dot_net.frontend.workflow_step import (
    render_approval,
    render_status_badge,
    render_step_form,
    render_step_progress,
    render_urgency_badge,
)


def setup():
    @ui.page("/workflow/request/{request_id}")
    async def detail_page(
        request_id: str,
        user: Optional[User] = Depends(current_active_user_optional),
    ):
        if user is None:
            ui.navigate.to("/login")
            return

        try:
            rid = uuid.UUID(request_id)
        except ValueError:
            _render_not_found()
            return

        req = await get_request_by_id(rid)
        if req is None:
            _render_not_found()
            return

        if not await can_view_request(user, req):
            _render_not_found()
            return

        cfg = await workflows_config.get()
        wf = cfg.workflows.get(req.type)
        if wf is None:
            _render_not_found()
            return

        events = await list_events(req.id)
        dash_cfg = await dashboard_config.get()
        age = compute_step_age_days(events, req.current_step)
        actor_names = await _resolve_actor_names([ev.actor_id for ev in events])

        # Load files for this request
        files_by_step = await _load_files(req.id)

        ui.colors(primary="#0F52AC")
        with ui.header().classes("row items-center px-4").style(
            "background-color: #0F52AC"
        ):
            ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to("/")).props(
                "flat color=white"
            )
            ui.label(t("app_name")).classes("text-h6 text-white text-weight-light")

        with ui.column().classes("w-full max-w-3xl mx-auto pa-6"):
            # Header
            _render_header(req, wf, age, dash_cfg, actor_names)

            # Step progress
            step_config = get_current_step_config(req, wf)
            render_step_progress(req.current_step, req.status, wf.steps)

            ui.separator().classes("my-4")

            # Timeline
            _render_timeline(events, actor_names, files_by_step)

            # Action panel
            if step_config and req.status == "in_progress":
                can_act = can_user_act(user, req, wf)
                if step_config.assignee_permission:
                    can_act = can_act and await has_permissions(user, step_config.assignee_permission)
                if can_act:
                    ui.separator().classes("my-4")
                    action_container = ui.column().classes("w-full")
                    with action_container:
                        await _render_action_panel(
                            action_container, user, req, step_config, wf, request_id,
                        )


def _render_not_found():
    ui.colors(primary="#0F52AC")
    with ui.header().classes("row items-center px-4").style(
        "background-color: #0F52AC"
    ):
        ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to("/")).props(
            "flat color=white"
        )
        ui.label(t("app_name")).classes("text-h6 text-white text-weight-light")
    with ui.column().classes("absolute-center items-center"):
        ui.icon("error", size="xl", color="negative")
        ui.label(t("page_not_found")).classes("text-h6")


def _render_header(req, wf, age_days, dash_cfg, actor_names):
    target = req.data.get("target_name") or req.data.get("person_name") or req.target_email or ""
    creator_name = actor_names.get(req.created_by, req.created_by or "")

    with ui.row().classes("w-full items-start justify-between"):
        with ui.column().classes("gap-0"):
            ui.label(f"{wf.label} — {target}").classes("text-h5 text-weight-light")
            date_str = req.created_at.strftime("%Y-%m-%d") if req.created_at else ""
            ui.label(f"{t('requested_by')}: {creator_name} · {date_str}").classes(
                "text-sm text-grey"
            )
        with ui.row().classes("items-center gap-2"):
            render_status_badge(req.status)
            if req.status == "in_progress":
                render_urgency_badge(age_days, dash_cfg.urgency_fresh_days, dash_cfg.urgency_aging_days)


def _render_timeline(events, actor_names, files_by_step):
    with ui.element("div").classes("relative ml-2 pl-5").style(
        "border-left: 2px solid #e0e0e0"
    ):
        for ev in events:
            is_last = ev == events[-1]
            dot_color = "#1976d2" if is_last else "#4caf50"

            with ui.element("div").classes("relative mb-5"):
                # Timeline dot
                ui.element("div").classes("absolute").style(
                    f"left: -31px; top: 2px; width: 12px; height: 12px; "
                    f"background: {dot_color}; border-radius: 50%;"
                    + (" box-shadow: 0 0 6px rgba(25,118,210,0.5);" if is_last else "")
                )

                # Timestamp
                ts = ev.created_at.strftime("%Y-%m-%d %H:%M") if ev.created_at else ""
                actor = actor_names.get(ev.actor_id, t("via_token") if ev.actor_token else "")
                ui.label(ts).classes("text-[11px] text-grey")
                ui.label(f"{actor} — {ev.step_key}: {ev.action}").classes("font-semibold text-sm")

                # Comment
                if ev.comment:
                    with ui.element("div").classes("mt-1 pl-3").style(
                        "border-left: 3px solid #1976d2; background: #f5f5f5; "
                        "padding: 6px 10px; border-radius: 4px;"
                    ):
                        ui.label(f'💬 "{ev.comment}"').classes("text-xs text-grey-8")

                # Data snapshot (collapsible)
                if ev.data_snapshot and ev.action not in ("save_draft",):
                    with ui.expansion(t("show_data")).classes("text-xs"):
                        for k, v in ev.data_snapshot.items():
                            if v:
                                ui.label(f"{k}: {v}").classes("text-xs text-grey-8")

                # Files for this step
                step_files = files_by_step.get(ev.step_key, [])
                if step_files and ev.action in ("submit", "save_draft"):
                    for f in step_files:
                        ui.link(
                            f"📎 {f.filename}",
                            f"/workflow/file/{f.id}",
                            new_tab=True,
                        ).classes("text-xs")


async def _render_action_panel(container, user, req, step_config, wf, request_id_str):
    with ui.card().classes("w-full q-pa-md").style(
        "background: #f8f9fa; border: 1px solid #e0e0e0;"
    ):
        ui.label(t("take_action")).classes(
            "text-xs text-grey uppercase tracking-wide mb-2"
        )

        if step_config.type == "approval":
            async def handle_approve(comment):
                try:
                    await submit_step(req.id, user.id, "approve", comment=comment, actor_user=user)
                except Exception as e:
                    ui.notify(str(e), color="negative")
                    return
                ui.notify(t("step_submitted"), color="positive")
                ui.navigate.to(f"/workflow/request/{request_id_str}")

            async def handle_reject(comment):
                try:
                    await submit_step(req.id, user.id, "reject", comment=comment, actor_user=user)
                except Exception as e:
                    ui.notify(str(e), color="negative")
                    return
                ui.notify(t("step_submitted"), color="positive")
                ui.navigate.to(f"/workflow/request/{request_id_str}")

            render_approval(req.data, wf, step_config, handle_approve, handle_reject)

        elif step_config.type == "form":
            async def handle_submit(data):
                try:
                    await submit_step(req.id, user.id, "submit", data=data, actor_user=user)
                except Exception as e:
                    ui.notify(str(e), color="negative")
                    return
                ui.notify(t("step_submitted"), color="positive")
                ui.navigate.to(f"/workflow/request/{request_id_str}")

            await render_step_form(step_config, req.data, on_submit=handle_submit)


async def _resolve_actor_names(actor_ids: list[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    """Resolve actor UUIDs to display names. Single query."""
    unique_ids = {aid for aid in actor_ids if aid is not None}
    if not unique_ids:
        return {}
    async with session_scope() as session:
        from sqlalchemy import select
        from not_dot_net.backend.db import User
        result = await session.execute(
            select(User.id, User.full_name, User.email).where(User.id.in_(unique_ids))
        )
        return {
            row.id: row.full_name or row.email
            for row in result.all()
        }


async def _load_files(request_id: uuid.UUID) -> dict[str, list[WorkflowFile]]:
    """Load all files for a request, grouped by step_key."""
    async with session_scope() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(WorkflowFile).where(WorkflowFile.request_id == request_id)
        )
        files: dict[str, list[WorkflowFile]] = {}
        for f in result.scalars().all():
            files.setdefault(f.step_key, []).append(f)
        return files
```

- [ ] **Step 2: Register the detail page in app.py**

Add to `not_dot_net/app.py` imports:

```python
from not_dot_net.frontend.workflow_detail import setup as setup_workflow_detail
```

Add after `setup_token()` (around line 83):

```python
setup_workflow_detail()
```

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest -v`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add not_dot_net/frontend/workflow_detail.py not_dot_net/app.py
git commit -m "feat: add request detail page at /workflow/request/{id}"
```

---

### Task 7: Enriched Dashboard Cards + Table Links

**Files:**
- Modify: `not_dot_net/frontend/dashboard.py`

- [ ] **Step 1: Rewrite _render_actionable with enriched cards**

Replace the entire `_render_actionable` function and `_render_action_form` function in `not_dot_net/frontend/dashboard.py` with:

```python
async def _render_actionable(container, user: User):
    container.clear()
    requests = await list_actionable(user)

    with container:
        ui.label(t("awaiting_action")).classes("text-h6 mb-2 mt-4")
        if not requests:
            ui.label(t("no_pending")).classes("text-grey")
            return

        cfg = await workflows_config.get()
        wf_labels = await _workflow_labels()
        dash_cfg = await dashboard_config.get()

        events_by_req = await list_events_batch([req.id for req in requests])

        # Build card data and sort by age (oldest first)
        card_data = []
        for req in requests:
            wf = cfg.workflows.get(req.type)
            if not wf:
                continue
            step_config = get_current_step_config(req, wf)
            if not step_config:
                continue
            events = events_by_req.get(req.id, [])
            age = compute_step_age_days(events, req.current_step)
            card_data.append((req, wf, step_config, events, age))

        card_data.sort(key=lambda x: x[4], reverse=True)

        # Resolve all actor names in one batch
        all_actor_ids = []
        for _, _, _, events, _ in card_data:
            all_actor_ids.extend(ev.actor_id for ev in events if ev.actor_id)
        all_actor_ids.extend(req.created_by for req, _, _, _, _ in card_data if req.created_by)
        actor_names = await _resolve_actor_names_batch(set(all_actor_ids))

        with ui.element("div").classes(
            "w-full grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3"
        ):
            for req, wf, step_config, events, age in card_data:
                target = _target_display(req)
                group_label = wf_labels.get(req.type, req.type)
                requester = actor_names.get(req.created_by, "")

                # Last non-create event
                last_event = next(
                    (ev for ev in reversed(events) if ev.action != "create"),
                    None,
                )
                last_comment = next(
                    (ev for ev in reversed(events) if ev.comment),
                    None,
                )

                with ui.card().classes(
                    "cursor-pointer q-py-sm q-px-md hover:shadow-lg transition-shadow"
                ).on("click", lambda _, r=req: ui.navigate.to(f"/workflow/request/{r.id}")):
                    # Header: target + urgency
                    with ui.row().classes("items-center justify-between w-full"):
                        with ui.column().classes("gap-0"):
                            ui.label(target or group_label).classes("font-bold")
                            ui.label(group_label).classes("text-xs text-grey")
                        render_urgency_badge(
                            age, dash_cfg.urgency_fresh_days, dash_cfg.urgency_aging_days,
                        )

                    # Step progress
                    render_step_progress(req.current_step, req.status, wf.steps)

                    # People
                    with ui.column().classes("gap-0 mt-2"):
                        ui.label(f"{t('requested_by')}: {requester}").classes(
                            "text-xs text-grey-8"
                        )
                        if last_event:
                            actor = actor_names.get(last_event.actor_id, t("via_token"))
                            ui.label(f"{actor} — {last_event.step_key}: {last_event.action}").classes(
                                "text-xs text-grey-8"
                            )

                    # Last comment
                    if last_comment:
                        actor = actor_names.get(last_comment.actor_id, "")
                        date_str = (
                            last_comment.created_at.strftime("%b %d")
                            if last_comment.created_at else ""
                        )
                        with ui.element("div").classes("mt-2 pl-3").style(
                            "border-left: 3px solid #1976d2; background: #f5f5f5; "
                            "padding: 4px 8px; border-radius: 4px;"
                        ):
                            comment_text = last_comment.comment
                            if len(comment_text) > 80:
                                comment_text = comment_text[:77] + "..."
                            ui.label(
                                f'💬 "{comment_text}" — {actor}, {date_str}'
                            ).classes("text-xs text-grey-8")
```

- [ ] **Step 2: Update _render_my_requests to link rows and add age column**

Replace the `_render_my_requests` function with a version that:
- Adds an "age" column
- Makes rows navigate to the detail page on click
- Uses named step progress instead of "N/M" text
- Removes inline event expansion

Replace the entire `_render_my_requests` function:

```python
async def _render_my_requests(container, user: User):
    container.clear()

    if await has_permissions(user, "view_audit_log"):
        requests = await list_all_requests()
    else:
        requests = await list_user_requests(user.id)

    with container:
        ui.label(t("my_requests")).classes("text-h6 mb-2")
        if not requests:
            ui.label(t("no_requests")).classes("text-grey")
            return

        cfg = await workflows_config.get()
        wf_labels = await _workflow_labels()
        dash_cfg = await dashboard_config.get()
        events_by_req = await list_events_batch([req.id for req in requests])

        columns = [
            {"name": "type", "label": t("workflow_type"), "field": "type", "sortable": True, "align": "left"},
            {"name": "target", "label": t("target_person"), "field": "target", "sortable": True, "align": "left"},
            {"name": "progress", "label": t("progress"), "field": "progress", "sortable": True, "align": "center"},
            {"name": "step", "label": t("current_step"), "field": "step", "sortable": True, "align": "left"},
            {"name": "age", "label": t("age"), "field": "age", "sortable": True, "align": "center"},
            {"name": "date", "label": t("created_at"), "field": "date", "sortable": True, "align": "left"},
            {"name": "status", "label": t("status"), "field": "status", "sortable": True, "align": "center"},
        ]

        rows = []
        for req in requests:
            wf = cfg.workflows.get(req.type)
            step_config = get_current_step_config(req, wf) if wf else None
            step_label = step_config.key if step_config else req.current_step
            current, total = get_step_progress(req, wf) if wf else (0, 0)
            events = events_by_req.get(req.id, [])
            age = compute_step_age_days(events, req.current_step)

            rows.append({
                "id": str(req.id),
                "type": wf_labels.get(req.type, req.type),
                "target": _target_display(req),
                "progress": f"{current}/{total}",
                "progress_pct": current / total if total else 0,
                "step": step_label,
                "age": age,
                "age_color": (
                    "positive" if age < dash_cfg.urgency_fresh_days
                    else "warning" if age < dash_cfg.urgency_aging_days
                    else "negative"
                ),
                "date": _format_date(req.created_at),
                "status": req.status,
            })

        table = ui.table(
            columns=columns, rows=rows, row_key="id", pagination={"rowsPerPage": 15},
        ).classes("w-full")
        table.props("flat bordered dense")

        table.add_slot("body", r'''
            <q-tr :props="props" @click="() => $parent.$emit('row-click', props.row)" class="cursor-pointer">
                <q-td v-for="col in props.cols" :key="col.name" :props="props">
                    <q-badge v-if="col.name === 'status'"
                        :color="col.value === 'completed' ? 'positive' : col.value === 'rejected' ? 'negative' : 'primary'"
                        :label="col.value"
                    />
                    <q-badge v-else-if="col.name === 'age'"
                        :color="props.row.age_color"
                        :label="col.value + 'd'"
                        outline
                    />
                    <div v-else-if="col.name === 'progress'" class="flex items-center gap-1" style="min-width: 80px">
                        <q-linear-progress
                            :value="props.row.progress_pct"
                            :color="props.row.status === 'rejected' ? 'negative' : props.row.status === 'completed' ? 'positive' : 'primary'"
                            style="width: 50px; height: 6px"
                            rounded
                        />
                        <span class="text-caption">{{ col.value }}</span>
                    </div>
                    <span v-else>{{ col.value }}</span>
                </q-td>
            </q-tr>
        ''')

        table.on("row-click", lambda e: ui.navigate.to(f"/workflow/request/{e.args['id']}"))

        # Filter row
        type_options = sorted({r["type"] for r in rows})
        status_options = sorted({r["status"] for r in rows})

        table.add_slot("top-left", "")
        with table.add_slot("top-right"):
            with ui.row().classes("items-center gap-2"):
                type_filter = ui.select(
                    options=[""] + type_options,
                    value="",
                    label=t("workflow_type"),
                ).props("outlined dense clearable").classes("min-w-[160px]")

                status_filter = ui.select(
                    options=[""] + status_options,
                    value="",
                    label=t("status"),
                ).props("outlined dense clearable").classes("min-w-[140px]")

                search = ui.input(placeholder="Search...").props("outlined dense clearable").classes("min-w-[160px]")

        def apply_filters():
            filtered = rows
            if type_filter.value:
                filtered = [r for r in filtered if r["type"] == type_filter.value]
            if status_filter.value:
                filtered = [r for r in filtered if r["status"] == status_filter.value]
            if search.value:
                q = search.value.lower()
                filtered = [r for r in filtered if q in r["target"].lower() or q in r["type"].lower()]
            table.rows = filtered

        type_filter.on_value_change(lambda _: apply_filters())
        status_filter.on_value_change(lambda _: apply_filters())
        search.on("update:model-value", lambda _: apply_filters())
```

- [ ] **Step 3: Update imports at top of dashboard.py**

Replace the imports at the top of `not_dot_net/frontend/dashboard.py`:

```python
"""Dashboard tab — My Requests + Awaiting My Action."""

import uuid

from nicegui import ui
from sqlalchemy import select

from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.permissions import has_permissions
from not_dot_net.backend.workflow_service import (
    list_user_requests,
    list_all_requests,
    list_actionable,
    list_events_batch,
    compute_step_age_days,
    workflows_config,
)
from not_dot_net.backend.workflow_engine import get_current_step_config, get_step_progress
from not_dot_net.config import dashboard_config
from not_dot_net.frontend.i18n import t
from not_dot_net.frontend.workflow_step import (
    render_status_badge,
    render_step_progress,
    render_urgency_badge,
)
```

- [ ] **Step 4: Add _resolve_actor_names_batch helper**

Add to `not_dot_net/frontend/dashboard.py` after `_target_display`:

```python
async def _resolve_actor_names_batch(actor_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Resolve actor UUIDs to display names."""
    if not actor_ids:
        return {}
    async with session_scope() as session:
        result = await session.execute(
            select(User.id, User.full_name, User.email).where(User.id.in_(actor_ids))
        )
        return {row.id: row.full_name or row.email for row in result.all()}
```

- [ ] **Step 5: Remove the old _render_action_form function**

Delete the entire `_render_action_form` function (it's no longer used — cards navigate to the detail page instead).

- [ ] **Step 6: Remove unused imports**

Remove `submit_step` from the workflow_service import (no longer used in dashboard). Remove unused imports from `workflow_step` (`render_approval`, `render_step_form`).

- [ ] **Step 7: Run full test suite**

Run: `uv run pytest -v`
Expected: ALL PASS

- [ ] **Step 8: Commit**

```bash
git add not_dot_net/frontend/dashboard.py
git commit -m "feat: enriched actionable cards and table links to detail page"
```

---

### Task 8: Notifications Badge in Shell

**Files:**
- Modify: `not_dot_net/frontend/shell.py`

- [ ] **Step 1: Add badge count to Dashboard tab**

In `not_dot_net/frontend/shell.py`, modify the tab creation and add a timer. Replace the `ui.tab(dashboard_label, icon="dashboard")` line (line 70) with a version that updates dynamically.

After `ui.colors(primary="#0F52AC")` and before the header, add a variable to hold the badge count:

```python
badge_count = {"value": 0}
```

Replace the dashboard tab line with:

```python
dashboard_tab = ui.tab(dashboard_label, icon="dashboard")
```

After the tab panels block (after line 121), add the badge timer:

```python
        if logged_in:
            from not_dot_net.backend.workflow_service import get_actionable_count

            async def update_badge():
                count = await get_actionable_count(effective_user)
                badge_count["value"] = count
                tab_text = f"{dashboard_label} ({count})" if count > 0 else dashboard_label
                dashboard_tab._props["label"] = tab_text
                dashboard_tab.update()
                title = f"({count}) NotDotNet" if count > 0 else "NotDotNet"
                await ui.run_javascript(f"document.title = {title!r}")

            ui.timer(60, update_badge)
            ui.timer(0, update_badge, once=True)
```

- [ ] **Step 2: Run full test suite**

Run: `uv run pytest -v`
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add not_dot_net/frontend/shell.py
git commit -m "feat: add notifications badge to dashboard tab and browser title"
```

---

### Task 9: Final Integration Test

**Files:**
- Run existing + new tests

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest -v`
Expected: ALL PASS

- [ ] **Step 2: Start the dev server and manually verify**

Run: `uv run python -m not_dot_net.cli serve --host localhost --port 8088 --seed-fake-users`

Manual checks:
1. Dashboard loads, actionable cards show urgency badge + people + comments
2. Click a card → navigates to `/workflow/request/{id}`
3. Detail page shows timeline, step progress, action panel
4. My Requests table rows are clickable, have age column
5. Dashboard tab shows badge count `(N)`
6. Browser tab title shows `(N) NotDotNet`
7. Approve/reject from detail page works and redirects back

- [ ] **Step 3: Commit any fixes if needed**

```bash
git add -A && git commit -m "fix: integration adjustments"
```
