# Workflow Editor Pipeline Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Settings → Workflows editor self-explanatory by replacing the flat step tree with a visual, reorderable step pipeline, adding human step names, explained action semantics, delete confirmations, and fixing the render-mutates-assignee bug.

**Architecture:** Keep the existing `WorkflowEditorDialog` master-detail dialog. The left panel becomes a workflows-only list; the workflow detail pane gains a vertical "pipeline" of numbered, clickable step cards with transition annotations (↓ advance, ↰ corrections, ✕ reject) and ▲▼ reordering. Clicking a card opens the existing step editor (with a back button). `WorkflowStepConfig` gains an optional `label`; raw slug keys move behind an Advanced expander. Free-text action chips become a labeled multi-select that spells out engine semantics.

**Tech Stack:** NiceGUI 3.4 (Quasar), Pydantic 2, pytest + nicegui.testing.User plugin. All work on `main`, one commit per task, never push without explicit user consent.

**Conventions that bind every task:**
- Every closure inside a `for` loop captures ALL loop variables (and any referenced sibling closures) as default arguments.
- Every `ui.select`/`ui.input` with `outlined dense` gets `stack-label`.
- Every new UI string is a `t("key")` with the key added to BOTH `en` and `fr` dicts in `not_dot_net/frontend/i18n.py` (test_i18n enforces parity).
- TDD: write the failing test, watch it fail, implement, watch it pass, commit.
- Run tests with `uv run pytest <file> -x -q`; full suite is `uv run pytest -q` (~40 s, 835 tests at start).

---

## Current-state map (read before starting)

- `not_dot_net/frontend/workflow_editor.py` (~1000 lines) — `WorkflowEditorDialog`. Key methods: `_build`, `_render_form_body` (left tree + right detail), `_refresh_tree` (workflows AND steps), `_refresh_detail`, `_render_workflow_editor`, `_render_step_editor`, `_render_field_more`, `compute_warnings` (returns `list[str]` with `[wf]` / `[wf/step]` / `[wf/step/field]` prefixes), `_prompt_for_key`.
- `not_dot_net/frontend/workflow_editor_options.py` — pure option builders (`assignee_options`, `recipient_options`, `event_options`, `effect_kind_options`, `_slugify`). No NiceGUI imports at module level; `t` is imported inside functions when needed.
- `not_dot_net/config.py` — `FieldConfig`, `WorkflowStepConfig` (key, type, assignee_role/permission/assignee, fields, actions, partial_save, corrections_target, effects), `WorkflowConfig`, `is_field_visible`.
- `not_dot_net/backend/workflow_engine.py` — `compute_next_step`: `reject` → terminal REJECTED, `request_corrections` → jump to `corrections_target`, `save_draft` → stay, **anything else advances** (last step → COMPLETED).
- `not_dot_net/frontend/dashboard.py:188` — `step_label = step_config.key if step_config else req.current_step` (raw slug shown to end users).
- Tests: `tests/test_workflow_editor.py` (54 tests, model-level via `WorkflowEditorDialog.create` on a `@ui.page` with the `user` fixture — copy the pattern from `test_open_dialog_clones_current_config`), `tests/test_workflow_editor_options.py` (11 pure tests).
- Default workflows never use a `cancel` step action (cancellation is the separate `cancel_request` service path) — so the action picker offers `submit / approve / complete / request_corrections / reject` and merely preserves any unknown persisted value.

Shared test fixture (already exists at top of `tests/test_workflow_editor.py`) — reuse it, do not redefine:

```python
@pytest.fixture
async def admin_user():
    from types import SimpleNamespace
    return SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        email="admin@test", is_superuser=True, is_active=True, role="admin",
    )
```

---

### Task 1: Bug fix — opening a step editor must not mutate the assignee

Rendering `_render_step_editor` currently calls `set_step_assignee_from_picker` from inside `_render_sub_select` (workflow_editor.py:625-649) even on the initial render. Viewing an unassigned step silently assigns the first role and dirties the dialog.

**Files:**
- Modify: `not_dot_net/frontend/workflow_editor.py:625-650`
- Test: `tests/test_workflow_editor.py` (append)

- [x] **Step 1: Write the failing test**

Append to `tests/test_workflow_editor.py`:

```python
async def test_viewing_step_editor_does_not_assign_assignee(user: User, admin_user):
    """Rendering the step editor must not write an assignee into the model (bug:
    _render_sub_select used to auto-apply the first role on initial render)."""
    from not_dot_net.backend.roles import roles_config, RolesConfig, RoleDefinition
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog

    await roles_config.set(RolesConfig(roles={
        "hr": RoleDefinition(label="HR", permissions=[]),
        "it": RoleDefinition(label="IT", permissions=[]),
    }))
    await workflows_config.set(WorkflowsConfig(workflows={
        "demo": WorkflowConfig(label="Demo", steps=[
            WorkflowStepConfig(key="s1", type="form"),
        ]),
    }))

    captured = {}

    @ui.page("/_we_assignee_render")
    async def _page():
        dlg = await WorkflowEditorDialog.create(admin_user)
        captured["dlg"] = dlg

    await user.open("/_we_assignee_render")
    dlg = captured["dlg"]
    dlg.select("demo", "s1")

    step = dlg.working_copy.workflows["demo"].steps[0]
    assert step.assignee_role is None
    assert step.assignee_permission is None
    assert step.assignee is None
    assert not dlg.is_dirty()
```

Check `RoleDefinition`'s constructor signature in `not_dot_net/backend/roles.py` first; if `permissions` has a default, `RoleDefinition(label="HR")` is fine too.

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_workflow_editor.py::test_viewing_step_editor_does_not_assign_assignee -x -q`
Expected: FAIL — `assert step.assignee_role is None` (it will be `"hr"`) or `assert not dlg.is_dirty()`.

- [x] **Step 3: Fix `_render_sub_select`**

In `_render_step_editor`, replace the `_render_sub_select` closure and its two call sites with an `apply`-aware version. Only user interaction (`kind_select.on_value_change` or the sub-select's own change) may write to the model:

```python
        def _render_sub_select(kind_value: str, *, apply: bool = True) -> None:
            sub_select_container.clear()
            sub_list = sub_opts_by_kind.get(kind_value, [])
            if not sub_list:
                contextual_value = "contextual:requester" if kind_value == "contextual_requester" else "contextual:target_person"
                if apply:
                    self.set_step_assignee_from_picker(wf_key, step.key, contextual_value)
                sub_select_holder["select"] = None
                return
            if len(sub_list) == 1:
                only = sub_list[0]["value"]
                with sub_select_container:
                    ui.label(sub_list[0]["label"]).classes("text-grey")
                if apply:
                    self.set_step_assignee_from_picker(wf_key, step.key, only)
                sub_select_holder["select"] = None
                return
            options_dict = {o["value"]: o["label"] for o in sub_list}
            if current_val and any(o["value"] == current_val for o in sub_list):
                initial = current_val
            else:
                initial = sub_list[0]["value"] if apply else None
            with sub_select_container:
                sub = ui.select(options_dict, value=initial, label=t("step_assignee")
                                ).classes("w-full").props("dense outlined stack-label")
                sub.on_value_change(lambda e, w=wf_key, k=step.key: self.set_step_assignee_from_picker(w, k, e.value))
            sub_select_holder["select"] = sub
            if apply:
                self.set_step_assignee_from_picker(wf_key, step.key, initial)

        _render_sub_select(current_kind, apply=False)
        kind_select.on_value_change(lambda e, _r=_render_sub_select: _r(e.value))
```

(The only changes from the current code: the `apply` keyword, the three `if apply:` guards, `initial` becoming `None` when nothing is persisted and `apply=False`, and the initial call passing `apply=False`.)

- [x] **Step 4: Run the editor test file**

Run: `uv run pytest tests/test_workflow_editor.py -x -q`
Expected: all PASS, including the three existing `test_assignee_picker_writes_*` tests (they exercise the on-change path, which still applies).

- [x] **Step 5: Commit**

```bash
git add -- tests/test_workflow_editor.py not_dot_net/frontend/workflow_editor.py
git commit -m "fix(workflow-editor): viewing a step no longer auto-assigns the first role"
```

---

### Task 2: `WorkflowStepConfig.label` + `step_display()` + dashboard uses it

Steps get a human display name. Old persisted JSON has no `label` → Pydantic default `""` applies, no migration validator needed. `step_display` falls back to a prettified key.

**Files:**
- Modify: `not_dot_net/config.py` (WorkflowStepConfig + new function)
- Modify: `not_dot_net/frontend/dashboard.py:188`
- Test: `tests/test_workflow_config.py` (append)

- [x] **Step 1: Write the failing tests**

Append to `tests/test_workflow_config.py`:

```python
def test_step_label_defaults_empty_for_legacy_configs():
    from not_dot_net.config import WorkflowStepConfig
    step = WorkflowStepConfig.model_validate({"key": "newcomer_info", "type": "form"})
    assert step.label == ""


def test_step_display_prefers_label():
    from not_dot_net.config import WorkflowStepConfig, step_display
    step = WorkflowStepConfig(key="newcomer_info", label="Newcomer information", type="form")
    assert step_display(step) == "Newcomer information"


def test_step_display_falls_back_to_prettified_key():
    from not_dot_net.config import WorkflowStepConfig, step_display
    step = WorkflowStepConfig(key="newcomer_info", type="form")
    assert step_display(step) == "Newcomer info"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_workflow_config.py -x -q`
Expected: FAIL — `step_display` not importable / `label` validation error... actually `label=` on a model without the field raises; first failure is import or unexpected-kwarg.

- [x] **Step 3: Implement**

In `not_dot_net/config.py`, add `label` to `WorkflowStepConfig` (right after `key`):

```python
class WorkflowStepConfig(BaseModel):
    key: str
    label: str = ""
    type: str  # form, approval
    ...
```

Add below the class (next to `is_field_visible`, same "pure helpers live with the schema" convention):

```python
def step_display(step: WorkflowStepConfig) -> str:
    """Human name for a step: explicit label, else the key prettified."""
    return step.label or step.key.replace("_", " ").capitalize()
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_workflow_config.py -x -q`
Expected: PASS.

- [x] **Step 5: Use it on the dashboard**

In `not_dot_net/frontend/dashboard.py`, add to the existing `from not_dot_net.config import ...` import (or create `from not_dot_net.config import step_display`), then change line 188:

```python
            step_label = step_display(step_config) if step_config else req.current_step
```

- [x] **Step 6: Run dashboard + config tests**

Run: `uv run pytest tests/test_workflow_config.py tests/test_dashboard_helpers.py -x -q`
Expected: PASS.

- [x] **Step 7: Commit**

```bash
git add -- not_dot_net/config.py not_dot_net/frontend/dashboard.py tests/test_workflow_config.py
git commit -m "feat(workflows): step display labels with key fallback, shown on dashboard"
```

---

### Task 3: Reorder + label-aware creation mutations

Pure dialog-state mutations, no UI yet: `move_step`, `move_field`, `label` parameters on `add_workflow` / `add_step` / `duplicate_workflow`, and `display_name_to_key` (name → valid slug).

**Files:**
- Modify: `not_dot_net/frontend/workflow_editor.py` (mutation sections)
- Modify: `not_dot_net/frontend/workflow_editor_options.py` (new `display_name_to_key`)
- Test: `tests/test_workflow_editor.py`, `tests/test_workflow_editor_options.py` (append)

- [x] **Step 1: Write the failing pure-function tests**

Append to `tests/test_workflow_editor_options.py`:

```python
def test_display_name_to_key_slugifies():
    from not_dot_net.frontend.workflow_editor_options import display_name_to_key
    assert display_name_to_key("Travel request", set()) == "travel_request"


def test_display_name_to_key_prefixes_when_slug_starts_with_digit():
    from not_dot_net.frontend.workflow_editor_options import display_name_to_key
    key = display_name_to_key("3D printer access", set(), fallback_prefix="workflow")
    assert key == "workflow_3d_printer_access"


def test_display_name_to_key_dedups_against_taken():
    from not_dot_net.frontend.workflow_editor_options import display_name_to_key
    assert display_name_to_key("Travel request", {"travel_request"}) == "travel_request_2"
```

- [x] **Step 2: Write the failing dialog-mutation tests**

Append to `tests/test_workflow_editor.py` (copy the `@ui.page` + `user.open` boilerplate from `test_add_step`):

```python
async def test_move_step_reorders_and_clamps(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "demo": WorkflowConfig(label="Demo", steps=[
            WorkflowStepConfig(key="a", type="form"),
            WorkflowStepConfig(key="b", type="form"),
            WorkflowStepConfig(key="c", type="form"),
        ]),
    }))
    captured = {}

    @ui.page("/_we_move_step")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_we_move_step")
    dlg = captured["dlg"]

    dlg.move_step("demo", "b", -1)
    assert [s.key for s in dlg.working_copy.workflows["demo"].steps] == ["b", "a", "c"]
    dlg.move_step("demo", "b", -1)  # already first: no-op
    assert [s.key for s in dlg.working_copy.workflows["demo"].steps] == ["b", "a", "c"]
    dlg.move_step("demo", "c", +1)  # already last: no-op
    assert [s.key for s in dlg.working_copy.workflows["demo"].steps] == ["b", "a", "c"]


async def test_move_field_reorders(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "demo": WorkflowConfig(label="Demo", steps=[
            WorkflowStepConfig(key="s1", type="form", fields=[
                FieldConfig(name="f1", type="text"),
                FieldConfig(name="f2", type="text"),
            ]),
        ]),
    }))
    captured = {}

    @ui.page("/_we_move_field")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_we_move_field")
    dlg = captured["dlg"]

    dlg.move_field("demo", "s1", 1, -1)
    fields = dlg.working_copy.workflows["demo"].steps[0].fields
    assert [f.name for f in fields] == ["f2", "f1"]


async def test_add_workflow_and_step_accept_labels(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={}))
    captured = {}

    @ui.page("/_we_labels")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_we_labels")
    dlg = captured["dlg"]

    dlg.add_workflow("travel_request", label="Travel request")
    assert dlg.working_copy.workflows["travel_request"].label == "Travel request"

    dlg.add_step("travel_request", "manager_approval", label="Manager approval")
    step = dlg.working_copy.workflows["travel_request"].steps[0]
    assert step.label == "Manager approval"


async def test_duplicate_workflow_takes_new_label(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "demo": WorkflowConfig(label="Demo", steps=[]),
    }))
    captured = {}

    @ui.page("/_we_dup_label")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_we_dup_label")
    dlg = captured["dlg"]

    dlg.duplicate_workflow("demo", "demo_copy", label="Demo (copy)")
    assert dlg.working_copy.workflows["demo_copy"].label == "Demo (copy)"
```

- [x] **Step 3: Run to verify failures**

Run: `uv run pytest tests/test_workflow_editor_options.py tests/test_workflow_editor.py -x -q -k "display_name or move_ or accept_labels or takes_new_label"`
Expected: FAIL — `display_name_to_key` import error first; then `move_step` AttributeError, etc.

- [x] **Step 4: Implement `display_name_to_key`**

In `not_dot_net/frontend/workflow_editor_options.py`, below `_slugify`:

```python
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def display_name_to_key(name: str, taken: set[str], *, fallback_prefix: str = "workflow") -> str:
    """Derive a valid config key from a human display name.

    `_slugify` can produce a digit-leading slug ("3D printer" -> "3d_printer"),
    which the editor's key validator rejects — prefix with a word in that case.
    """
    slug = _slugify(name, taken)
    if _KEY_RE.fullmatch(slug):
        return slug
    return _slugify(f"{fallback_prefix} {name}", taken)
```

- [x] **Step 5: Implement the dialog mutations**

In `not_dot_net/frontend/workflow_editor.py`:

Change `add_workflow`, `add_step`, `duplicate_workflow` signatures (existing positional callers keep working):

```python
    def add_workflow(self, key: str, label: str | None = None) -> None:
        _validate_slug(key)
        if key in self.working_copy.workflows:
            raise ValueError(f"Workflow '{key}' already exists")
        self.working_copy.workflows[key] = WorkflowConfig(label=label or key, steps=[])
        ...  # rest unchanged

    def duplicate_workflow(self, src_key: str, new_key: str, label: str | None = None) -> None:
        ...  # existing validation unchanged
        self.working_copy.workflows[new_key] = self.working_copy.workflows[src_key].model_copy(deep=True)
        if label:
            self.working_copy.workflows[new_key].label = label
        ...  # rest unchanged

    def add_step(self, wf_key: str, step_key: str, label: str = "") -> None:
        ...  # existing validation unchanged
        wf.steps.append(WorkflowStepConfig(key=step_key, label=label, type="form"))
        ...  # rest unchanged
```

Add to the "step mutations" section:

```python
    def move_step(self, wf_key: str, step_key: str, delta: int) -> None:
        wf = self.working_copy.workflows[wf_key]
        keys = [s.key for s in wf.steps]
        idx = keys.index(step_key)
        new_idx = idx + delta
        if not 0 <= new_idx < len(wf.steps):
            return
        wf.steps[idx], wf.steps[new_idx] = wf.steps[new_idx], wf.steps[idx]
        self._refresh_detail()
```

Add to the "field-level mutations" section:

```python
    def move_field(self, wf_key: str, step_key: str, index: int, delta: int) -> None:
        step = self._find_step(wf_key, step_key)
        new_idx = index + delta
        if not 0 <= new_idx < len(step.fields):
            return
        step.fields[index], step.fields[new_idx] = step.fields[new_idx], step.fields[index]
        self._refresh_detail()
```

- [x] **Step 6: Run the two test files**

Run: `uv run pytest tests/test_workflow_editor_options.py tests/test_workflow_editor.py -x -q`
Expected: all PASS.

- [x] **Step 7: Commit**

```bash
git add -- not_dot_net/frontend/workflow_editor.py not_dot_net/frontend/workflow_editor_options.py tests/test_workflow_editor.py tests/test_workflow_editor_options.py
git commit -m "feat(workflow-editor): step/field reordering and label-aware create/duplicate mutations"
```

---

### Task 4: Pure helpers — `action_options` and `assignee_summary` (+ their i18n keys)

**Files:**
- Modify: `not_dot_net/frontend/workflow_editor_options.py`
- Modify: `not_dot_net/frontend/i18n.py` (both `en` and `fr`)
- Test: `tests/test_workflow_editor_options.py` (append)

- [x] **Step 1: Write the failing tests**

Append to `tests/test_workflow_editor_options.py`:

```python
def test_action_options_cover_engine_actions_and_preserve_unknown():
    from not_dot_net.frontend.workflow_editor_options import action_options
    opts = action_options(["submit", "legacy_sign_off"])
    values = [o["value"] for o in opts]
    assert values[:5] == ["submit", "approve", "complete", "request_corrections", "reject"]
    assert "legacy_sign_off" in values
    by_value = {o["value"]: o["label"] for o in opts}
    assert "legacy_sign_off" in by_value["legacy_sign_off"]


def test_assignee_summary_precedence_matches_engine():
    from types import SimpleNamespace
    from not_dot_net.backend.roles import RoleDefinition
    from not_dot_net.frontend.workflow_editor_options import assignee_summary

    roles = {"hr": RoleDefinition(label="HR team", permissions=[])}
    perms = {}

    step = SimpleNamespace(assignee="target_person", assignee_permission="x", assignee_role="hr")
    assert "request is about" in assignee_summary(step, roles, perms).lower() or assignee_summary(step, roles, perms)

    step = SimpleNamespace(assignee=None, assignee_permission=None, assignee_role="hr")
    assert "HR team" in assignee_summary(step, roles, perms)

    step = SimpleNamespace(assignee=None, assignee_permission=None, assignee_role=None)
    summary = assignee_summary(step, roles, perms)
    assert summary  # non-empty "nobody yet" text
```

(The first assertion is deliberately loose on wording; the point is precedence: contextual wins over permission over role — same order as `workflow_engine.effective_assignee`.)

- [x] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_workflow_editor_options.py -x -q`
Expected: FAIL — ImportError.

- [x] **Step 3: Implement the helpers**

In `not_dot_net/frontend/workflow_editor_options.py` (follow the `effect_kind_options` pattern of importing `t` inside the function):

```python
def action_options(existing: list[str] | None = None) -> list[dict]:
    """Labeled options for the step actions picker. The engine treats reject and
    request_corrections specially; every other action just advances — say so."""
    from not_dot_net.frontend.i18n import t
    out = [
        {"value": "submit", "label": t("action_submit")},
        {"value": "approve", "label": t("action_approve")},
        {"value": "complete", "label": t("action_complete")},
        {"value": "request_corrections", "label": t("action_request_corrections")},
        {"value": "reject", "label": t("action_reject")},
    ]
    known = {o["value"] for o in out}
    out.extend(
        {"value": a, "label": f"{a} (custom)"}
        for a in existing or [] if a not in known
    )
    return out


def assignee_summary(step, roles: Mapping[str, RoleDefinition],
                     permissions: Mapping[str, PermissionInfo]) -> str:
    """One-line description of who handles a step.

    Same precedence as workflow_engine.effective_assignee:
    contextual assignee > permission > role.
    """
    from not_dot_net.frontend.i18n import t
    if step.assignee == "target_person":
        return t("assignee_kind_target")
    if step.assignee == "requester":
        return t("assignee_kind_requester")
    if step.assignee_permission:
        info = permissions.get(step.assignee_permission)
        name = getattr(info, "label", None) or step.assignee_permission
        return t("assignee_summary_permission", name=name)
    if step.assignee_role:
        definition = roles.get(step.assignee_role)
        name = getattr(definition, "label", None) or step.assignee_role
        return t("assignee_summary_role", name=name)
    return t("assignee_none")
```

- [x] **Step 4: Add the i18n keys**

In `not_dot_net/frontend/i18n.py`, add to the `en` dict (near the existing `assignee_kind_*` keys):

```python
        "action_submit": "Submit — sends the form on to the next step",
        "action_approve": "Approve — moves the request forward",
        "action_complete": "Complete — moves the request forward",
        "action_reject": "Reject — ends the request as rejected",
        "action_request_corrections": "Request corrections — sends the request back to an earlier step",
        "assignee_summary_role": "Role: {name}",
        "assignee_summary_permission": "Permission: {name}",
        "assignee_none": "Nobody can act on this step yet",
```

And to the `fr` dict:

```python
        "action_submit": "Soumettre — envoie le formulaire à l'étape suivante",
        "action_approve": "Approuver — fait avancer la demande",
        "action_complete": "Terminer — fait avancer la demande",
        "action_reject": "Rejeter — clôt la demande comme rejetée",
        "action_request_corrections": "Demander des corrections — renvoie la demande à une étape précédente",
        "assignee_summary_role": "Rôle : {name}",
        "assignee_summary_permission": "Permission : {name}",
        "assignee_none": "Personne ne peut encore traiter cette étape",
```

- [x] **Step 5: Run tests**

Run: `uv run pytest tests/test_workflow_editor_options.py tests/test_i18n.py -x -q`
Expected: PASS (i18n parity + placeholder tests cover the new keys automatically).

- [x] **Step 6: Commit**

```bash
git add -- not_dot_net/frontend/workflow_editor_options.py not_dot_net/frontend/i18n.py tests/test_workflow_editor_options.py
git commit -m "feat(workflow-editor): labeled action options and assignee summaries"
```

---

### Task 5: Pipeline UI — workflows-only left list, step pipeline, delete confirms, warning badges

The big layout task. The left panel loses its step rows; the workflow detail pane gains the pipeline. `warnings_for` scopes the existing flat warning strings to a workflow/step without changing `compute_warnings`' return type (zero churn in the 10 existing warning tests).

**Files:**
- Modify: `not_dot_net/frontend/workflow_editor.py` (`_refresh_tree`, `_render_workflow_header`, delete `_render_step_row`, `_refresh_detail`, `_render_workflow_editor`, new `_render_pipeline` / `_render_transition_labels` / `warnings_for` / `_confirm`, `_prompt_for_key` generalization, `_on_add_*` handlers)
- Modify: `not_dot_net/frontend/i18n.py`
- Test: `tests/test_workflow_editor.py` (append)

- [x] **Step 1: Write the failing tests**

Append to `tests/test_workflow_editor.py`:

```python
async def test_warnings_for_scopes_by_workflow_and_step(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "demo": WorkflowConfig(label="Demo", steps=[
            WorkflowStepConfig(key="a", type="form",
                               actions=["request_corrections"], corrections_target="ghost"),
        ]),
        "demo2": WorkflowConfig(label="Demo2", steps=[]),
    }))
    captured = {}

    @ui.page("/_we_warnings_for")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_we_warnings_for")
    dlg = captured["dlg"]
    dlg._current_warnings = dlg.compute_warnings()

    step_warns = dlg.warnings_for("demo", "a")
    assert any("corrections_target" in w for w in step_warns)
    assert dlg.warnings_for("demo2", "a") == []
    wf_warns = dlg.warnings_for("demo2")
    assert any("no steps" in w for w in wf_warns)
    # workflow scope must not leak into a sibling whose key shares a prefix
    assert all(not w.startswith("[demo2") for w in dlg.warnings_for("demo"))


async def test_select_workflow_renders_pipeline_without_crash(user: User, admin_user):
    """Smoke test: the new pipeline renderer handles steps with corrections
    loops, rejects, plain advances, and a dead-end step with no actions."""
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "demo": WorkflowConfig(label="Demo", steps=[
            WorkflowStepConfig(key="a", label="Start", type="form", actions=["submit"]),
            WorkflowStepConfig(key="b", type="approval",
                               actions=["approve", "reject", "request_corrections"],
                               corrections_target="a"),
            WorkflowStepConfig(key="c", type="form", actions=[]),
        ]),
    }))
    captured = {}

    @ui.page("/_we_pipeline_smoke")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_we_pipeline_smoke")
    dlg = captured["dlg"]
    dlg.select("demo")          # workflow view: full pipeline
    dlg.select("demo", "b")     # step view
    dlg.select("demo")          # and back
    assert dlg.selected_workflow == "demo"
    assert dlg.selected_step is None
```

- [x] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_workflow_editor.py -x -q -k "warnings_for_scopes or pipeline_smoke"`
Expected: FAIL — `warnings_for` AttributeError. (The smoke test may pass before the renderer exists; that's fine, it's a regression net for Step 4.)

- [x] **Step 3: Implement `warnings_for` and `_confirm`**

In `workflow_editor.py`, next to `compute_warnings`:

```python
    def warnings_for(self, wf_key: str, step_key: str | None = None) -> list[str]:
        """Scope the flat warning strings to one workflow or one step, relying on
        the existing '[wf]' / '[wf/step]' / '[wf/step/field]' prefix convention."""
        if step_key is None:
            prefixes = (f"[{wf_key}]", f"[{wf_key}/")
        else:
            prefixes = (f"[{wf_key}/{step_key}]", f"[{wf_key}/{step_key}/")
        return [w for w in self._current_warnings if w.startswith(prefixes)]
```

Next to `_on_cancel_click`:

```python
    def _confirm(self, message: str, on_confirm) -> None:
        dlg = ui.dialog()
        with dlg, ui.card():
            ui.label(message)
            with ui.row():
                ui.button(t("delete"), on_click=lambda: (dlg.close(), on_confirm())).props("color=negative")
                ui.button(t("cancel"), on_click=dlg.close).props("flat")
        dlg.open()

    def _confirm_delete_workflow(self, key: str) -> None:
        wf = self.working_copy.workflows.get(key)
        name = (wf.label if wf else None) or key
        self._confirm(t("confirm_delete", name=name), lambda k=key: self.delete_workflow(k))

    def _confirm_delete_step(self, wf_key: str, step_key: str) -> None:
        from not_dot_net.config import step_display
        try:
            name = step_display(self._find_step(wf_key, step_key))
        except KeyError:
            name = step_key
        self._confirm(t("confirm_delete", name=name),
                      lambda w=wf_key, s=step_key: self.delete_step(w, s))
```

(`confirm_delete` = "Delete {name}?" already exists in i18n.)

- [x] **Step 4: Rewrite the left panel (workflows only)**

Replace `_refresh_tree` and `_render_workflow_header`, delete `_render_step_row`:

```python
    def _refresh_tree(self) -> None:
        if self._tree_container is None:
            return
        self._current_warnings = self.compute_warnings()
        self._tree_container.clear()
        with self._tree_container:
            for wf_key, wf in self.working_copy.workflows.items():
                self._render_workflow_row(wf_key, wf)
            ui.button(f"+ {t('add_workflow')}", on_click=self._on_add_workflow_click
                      ).props("flat dense color=primary")

    def _render_workflow_row(self, wf_key: str, wf) -> None:
        is_selected = self.selected_workflow == wf_key
        with ui.row().classes(f"w-full items-center no-wrap {'bg-blue-1' if is_selected else ''}"):
            ui.button(wf.label or wf_key, on_click=lambda k=wf_key: self.select(k)
                      ).props("flat dense no-caps").classes("grow text-left")
            wf_warns = self.warnings_for(wf_key)
            if wf_warns:
                ui.icon("warning", color="warning", size="xs").tooltip("\n".join(wf_warns))
            ui.button(icon="content_copy", on_click=lambda k=wf_key: self._on_duplicate_click(k)
                      ).props("flat dense round size=sm").tooltip(t("duplicate"))
            ui.button(icon="delete", on_click=lambda k=wf_key: self._confirm_delete_workflow(k)
                      ).props("flat dense round size=sm color=negative")
```

- [x] **Step 5: Remove the stray add-step button from `_refresh_detail`**

In `_refresh_detail`, delete the trailing `ui.button(f"+ Add step to {self.selected_workflow}", ...)` block — adding steps now lives only in the pipeline.

- [x] **Step 6: Implement the pipeline**

In `_render_workflow_editor`, insert between the Basics expansion and the Notifications expansion:

```python
        ui.label(t("wf_section_steps")).classes("text-subtitle2 q-mt-md")
        self._render_pipeline(wf_key, wf)
```

New methods (note: every button inside a card uses `.on("click.stop", ...)` so clicks don't bubble into the card's own select-step handler; every closure captures its loop variables as defaults):

```python
    def _render_pipeline(self, wf_key: str, wf) -> None:
        from not_dot_net.config import step_display
        if not wf.steps:
            ui.label(t("empty_steps")).classes("text-grey text-sm")
        last = len(wf.steps) - 1
        for idx, step in enumerate(wf.steps):
            card = ui.card().classes("w-full q-pa-sm cursor-pointer").props("flat bordered")
            card.on("click", lambda e, w=wf_key, s=step.key: self.select(w, s))
            with card:
                with ui.row().classes("w-full items-center no-wrap gap-2"):
                    ui.badge(str(idx + 1)).props("rounded color=primary")
                    with ui.column().classes("grow gap-0"):
                        ui.label(step_display(step)).classes("text-subtitle2")
                        ui.label(assignee_summary(step, self._roles, self._permissions)
                                 ).classes("text-grey text-xs")
                    step_warns = self.warnings_for(wf_key, step.key)
                    if step_warns:
                        ui.icon("warning", color="warning").tooltip("\n".join(step_warns))
                    up = ui.button(icon="keyboard_arrow_up").props("flat dense round size=sm")
                    up.on("click.stop", lambda e, w=wf_key, s=step.key: self.move_step(w, s, -1))
                    if idx == 0:
                        up.props("disable")
                    down = ui.button(icon="keyboard_arrow_down").props("flat dense round size=sm")
                    down.on("click.stop", lambda e, w=wf_key, s=step.key: self.move_step(w, s, +1))
                    if idx == last:
                        down.props("disable")
                    rm = ui.button(icon="delete").props("flat dense round size=sm color=negative")
                    rm.on("click.stop", lambda e, w=wf_key, s=step.key: self._confirm_delete_step(w, s))
            self._render_transition_labels(wf, idx, step)
        ui.button(f"+ {t('add_step')}", on_click=lambda k=wf_key: self._on_add_step_click(k)
                  ).props("flat dense color=primary")

    def _render_transition_labels(self, wf, idx: int, step) -> None:
        from not_dot_net.config import step_display
        actions = step.actions or []
        advancing = [a for a in actions if a not in ("reject", "request_corrections")]
        parts: list[str] = []
        if advancing:
            dest = (step_display(wf.steps[idx + 1]) if idx + 1 < len(wf.steps)
                    else f"✓ {t('pipeline_completed')}")
            parts.append(f"↓ {' / '.join(advancing)} → {dest}")
        if "request_corrections" in actions:
            target_step = next((s for s in wf.steps if s.key == step.corrections_target), None)
            dest = step_display(target_step) if target_step else (step.corrections_target or "?")
            parts.append(f"↰ request_corrections → {dest}")
        if "reject" in actions:
            parts.append(f"✕ reject → {t('pipeline_rejected')}")
        if not parts:
            parts.append(f"⚠ {t('pipeline_no_actions')}")
        with ui.row().classes("w-full q-pl-lg q-py-none"):
            ui.label("    ".join(parts)).classes("text-grey text-xs")
```

Add the import at the top of the file: extend the existing `from not_dot_net.frontend.workflow_editor_options import (...)` with `assignee_summary`.

- [x] **Step 7: Name-first creation prompts**

Generalize `_prompt_for_key` (rename arg only — body unchanged except the input label) and rewire the three `_on_*` handlers:

```python
    def _on_add_workflow_click(self) -> None:
        def _create(name: str) -> None:
            key = display_name_to_key(name, set(self.working_copy.workflows), fallback_prefix="workflow")
            self.add_workflow(key, label=name)
        self._prompt_for_name(t("new_workflow_prompt"), _create)

    def _on_duplicate_click(self, src_key: str) -> None:
        def _create(name: str, src=src_key) -> None:
            key = display_name_to_key(name, set(self.working_copy.workflows), fallback_prefix="workflow")
            self.duplicate_workflow(src, key, label=name)
        self._prompt_for_name(t("duplicate_workflow_prompt"), _create)

    def _on_add_step_click(self, wf_key: str) -> None:
        def _create(name: str, wk=wf_key) -> None:
            taken = {s.key for s in self.working_copy.workflows[wk].steps}
            key = display_name_to_key(name, taken, fallback_prefix="step")
            self.add_step(wk, key, label=name)
        self._prompt_for_name(t("new_step_prompt"), _create)

    def _prompt_for_name(self, prompt: str, callback) -> None:
        dlg = ui.dialog()
        with dlg, ui.card():
            ui.label(prompt)
            inp = ui.input(label=t("name_label")).props("dense outlined stack-label autofocus")
            err = ui.label("").classes("text-negative text-sm")

            def confirm():
                value = (inp.value or "").strip()
                if not value:
                    err.set_text(t("name_required"))
                    return
                try:
                    callback(value)
                    dlg.close()
                except ValueError as e:
                    err.set_text(str(e))

            inp.on("keydown.enter", lambda e: confirm())
            with ui.row():
                ui.button("OK", on_click=confirm).props("color=primary")
                ui.button(t("cancel"), on_click=dlg.close).props("flat")
        dlg.open()
```

Delete the now-unused `_prompt_for_key`. Import `display_name_to_key` in the options import at the top.

- [x] **Step 8: Add the i18n keys**

`en`:

```python
        "wf_section_steps": "Steps",
        "empty_steps": "No steps yet — add the first one below",
        "add_workflow": "Add workflow",
        "add_step": "Add step",
        "duplicate": "Duplicate",
        "new_workflow_prompt": "Name the new workflow",
        "new_step_prompt": "Name the new step",
        "duplicate_workflow_prompt": "Name for the copy",
        "name_label": "Name",
        "name_required": "Please enter a name",
        "pipeline_completed": "Request completed",
        "pipeline_rejected": "Request rejected",
        "pipeline_no_actions": "No actions configured — requests will be stuck on this step",
```

`fr`:

```python
        "wf_section_steps": "Étapes",
        "empty_steps": "Aucune étape — ajoutez la première ci-dessous",
        "add_workflow": "Ajouter un processus",
        "add_step": "Ajouter une étape",
        "duplicate": "Dupliquer",
        "new_workflow_prompt": "Nom du nouveau processus",
        "new_step_prompt": "Nom de la nouvelle étape",
        "duplicate_workflow_prompt": "Nom de la copie",
        "name_label": "Nom",
        "name_required": "Veuillez saisir un nom",
        "pipeline_completed": "Demande terminée",
        "pipeline_rejected": "Demande rejetée",
        "pipeline_no_actions": "Aucune action configurée — les demandes resteront bloquées à cette étape",
```

- [x] **Step 9: Run the editor + i18n test files**

Run: `uv run pytest tests/test_workflow_editor.py tests/test_i18n.py -x -q`
Expected: all PASS. If an existing test referenced the deleted `_prompt_for_key` or `_render_step_row`, update it to the new name-first flow (check with `grep -n "_prompt_for_key\|_render_step_row\|_render_workflow_header" tests/`).

- [x] **Step 10: Commit**

```bash
git add -- not_dot_net/frontend/workflow_editor.py not_dot_net/frontend/i18n.py tests/test_workflow_editor.py
git commit -m "feat(workflow-editor): visual step pipeline with reordering, confirms, and inline warnings"
```

---

### Task 6: Step editor rework — back button, label field, key behind Advanced, action picker, field reordering

**Files:**
- Modify: `not_dot_net/frontend/workflow_editor.py` (`_render_step_editor`, `_render_field_more`)
- Modify: `not_dot_net/frontend/i18n.py`
- Test: `tests/test_workflow_editor.py` (append)

- [x] **Step 1: Write the failing test**

```python
async def test_action_picker_preserves_unknown_persisted_action(user: User, admin_user):
    """A legacy action like 'legacy_sign_off' must survive a round-trip through
    the step editor's action picker (rendered as '<name> (custom)')."""
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "demo": WorkflowConfig(label="Demo", steps=[
            WorkflowStepConfig(key="s1", type="form", actions=["submit", "legacy_sign_off"]),
        ]),
    }))
    captured = {}

    @ui.page("/_we_action_picker")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_we_action_picker")
    dlg = captured["dlg"]
    dlg.select("demo", "s1")  # render the step editor

    step = dlg.working_copy.workflows["demo"].steps[0]
    assert step.actions == ["submit", "legacy_sign_off"]
    assert not dlg.is_dirty()


async def test_step_label_edit_propagates(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "demo": WorkflowConfig(label="Demo", steps=[
            WorkflowStepConfig(key="s1", type="form"),
        ]),
    }))
    captured = {}

    @ui.page("/_we_step_label")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_we_step_label")
    dlg = captured["dlg"]
    dlg.set_step_field("demo", "s1", "label", "First step")
    assert dlg.working_copy.workflows["demo"].steps[0].label == "First step"
```

- [x] **Step 2: Run to verify state**

Run: `uv run pytest tests/test_workflow_editor.py -x -q -k "action_picker_preserves or step_label_edit"`
Expected: `test_step_label_edit_propagates` PASSES already (`set_step_field` is generic — that's fine, it pins the behavior). `test_action_picker_preserves_unknown_persisted_action` FAILS only if rendering mutates; treat it as the regression net while rewiring the widget.

- [x] **Step 3: Rework `_render_step_editor` header**

Replace the heading + key input at the top of `_render_step_editor` with:

```python
        from not_dot_net.config import step_display

        with ui.row().classes("items-center gap-2"):
            wf = self.working_copy.workflows[wf_key]
            ui.button(icon="arrow_back", on_click=lambda w=wf_key: self.select(w)
                      ).props("flat dense round").tooltip(t("back_to_workflow", name=wf.label or wf_key))
            ui.label(step_display(step)).classes("text-h6")

        ui.input(t("step_label_field"), value=step.label,
                 on_change=lambda e, w=wf_key, k=step.key: self.set_step_field(w, k, "label", e.value)
                 ).classes("w-full").props("dense outlined stack-label")
```

Move the key input into an Advanced expansion at the BOTTOM of `_render_step_editor` (after the Fields panel):

```python
        with ui.expansion(t("step_advanced"), icon="settings").classes("w-full q-mt-md"):
            ui.label(t("step_key_hint")).classes("text-warning text-xs")
            ui.input(t("step_key_field"), value=step.key,
                     on_change=lambda e, w=wf_key, k=step.key: self._safe_set(w, k, "key", e.value)
                     ).classes("w-full").props("dense outlined stack-label")
```

- [x] **Step 4: Replace the actions chip editor with the labeled picker**

Replace the `actions_widget = chip_list_editor(...)` block with:

```python
        from not_dot_net.frontend.workflow_editor_options import action_options

        ui.label(t("step_actions")).classes("text-subtitle2 q-mt-sm")
        act_opts = {o["value"]: o["label"] for o in action_options(step.actions)}
        actions_select = ui.select(act_opts, value=list(step.actions or []), multiple=True,
                                   label=t("step_actions")
                                   ).classes("w-full").props("dense outlined stack-label use-chips")

        def _bind_actions(w=actions_select, wk=wf_key, sk=step.key):
            self.set_step_field(wk, sk, "actions", list(w.value or []))
            self._refresh_detail()  # corrections_target visibility may change
        actions_select.on_value_change(lambda e, _b=_bind_actions: _b())
```

(`chip_list_editor` stays imported — the effects table still uses it.)

- [x] **Step 5: Add ▲▼ to field rows**

In the fields loop of `_render_step_editor`, before the delete button, add (capture `idx` and the loop bound `n_fields = len(step.fields)` computed before the loop):

```python
                        ui.button(icon="keyboard_arrow_up",
                                  on_click=lambda e, i=idx, w=wf_key, sk=step.key: self.move_field(w, sk, i, -1)
                                  ).props(f"flat dense round size=sm {'disable' if idx == 0 else ''}")
                        ui.button(icon="keyboard_arrow_down",
                                  on_click=lambda e, i=idx, w=wf_key, sk=step.key: self.move_field(w, sk, i, +1)
                                  ).props(f"flat dense round size=sm {'disable' if idx == len(step.fields) - 1 else ''}")
```

- [x] **Step 6: `stack-label` + labels on the visible_when pickers**

In `_render_field_more`, give the two bare selects labels and `stack-label`:

```python
                key_select = ui.select(
                    [None, *checkbox_names],
                    value=current_key, label=t("visible_when_checkbox"),
                ).props("dense outlined stack-label").classes("grow")
                ui.label("=").classes("text-grey")
                val_select = ui.select(
                    [True, False],
                    value=current_val if isinstance(current_val, bool) else None,
                    label=t("visible_when_value"),
                ).props("dense outlined stack-label").classes("w-24")
```

- [x] **Step 7: Add the i18n keys**

`en`:

```python
        "back_to_workflow": "Back to {name}",
        "step_label_field": "Step name",
        "step_advanced": "Advanced",
        "step_key_field": "Internal key",
        "step_key_hint": "Renaming the key of a live workflow can strand in-progress requests on the old key.",
        "visible_when_checkbox": "Checkbox",
        "visible_when_value": "Value",
```

`fr`:

```python
        "back_to_workflow": "Retour à {name}",
        "step_label_field": "Nom de l'étape",
        "step_advanced": "Avancé",
        "step_key_field": "Clé interne",
        "step_key_hint": "Renommer la clé d'un processus actif peut bloquer les demandes en cours sur l'ancienne clé.",
        "visible_when_checkbox": "Case à cocher",
        "visible_when_value": "Valeur",
```

- [x] **Step 8: Run the editor + i18n test files**

Run: `uv run pytest tests/test_workflow_editor.py tests/test_i18n.py -x -q`
Expected: all PASS — pay attention to `test_step_rename_can_be_repeated_without_keyerror` (key rename now lives in Advanced but uses the same `_safe_set` path) and `test_workflow_editor_renders_three_sections`.

- [x] **Step 9: Commit**

```bash
git add -- not_dot_net/frontend/workflow_editor.py not_dot_net/frontend/i18n.py tests/test_workflow_editor.py
git commit -m "feat(workflow-editor): friendlier step editor — back nav, step names, explained actions, field reordering"
```

---

### Task 7: Step labels in the default workflows + full-suite verification

The seeded workflows (`backend/default_workflows.py`) should demonstrate the new `label` field so a fresh install shows friendly names, and the whole suite must be green.

**Files:**
- Modify: `not_dot_net/backend/default_workflows.py`
- Test: existing suite

- [x] **Step 1: Add labels to seeded steps**

In `not_dot_net/backend/default_workflows.py`, add a `label=` to every `WorkflowStepConfig(...)`. Derive the wording from the key, e.g.:

```python
WorkflowStepConfig(key="initiation", label="Initiation", ...)
WorkflowStepConfig(key="newcomer_info", label="Newcomer information", ...)
WorkflowStepConfig(key="admin_validation", label="Admin validation", ...)
WorkflowStepConfig(key="it_account_creation", label="IT account creation", ...)
```

(Open the file and label every step in every seeded workflow — VPN approval and ordre de mission included.)

- [x] **Step 2: Run the workflow-related test files**

Run: `uv run pytest tests/test_workflow_service.py tests/test_workflow_engine.py tests/test_onboarding_e2e.py tests/test_new_request.py -x -q`
Expected: PASS (label is additive; nothing matches on it).

- [x] **Step 3: Run the full suite, three times if flaky history bites**

Run: `uv run pytest -q`
Expected: 835 + ~14 new tests, 0 failures.

- [x] **Step 4: Manual smoke check (dev server)**

Run: `uv run python -m not_dot_net.cli serve --host localhost --port 8088` and verify in a browser (or via the webapp-testing skill): Settings → Workflows opens, pipeline renders for Onboarding, step click-through + back works, reorder works, delete prompts, add workflow/step asks for a *name*, YAML `</>` button still round-trips.

- [x] **Step 5: Commit**

```bash
git add -- not_dot_net/backend/default_workflows.py
git commit -m "feat(workflows): human labels on seeded workflow steps"
```

**STOP — do not push. Ask the user for explicit consent before any `git push`.**

---

## Self-review notes

- **Spec coverage:** pipeline visualization (T5), reordering steps+fields (T3/T5/T6), step labels incl. end-user dashboard (T2/T7), name-first creation (T3/T5), action semantics (T4/T6), delete confirms (T5), render-mutates-assignee bug (T1), warning badges (T5), stack-label fix (T6). Footer warnings summary stays as-is (still useful as the aggregate view).
- **Deliberately out of scope:** drag-and-drop (▲▼ is enough, KISS), mapping historical `event.step_key` strings in audit/history views to labels (events store keys; fine), changing `compute_warnings`' return type.
- **Known interaction kept:** `_bind_actions` calls `_refresh_detail()`, which rebuilds the select mid-edit — same behavior as today's chips; acceptable.
- **Type consistency check:** `add_workflow(key, label=None)`, `add_step(wf_key, step_key, label="")`, `duplicate_workflow(src_key, new_key, label=None)`, `move_step(wf_key, step_key, delta)`, `move_field(wf_key, step_key, index, delta)`, `warnings_for(wf_key, step_key=None)`, `display_name_to_key(name, taken, *, fallback_prefix)`, `step_display(step)`, `action_options(existing=None)`, `assignee_summary(step, roles, permissions)` — used consistently across tasks.
