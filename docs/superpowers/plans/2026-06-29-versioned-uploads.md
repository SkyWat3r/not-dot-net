# Versioned Workflow Uploads Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Treat the newest `WorkflowFile` per `(request, step, field)` as the current file; carry existing files over on corrections (no forced re-upload) and group the admin card per field with collapsible history.

**Architecture:** A new pure module groups `WorkflowFile` rows into current + previous versions and loads them. The token form seeds already-uploaded files so corrections show them as present with a Replace affordance. The admin detail card renders one entry per field (in workflow field order) with a collapsible previous-versions section. No schema change — `WorkflowFile.uploaded_at` already exists.

**Tech Stack:** Python 3.13, SQLAlchemy 2.x async, NiceGUI, pytest (`nicegui.testing.User`).

## Global Constraints

- No DB schema change / no Alembic migration — ordering uses the existing `WorkflowFile.uploaded_at` (server_default `now()`).
- Nothing is deleted: a re-upload adds a newer row that supersedes; older rows are kept as history.
- Every new user-facing string gets EN + FR i18n entries.
- Tests use the autouse in-memory SQLite fixture from `tests/conftest.py`; `PRAGMA foreign_keys=ON` is on, so any `WorkflowFile` must reference a real `WorkflowRequest`.

---

### Task 1: File-grouping module

**Files:**
- Create: `not_dot_net/backend/workflow_files.py`
- Test: `tests/test_workflow_files.py`

**Interfaces:**
- Consumes: `WorkflowFile` from `not_dot_net.backend.workflow_models`.
- Produces:
  - `FieldFileGroup(step_key: str, field_name: str, current: WorkflowFile, previous: list[WorkflowFile])` (frozen dataclass)
  - `group_files_by_field(files: list[WorkflowFile]) -> list[FieldFileGroup]`
  - `current_files_by_name(files: list[WorkflowFile]) -> dict[str, WorkflowFile]`
  - `async load_files(request_id: uuid.UUID, step_key: str | None = None) -> list[WorkflowFile]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_workflow_files.py
import uuid
from datetime import datetime, timedelta, timezone

from not_dot_net.backend.workflow_models import WorkflowFile, WorkflowRequest
from not_dot_net.backend.workflow_files import (
    group_files_by_field,
    current_files_by_name,
    load_files,
)
from not_dot_net.backend.db import session_scope

REQ = uuid.uuid4()


def _wf(field: str, dt: datetime, name: str, step: str = "newcomer_info") -> WorkflowFile:
    return WorkflowFile(
        request_id=REQ, step_key=step, field_name=field,
        filename=name, storage_path="x", uploaded_at=dt,
    )


def test_group_current_is_newest_and_previous_ordered():
    old = _wf("id_document", datetime(2026, 6, 10, 17, 19), "old.png")
    mid = _wf("id_document", datetime(2026, 6, 20, 9, 0), "mid.png")
    new = _wf("id_document", datetime(2026, 6, 29, 17, 4), "new.png")
    groups = group_files_by_field([old, new, mid])
    assert len(groups) == 1
    g = groups[0]
    assert g.field_name == "id_document"
    assert g.current.filename == "new.png"
    assert [p.filename for p in g.previous] == ["mid.png", "old.png"]


def test_group_separates_fields():
    a = _wf("id_document", datetime(2026, 6, 10, 1, 0), "id.png")
    b = _wf("bank_details", datetime(2026, 6, 10, 2, 0), "rib.png")
    by_field = {g.field_name: g for g in group_files_by_field([a, b])}
    assert set(by_field) == {"id_document", "bank_details"}
    assert by_field["bank_details"].previous == []


def test_current_files_by_name_picks_newest():
    old = _wf("id_document", datetime(2026, 6, 10, 1, 0), "old.png")
    new = _wf("id_document", datetime(2026, 6, 29, 1, 0), "new.png")
    current = current_files_by_name([old, new])
    assert current["id_document"].filename == "new.png"


async def test_load_files_filters_by_step():
    expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1)
    async with session_scope() as s:
        row = WorkflowRequest(type="doc_wf", current_step="docs",
                              token=str(uuid.uuid4()), token_expires_at=expiry)
        s.add(row)
        await s.commit()
        await s.refresh(row)
        rid = row.id
    async with session_scope() as s:
        s.add(WorkflowFile(request_id=rid, step_key="newcomer_info",
                           field_name="id_document", filename="a.png", storage_path="x"))
        s.add(WorkflowFile(request_id=rid, step_key="other_step",
                           field_name="x", filename="b.png", storage_path="x"))
        await s.commit()

    all_rows = await load_files(rid)
    step_rows = await load_files(rid, "newcomer_info")
    assert len(all_rows) == 2
    assert [f.filename for f in step_rows] == ["a.png"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_workflow_files.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'not_dot_net.backend.workflow_files'`.

- [ ] **Step 3: Write minimal implementation**

```python
# not_dot_net/backend/workflow_files.py
"""Group workflow file uploads into the current + historical versions per field."""

import uuid
from dataclasses import dataclass

from sqlalchemy import select

from not_dot_net.backend.db import session_scope
from not_dot_net.backend.workflow_models import WorkflowFile


@dataclass(frozen=True)
class FieldFileGroup:
    step_key: str
    field_name: str
    current: WorkflowFile
    previous: list[WorkflowFile]


def _newest_first(rows: list[WorkflowFile]) -> list[WorkflowFile]:
    # Stable tie-break on id so equal timestamps stay deterministic.
    return sorted(rows, key=lambda f: (f.uploaded_at, str(f.id)), reverse=True)


def group_files_by_field(files: list[WorkflowFile]) -> list[FieldFileGroup]:
    grouped: dict[tuple[str, str], list[WorkflowFile]] = {}
    for f in files:
        grouped.setdefault((f.step_key, f.field_name), []).append(f)
    groups: list[FieldFileGroup] = []
    for (step_key, field_name), rows in grouped.items():
        ordered = _newest_first(rows)
        groups.append(FieldFileGroup(
            step_key=step_key, field_name=field_name,
            current=ordered[0], previous=ordered[1:],
        ))
    return groups


def current_files_by_name(files: list[WorkflowFile]) -> dict[str, WorkflowFile]:
    by_name: dict[str, list[WorkflowFile]] = {}
    for f in files:
        by_name.setdefault(f.field_name, []).append(f)
    return {name: _newest_first(rows)[0] for name, rows in by_name.items()}


async def load_files(request_id: uuid.UUID, step_key: str | None = None) -> list[WorkflowFile]:
    async with session_scope() as session:
        query = select(WorkflowFile).where(WorkflowFile.request_id == request_id)
        if step_key is not None:
            query = query.where(WorkflowFile.step_key == step_key)
        return list((await session.execute(query)).scalars().all())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_workflow_files.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/backend/workflow_files.py tests/test_workflow_files.py
git commit -m "feat(files): group workflow uploads into current + history per field"
```

---

### Task 2: Admin card grouped per field

**Files:**
- Modify: `not_dot_net/frontend/workflow_detail.py` (the `field_labels` loop ~94-101, `_render_files_section` ~304-319, remove `_load_files` ~465-475)
- Modify: `not_dot_net/frontend/i18n.py` (add `previous_versions`, `other_files` to EN ~after line 193 and FR ~after line 713)
- Test: `tests/test_uploaded_files_versioning.py`

**Interfaces:**
- Consumes: `group_files_by_field`, `load_files`, `FieldFileGroup` from Task 1; the existing `_render_file_download(f, field_label, user)` in the same module.
- Produces: `_render_files_section(files: list[WorkflowFile], field_order: list[tuple[str, str]], field_labels: dict[str, str], user)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_uploaded_files_versioning.py
"""Admin detail card groups uploads per field: current + collapsible history."""
import uuid
from contextlib import asynccontextmanager

from nicegui.testing import User as UiUser

from not_dot_net.backend.db import session_scope, get_user_db
from not_dot_net.backend.roles import RoleDefinition, roles_config
from not_dot_net.backend.schemas import UserCreate
from not_dot_net.backend.users import get_user_manager, get_jwt_strategy
from not_dot_net.backend.workflow_models import WorkflowFile
from not_dot_net.backend.workflow_service import create_request, submit_step


async def _make_admin(email: str):
    cfg = await roles_config.get()
    cfg.roles["admin"] = RoleDefinition(
        label="Admin",
        permissions=["create_workflows", "approve_workflows", "access_personal_data"],
    )
    await roles_config.set(cfg)
    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as manager:
                db_user = await manager.create(UserCreate(email=email, password="pw123456"))
        db_user.role = "admin"
        session.add(db_user)
        await session.commit()
        await session.refresh(db_user)
    return db_user


async def test_admin_card_shows_current_and_previous(user: UiUser):
    admin = await _make_admin("ver-admin@test.com")
    req = await create_request(
        workflow_type="onboarding", created_by=admin.id,
        data={"contact_email": "n@e.com", "status": "PhD"}, actor=admin,
    )
    req = await submit_step(req.id, admin.id, "submit", data={}, actor_user=admin)

    from datetime import datetime
    async with session_scope() as session:
        session.add(WorkflowFile(
            request_id=req.id, step_key="newcomer_info", field_name="id_document",
            filename="OLD.png", storage_path="data/uploads/x/OLD.png",
            uploaded_at=datetime(2026, 6, 10, 17, 19)))
        session.add(WorkflowFile(
            request_id=req.id, step_key="newcomer_info", field_name="id_document",
            filename="NEW.png", storage_path="data/uploads/x/NEW.png",
            uploaded_at=datetime(2026, 6, 29, 17, 4)))
        await session.commit()

    token = await get_jwt_strategy().write_token(admin)
    user.http_client.cookies.set("fastapiusersauth", token)
    await user.open(f"/workflow/request/{req.id}")

    await user.should_see("NEW.png")        # current
    await user.should_see("previous version")  # history expansion label
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_uploaded_files_versioning.py -q`
Expected: FAIL — the flat card shows both "OLD.png" and "NEW.png" as siblings and there is no "previous version" expansion text.

- [ ] **Step 3a: Add i18n keys**

In `not_dot_net/frontend/i18n.py`, add to the EN dict immediately after the `"uploaded_files"` line (~193):

```python
        "previous_versions": "{count} previous version(s)",
        "other_files": "Other files",
```

And to the FR dict immediately after its `"uploaded_files"` line (~713):

```python
        "previous_versions": "{count} version(s) précédente(s)",
        "other_files": "Autres fichiers",
```

- [ ] **Step 3b: Build the field-order list in the page render**

In `not_dot_net/frontend/workflow_detail.py`, replace the `field_labels` loop (currently ~94-101) with:

```python
            from not_dot_net.backend.field_definitions import field_definitions_config, resolve_step_fields
            from not_dot_net.backend.workflow_files import load_files
            _defs_cfg = await field_definitions_config.get()
            field_labels = {}
            field_order: list[tuple[str, str]] = []
            for step in wf.steps:
                for f in await resolve_step_fields(step, cfg=_defs_cfg):
                    field_labels[f.name] = t(f.label) if f.label else f.name
                    if f.type == "file":
                        field_order.append((step.key, f.name))
            all_files = await load_files(req.id)
            if all_files:
                _render_files_section(all_files, field_order, field_labels, user)
```

Then delete the now-unused `files_by_step = await _load_files(req.id)` line (~75) and the `_load_files` function definition (~465-475).

- [ ] **Step 3c: Rewrite `_render_files_section`**

Replace the existing `_render_files_section` (~304-319) with:

```python
def _format_ts(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt is not None else ""


def _render_field_group(group, field_labels, user):
    label = field_labels.get(group.field_name, group.field_name)
    with ui.row().classes("items-center gap-2"):
        _render_file_download(group.current, label, user)
        ui.label(_format_ts(group.current.uploaded_at)).classes("text-xs text-grey")
    if group.previous:
        with ui.expansion(
            t("previous_versions").format(count=len(group.previous))
        ).classes("w-full"):
            for f in group.previous:
                with ui.row().classes("items-center gap-2 ml-4"):
                    _render_file_download(f, label, user)
                    ui.label(_format_ts(f.uploaded_at)).classes("text-xs text-grey")


def _render_files_section(files, field_order, field_labels, user):
    """Group uploads per field: current file + collapsible previous versions.

    Files whose (step, field) is no longer in the workflow config fall into a
    trailing "Other files" group so nothing is hidden.
    """
    from not_dot_net.backend.workflow_files import group_files_by_field

    groups = {(g.step_key, g.field_name): g for g in group_files_by_field(files)}
    with ui.card().classes("w-full q-pa-md mb-4").style(
        "background: #f8f9fa; border: 1px solid #e0e0e0;"
    ):
        ui.label(t("uploaded_files")).classes(
            "text-xs text-grey uppercase tracking-wide mb-2"
        )
        rendered = set()
        for key in field_order:
            group = groups.get(key)
            if group is None:
                continue
            _render_field_group(group, field_labels, user)
            rendered.add(key)
        orphans = [g for key, g in groups.items() if key not in rendered]
        if orphans:
            ui.label(t("other_files")).classes("text-xs text-grey mt-2")
            for group in orphans:
                _render_field_group(group, field_labels, user)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_uploaded_files_versioning.py -q`
Expected: PASS.

- [ ] **Step 5: Run the file-display regression suites**

Run: `uv run pytest tests/test_workflow_detail_download.py tests/test_workflow_file_download.py -q`
Expected: PASS (the current file still renders and downloads).

- [ ] **Step 6: Commit**

```bash
git add not_dot_net/frontend/workflow_detail.py not_dot_net/frontend/i18n.py tests/test_uploaded_files_versioning.py
git commit -m "feat(files): group admin uploaded-files card per field with history"
```

---

### Task 3: Corrections carry-over + Replace affordance

**Files:**
- Modify: `not_dot_net/frontend/workflow_token.py` (seed `uploaded_files` ~110)
- Modify: `not_dot_net/frontend/workflow_step.py` (`_render_field` file branch ~140-156)
- Modify: `not_dot_net/frontend/i18n.py` (add `replace` to EN ~after 193 and FR ~after 713)
- Test: `tests/test_corrections_carry_over.py`

**Interfaces:**
- Consumes: `load_files`, `current_files_by_name` from Task 1.
- Produces: no new public symbols; behaviour change only (seeded `files` dict + Replace button).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_corrections_carry_over.py
"""A file already uploaded must carry over on corrections: shown as present,
submittable without re-upload."""
import uuid
from datetime import datetime, timedelta, timezone

from nicegui.testing import User
from sqlalchemy import select

from not_dot_net.backend.db import session_scope
from not_dot_net.backend.workflow_models import WorkflowFile, WorkflowRequest
from not_dot_net.backend.workflow_service import WorkflowsConfig, workflows_config
from not_dot_net.config import FieldConfig, WorkflowConfig, WorkflowStepConfig


async def test_existing_file_carries_over_and_submits(user: User, monkeypatch):
    import not_dot_net.frontend.workflow_token as wt_mod

    await workflows_config.set(WorkflowsConfig(workflows={
        "doc_wf": WorkflowConfig(label="Docs", steps=[
            WorkflowStepConfig(
                key="docs", type="form", assignee="target_person",
                fields=[FieldConfig(name="id_document", type="file",
                                    required=True, label="id_document")],
                actions=["submit"]),
            WorkflowStepConfig(key="done", type="form",
                               assignee="target_person", actions=["submit"]),
        ]),
    }))

    tok = str(uuid.uuid4())
    expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1)
    async with session_scope() as s:
        row = WorkflowRequest(type="doc_wf", current_step="docs",
                              token=tok, token_expires_at=expiry)
        s.add(row)
        await s.commit()
        await s.refresh(row)
        req_id = row.id
        s.add(WorkflowFile(request_id=req_id, step_key="docs",
                           field_name="id_document", filename="ALREADY.png",
                           storage_path="data/uploads/x/ALREADY.png"))
        await s.commit()

    async def _true(*_a, **_kw):
        return True

    async def _false(*_a, **_kw):
        return False

    monkeypatch.setattr(wt_mod, "is_locked_out", _false)
    monkeypatch.setattr(wt_mod, "has_valid_code", _true)
    monkeypatch.setattr(wt_mod, "verify_code", _true)

    await user.open(f"/workflow/token/{tok}")
    user.find("Verify").click()
    await user.should_see("ALREADY.png")  # shown as already uploaded

    user.find("Submit").click()
    await user.should_see("Step submitted")  # no re-upload required

    async with session_scope() as s:
        req = (await s.execute(
            select(WorkflowRequest).where(WorkflowRequest.id == req_id)
        )).scalar_one()
        assert req.current_step == "done"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_corrections_carry_over.py -q`
Expected: FAIL — the form does not show "ALREADY.png" (the `files` dict is empty), and the required-file check blocks submit, so `current_step` stays `"docs"`.

- [ ] **Step 3a: Add the `replace` i18n key**

In `not_dot_net/frontend/i18n.py`, add to the EN dict after `"uploaded_files"` (~193):

```python
        "replace": "Replace",
```

And to the FR dict after its `"uploaded_files"` (~713):

```python
        "replace": "Remplacer",
```

- [ ] **Step 3b: Seed `uploaded_files` from existing files**

In `not_dot_net/frontend/workflow_token.py`, replace the line `uploaded_files: dict[str, str] = {}` (~110) with:

```python
                from not_dot_net.backend.workflow_files import load_files, current_files_by_name
                _existing = await load_files(request.id, step.key)
                uploaded_files: dict[str, str] = {
                    name: f.filename for name, f in current_files_by_name(_existing).items()
                }
```

- [ ] **Step 3c: Add the Replace affordance to the file field**

In `not_dot_net/frontend/workflow_step.py`, replace the `elif field_cfg.type == "file":` branch (~140-156) with:

```python
    elif field_cfg.type == "file":
        uploaded = (files or {}).get(field_cfg.name)
        slot = ui.column().classes(f"{width_class} gap-1")

        def _show_upload(slot=slot, field_cfg=field_cfg, label=label):
            slot.clear()
            with slot:
                req_mark = " *" if field_cfg.required else ""
                ui.upload(
                    label=f"{label}{req_mark}",
                    auto_upload=True,
                    max_file_size=max_upload_size_mb * 1024 * 1024,
                    on_upload=lambda e, name=field_cfg.name: on_file_upload(name, e),
                ).props("outlined flat accept='.pdf,.jpg,.jpeg,.png,.doc,.docx'").classes("w-full")

        def _show_uploaded(fname, slot=slot, label=label):
            slot.clear()
            with slot:
                with ui.row().classes("items-center gap-2"):
                    ui.icon("check_circle", color="positive", size="sm")
                    ui.label(f"{label}: {fname}").classes("text-positive text-sm")
                    if on_file_upload:
                        ui.button(t("replace"), on_click=_show_upload).props("flat dense size=sm")

        if uploaded:
            _show_uploaded(uploaded)
        elif on_file_upload:
            _show_upload()
        else:
            with slot:
                ui.label(f"{label}: no upload available").classes("text-grey text-sm")
        fields[field_cfg.name] = None
```

Note: `t` is already imported in `workflow_step.py`; `_show_upload`/`_show_uploaded` capture loop-free locals as default args per the project's closure rule.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_corrections_carry_over.py -q`
Expected: PASS.

- [ ] **Step 5: Run the upload/enforcement regression suites**

Run: `uv run pytest tests/test_required_file_enforced.py tests/test_workflow_token.py tests/test_onboarding_e2e.py -q`
Expected: PASS — first-round uploads (empty seed) still render the upload widget and required enforcement still blocks a truly-empty field.

- [ ] **Step 6: Commit**

```bash
git add not_dot_net/frontend/workflow_token.py not_dot_net/frontend/workflow_step.py not_dot_net/frontend/i18n.py tests/test_corrections_carry_over.py
git commit -m "feat(files): carry existing uploads over on corrections with Replace"
```

---

### Task 4: Full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS — all prior tests plus the three new files, no regressions.

- [ ] **Step 2: i18n parity check**

Run: `uv run pytest tests/test_i18n.py -q`
Expected: PASS — `previous_versions`, `other_files`, `replace` exist in both EN and FR.

---

## Self-Review

**Spec coverage:**
- "current file per (request,step,field)" → Task 1 `group_files_by_field` / `current_files_by_name`.
- "corrections carries files over, no forced re-upload" → Task 3 (seed + Replace; submit test asserts advance).
- "admin card grouped per field, current + collapsible history, field order, Other group" → Task 2.
- "no schema change" → Global Constraints; ordering via existing `uploaded_at`.
- "download unchanged" → Task 2 reuses `_render_file_download`; Step 5 regression run.

**Placeholder scan:** none — every code step shows full code; commands have expected output.

**Type consistency:** `group_files_by_field` / `current_files_by_name` / `load_files` / `FieldFileGroup` names and signatures match between Task 1 (definition) and Tasks 2-3 (consumers). `_render_files_section` new signature `(files, field_order, field_labels, user)` matches its only caller in Task 2 Step 3b.
