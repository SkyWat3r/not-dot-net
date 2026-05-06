# Workflow conditional fields — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `checkbox` field type and a same-step `visible_when` predicate to the workflow form engine, plus the matching workflow-editor UI, so admins can build conditional sections (the first user being the ZRR onboarding extras) without any role/feature-specific code.

**Architecture:** A pure predicate `is_field_visible(field, data) -> bool` lives next to `FieldConfig` in `config.py`. The engine (`workflow_step._render_field`) gets a `checkbox` branch and wraps every field in a NiceGUI container whose `set_visibility()` is driven by that predicate; checkboxes that are referenced get an `on_value_change` handler that re-evaluates every container. The same predicate gates required-field validation in `validated_submit`. The workflow editor exposes the new properties: `checkbox` joins the type select, the per-row "More…" expander gets a `visible_when` picker, and `compute_warnings` flags dangling references.

**Tech Stack:** Python 3.10+, NiceGUI 3.4+, Pydantic 2, pytest, pytest-asyncio.

---

## File Structure

**Modify:**

- `not_dot_net/config.py` — extend `FieldConfig` schema; add pure `is_field_visible`.
- `not_dot_net/frontend/workflow_step.py` — checkbox render branch; container-wrapped fields; reactive visibility; required-validation skip.
- `not_dot_net/frontend/workflow_editor.py` — `checkbox` in field-type select; visible_when picker in `_render_field_more`; dangling-ref warning in `compute_warnings`.
- `not_dot_net/frontend/i18n.py` — keys `field_type_checkbox` and `wf_visible_when_help` in the `EN` and `FR` blocks.
- `tests/test_workflow_config.py` — round-trip test for the new properties.
- `tests/test_workflow_editor.py` — save/load test for `visible_when`; dangling-ref warning test.

**Create:**

- `tests/test_conditional_fields.py` — predicate unit tests.

No DB migration: `WorkflowsConfig` is JSON-serialized into `app_setting`; new optional fields deserialize cleanly on existing rows.

---

## Task 1: Extend `FieldConfig` schema and add the visibility predicate

**Files:**
- Modify: `not_dot_net/config.py:8-15`
- Test: `tests/test_conditional_fields.py` (new)

- [ ] **Step 1: Write failing predicate tests**

Create `tests/test_conditional_fields.py`:

```python
"""Pure-predicate tests for FieldConfig.visible_when — no NiceGUI."""

from not_dot_net.config import FieldConfig, is_field_visible


def test_no_rule_means_always_visible():
    f = FieldConfig(name="x", type="text")
    assert is_field_visible(f, {}) is True
    assert is_field_visible(f, {"anything": True}) is True


def test_rule_matches_value():
    f = FieldConfig(name="zrr_topic", type="text", visible_when={"zrr": True})
    assert is_field_visible(f, {"zrr": True}) is True
    assert is_field_visible(f, {"zrr": False}) is False
    assert is_field_visible(f, {}) is False  # missing key counts as mismatch


def test_rule_with_string_value():
    f = FieldConfig(name="cdd_end", type="date", visible_when={"status": "CDD"})
    assert is_field_visible(f, {"status": "CDD"}) is True
    assert is_field_visible(f, {"status": "CDI"}) is False


def test_rule_with_multiple_keys_is_and():
    """Future-proof: if more than one key/value pair, all must match."""
    f = FieldConfig(name="x", type="text", visible_when={"a": True, "b": "y"})
    assert is_field_visible(f, {"a": True, "b": "y"}) is True
    assert is_field_visible(f, {"a": True, "b": "z"}) is False
    assert is_field_visible(f, {"a": False, "b": "y"}) is False
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/test_conditional_fields.py -v
```

Expected: ImportError on `is_field_visible` (or `visible_when` field absent on `FieldConfig`).

- [ ] **Step 3: Extend `FieldConfig` and add the predicate**

In `not_dot_net/config.py`, replace the `FieldConfig` class and add the helper at module level:

```python
from typing import Any
from pydantic import BaseModel


class FieldConfig(BaseModel):
    name: str
    type: str  # text, email, textarea, date, select, file, phone, location, checkbox
    required: bool = False
    label: str = ""
    options_key: str | None = None
    encrypted: bool = False
    half_width: bool = False
    visible_when: dict[str, Any] | None = None


def is_field_visible(field: FieldConfig, data: dict) -> bool:
    """A field is visible iff every (key, value) in `visible_when` matches `data`.
    No rule means always visible. Missing keys are treated as mismatches."""
    rule = field.visible_when
    if not rule:
        return True
    return all(data.get(k) == v for k, v in rule.items())
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/test_conditional_fields.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Add round-trip test in `tests/test_workflow_config.py`**

Append to that file:

```python
async def test_field_config_round_trips_visible_when_and_checkbox():
    """A FieldConfig with type=checkbox and visible_when serializes and
    re-parses without loss through the workflows ConfigSection."""
    from not_dot_net.config import FieldConfig
    from not_dot_net.backend.workflow_service import workflows_config, WorkflowsConfig
    from not_dot_net.config import WorkflowConfig, WorkflowStepConfig

    cfg = WorkflowsConfig(workflows={
        "demo": WorkflowConfig(
            label="Demo",
            steps=[WorkflowStepConfig(
                key="s1", type="form",
                fields=[
                    FieldConfig(name="zrr", type="checkbox", label="zrr"),
                    FieldConfig(name="zrr_topic", type="text", label="zrr_topic",
                                visible_when={"zrr": True}),
                ],
                actions=["submit"],
            )],
        ),
    })
    await workflows_config.set(cfg)
    reloaded = await workflows_config.get()
    fields = reloaded.workflows["demo"].steps[0].fields
    assert fields[0].type == "checkbox"
    assert fields[1].visible_when == {"zrr": True}
```

If `workflows_config` lives elsewhere, grep for `workflows_config = section(` to confirm the import path before running.

- [ ] **Step 6: Run config tests**

```
uv run pytest tests/test_workflow_config.py -v
```

Expected: all green including the new test.

- [ ] **Step 7: Commit**

```bash
git add not_dot_net/config.py tests/test_conditional_fields.py tests/test_workflow_config.py
git commit -m "feat(workflow): add visible_when + checkbox to FieldConfig"
```

---

## Task 2: Render the `checkbox` field type

**Files:**
- Modify: `not_dot_net/frontend/workflow_step.py:43-131` (`_render_field`)
- Test: covered by Task 4 e2e + manual smoke check

This task adds the rendering branch only; reactivity and visibility wrapping come in Task 3.

- [ ] **Step 1: Add the branch in `_render_field`**

After the `field_cfg.type == "phone"` branch (around line 127) and before the `else:` fallback, insert:

```python
    elif field_cfg.type == "checkbox":
        fields[field_cfg.name] = ui.checkbox(
            text=label, value=bool(value)
        ).classes(width_class)
```

The label is the checkbox's caption (no separate floating label — Quasar checkboxes already render an inline label).

- [ ] **Step 2: Smoke-check the engine import**

```
uv run python -c "from not_dot_net.frontend.workflow_step import _render_field; print('ok')"
```

Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add not_dot_net/frontend/workflow_step.py
git commit -m "feat(workflow): render checkbox field type"
```

---

## Task 3: Wrap fields in visibility containers; wire reactivity

**Files:**
- Modify: `not_dot_net/frontend/workflow_step.py:134-197` (`render_step_form`)

Each field gets wrapped in a `ui.element('div')` container; the container's `set_visibility` is driven by `is_field_visible(field, current_state)`. After all fields render, every checkbox referenced by some `visible_when` rule receives an `on_value_change` callback that updates the shared state and re-evaluates every container.

- [ ] **Step 1: Refactor `render_step_form` to use containers + state**

Replace the body of `render_step_form` from the start through the date-pair / completion-indicator block. Show the changed region in full:

```python
async def render_step_form(
    step: WorkflowStepConfig,
    data: dict,
    on_submit,
    on_save_draft=None,
    files: dict | None = None,
    on_file_upload=None,
    max_upload_size_mb: int = 10,
):
    """Render a form step's fields. Returns dict of field name -> ui element."""
    from not_dot_net.config import is_field_visible

    fields: dict = {}
    containers: dict = {}
    state: dict = dict(data)  # mutable working copy for visibility predicates

    # Group consecutive half_width fields into pairs for row layout
    groups: list[list] = []
    for field_cfg in step.fields:
        if field_cfg.half_width:
            if groups and isinstance(groups[-1], list) and len(groups[-1]) < 2 and groups[-1][0].half_width:
                groups[-1].append(field_cfg)
            else:
                groups.append([field_cfg])
        else:
            groups.append([field_cfg])

    async def _wrap(field_cfg, width_class):
        with ui.element("div").classes(width_class) as container:
            await _render_field(field_cfg, data, fields, files, on_file_upload, max_upload_size_mb, "w-full")
        containers[field_cfg.name] = container
        container.set_visibility(is_field_visible(field_cfg, state))

    for group in groups:
        is_pair = len(group) == 2 and group[0].half_width
        if is_pair:
            with ui.row().classes("w-full gap-4"):
                for field_cfg in group:
                    await _wrap(field_cfg, "flex-1 min-w-[200px]")
        else:
            await _wrap(group[0], "w-full")

    # After every field is rendered, attach reactivity to checkboxes that
    # are referenced by any visible_when in this step.
    referenced = {
        key
        for f in step.fields
        if f.visible_when
        for key in f.visible_when.keys()
    }

    def _refresh_visibility():
        collected = _collect_data(fields)
        state.clear()
        state.update(collected)
        for f in step.fields:
            c = containers.get(f.name)
            if c is not None:
                c.set_visibility(is_field_visible(f, state))

    for ref_name in referenced:
        widget = fields.get(ref_name)
        if widget is not None and hasattr(widget, "on_value_change"):
            widget.on_value_change(lambda e, _r=_refresh_visibility: _r())

    # Date-pair: show duration when both departure_date and return_date are set
    if "departure_date" in fields and "return_date" in fields:
        _wire_date_pair(fields["departure_date"], fields["return_date"])

    # Completion status for partial-save steps
    if step.partial_save:
        _render_completion_indicator(step, data, files or {})
```

The downstream submit/save buttons (lines 174-195 of the original) stay as they were.

- [ ] **Step 2: Smoke-check imports**

```
uv run python -c "from not_dot_net.frontend.workflow_step import render_step_form; print('ok')"
```

Expected: `ok`.

- [ ] **Step 3: Run the existing onboarding e2e and editor tests as a regression**

```
uv run pytest tests/test_onboarding_e2e.py tests/test_workflow_detail.py tests/test_workflow_editor.py -v
```

Expected: all green (visible_when not yet exercised, but the wrapping must not break existing flows).

- [ ] **Step 4: Commit**

```bash
git add not_dot_net/frontend/workflow_step.py
git commit -m "feat(workflow): wrap fields in visibility containers for visible_when"
```

---

## Task 4: Skip required-validation for hidden fields

**Files:**
- Modify: `not_dot_net/frontend/workflow_step.py:180-193` (`validated_submit`)

- [ ] **Step 1: Update `validated_submit`**

Replace the `missing = [...]` comprehension to also skip fields whose `visible_when` predicate fails against the collected data:

```python
        async def validated_submit():
            from not_dot_net.config import is_field_visible
            collected = _collect_data(fields)
            missing = [
                t(f.label) if f.label else f.name for f in step.fields
                if f.required
                and f.type != "file"
                and is_field_visible(f, collected)
                and not collected.get(f.name)
            ]
            if missing:
                ui.notify(f"{t('required_field')}: {', '.join(missing)}", color="negative")
                return
            error = _validate_date_pair(collected)
            if error:
                ui.notify(error, color="negative")
                return
            await on_submit(collected)
```

No corresponding change is needed in `workflow_detail.py` — file uploads are validated at the engine layer (`workflow_service.validate_upload`) which doesn't check required-presence. Required-presence is purely a form-level concern handled here.

- [ ] **Step 2: Update the completion indicator to skip hidden fields**

Replace `_render_completion_indicator` (lines 200-210):

```python
def _render_completion_indicator(step: WorkflowStepConfig, data: dict, files: dict):
    """Show which required, currently-visible fields are filled (partial save)."""
    from not_dot_net.config import is_field_visible
    required = [f for f in step.fields if f.required and is_field_visible(f, data)]
    if not required:
        return
    filled = sum(
        1 for f in required
        if (f.type == "file" and files.get(f.name)) or (f.type != "file" and data.get(f.name))
    )
    ui.linear_progress(value=filled / len(required)).classes("w-full mb-2")
    ui.label(f"{filled}/{len(required)}").classes("text-sm text-grey")
```

- [ ] **Step 3: Add a test for the predicate-driven required skip**

Append to `tests/test_conditional_fields.py`:

```python
def test_required_skip_uses_same_predicate():
    """Demonstrate the contract: if is_field_visible is False, callers can
    treat the field as 'effectively not required'. (The render and submit
    paths both use this exact rule.)"""
    f = FieldConfig(name="zrr_topic", type="text", required=True,
                    visible_when={"zrr": True})

    # Required + hidden → skip (predicate returns False)
    assert is_field_visible(f, {"zrr": False}) is False

    # Required + visible + empty → caller will flag as missing
    assert is_field_visible(f, {"zrr": True}) is True
```

- [ ] **Step 4: Run all conditional-field tests**

```
uv run pytest tests/test_conditional_fields.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Run the full suite as regression**

```
uv run pytest --tb=short
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add not_dot_net/frontend/workflow_step.py tests/test_conditional_fields.py
git commit -m "feat(workflow): skip required-validation for hidden fields"
```

---

## Task 5: Editor — add `checkbox` to the field-type select

**Files:**
- Modify: `not_dot_net/frontend/workflow_editor.py:594` (the `ui.select` of types)
- Modify: `not_dot_net/frontend/i18n.py` (`EN` and `FR` blocks)

- [ ] **Step 1: Add the i18n key**

In `not_dot_net/frontend/i18n.py`, find the block that contains `"field_more": "More…"` (English) and `"field_more": "Plus…"` (French). Insert next to them:

English:
```python
        "field_type_checkbox": "Checkbox",
```

French:
```python
        "field_type_checkbox": "Case à cocher",
```

- [ ] **Step 2: Add `checkbox` to the type select**

In `not_dot_net/frontend/workflow_editor.py:594`, change:

```python
                    ui.select(["text", "email", "phone", "textarea", "date", "select", "file", "location"],
```

to:

```python
                    ui.select(["text", "email", "phone", "textarea", "date", "select", "file", "location", "checkbox"],
```

(The select uses raw type strings as both value and label today — keep that consistency. The new i18n key is for the visible_when picker in Task 6.)

- [ ] **Step 3: Run editor tests**

```
uv run pytest tests/test_workflow_editor.py tests/test_i18n.py -v
```

Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add not_dot_net/frontend/workflow_editor.py not_dot_net/frontend/i18n.py
git commit -m "feat(editor): expose checkbox in the field-type select"
```

---

## Task 6: Editor — `visible_when` picker in the More… expander

**Files:**
- Modify: `not_dot_net/frontend/workflow_editor.py:616-642` (`_render_field_more`)
- Modify: `not_dot_net/frontend/workflow_editor.py:295-323` (`set_field_attr`) — verify it accepts `visible_when` (it currently uses `setattr`, so it should "just work"; if there's an allowlist, add the field).
- Modify: `not_dot_net/frontend/i18n.py`
- Modify: `tests/test_workflow_editor.py` (save/load coverage)

The picker is two selects: which sibling checkbox controls visibility, and the required value (`true`/`false`). Empty controlling field = no rule.

- [ ] **Step 1: Add the i18n key**

In `not_dot_net/frontend/i18n.py`, alongside the previous additions:

English:
```python
        "wf_visible_when_help": "Show this field only when",
```

French:
```python
        "wf_visible_when_help": "Afficher ce champ seulement si",
```

- [ ] **Step 2: Render the picker**

`set_field_attr` uses bare `setattr(field, attr, value)` — no allowlist, so the new attribute "just works" as soon as Task 1's schema lands.

In `_render_field_more`, before the closing of the `with ui.column()` block (after the `field_encrypted` switch at line 642), insert:

```python
            # visible_when picker — same-step checkbox + value, v1
            wf = self.working_copy.workflows[wf_key]
            step = next(s for s in wf.steps if s.key == step_key)
            checkbox_names = [f.name for f in step.fields
                              if f.type == "checkbox" and f.name != field.name]
            current_when = field.visible_when or {}
            current_key = next(iter(current_when), None) if current_when else None
            current_val = current_when.get(current_key) if current_key else None

            ui.label(t("wf_visible_when_help")).classes("text-sm q-mt-sm")
            with ui.row().classes("w-full items-center gap-2"):
                key_select = ui.select(
                    [None, *checkbox_names],
                    value=current_key,
                ).props("dense outlined").classes("grow")
                ui.label("=").classes("text-grey")
                val_select = ui.select(
                    [True, False],
                    value=current_val if isinstance(current_val, bool) else None,
                ).props("dense outlined").classes("w-24")

            def _apply(_e=None, w=wf_key, sk=step_key, i=idx,
                       ks=key_select, vs=val_select):
                k, v = ks.value, vs.value
                rule = {k: v} if k and v is not None else None
                self.set_field_attr(w, sk, i, "visible_when", rule)
                self._refresh_detail()

            key_select.on_value_change(_apply)
            val_select.on_value_change(_apply)
```

The selects use no `label=` so they don't need extra i18n keys; the help label above the row reads as the sentence-form "Show this field only when [select] = [select]".

- [ ] **Step 3: Run editor + i18n tests**

```
uv run pytest tests/test_workflow_editor.py tests/test_i18n.py -v
```

Expected: all green. The Task 1 config round-trip already proves persistence; no additional editor save/load test is needed.

- [ ] **Step 4: Commit**

```bash
git add not_dot_net/frontend/workflow_editor.py not_dot_net/frontend/i18n.py tests/test_workflow_editor.py
git commit -m "feat(editor): visible_when picker in field More expander"
```

---

## Task 7: Editor — flag dangling `visible_when` references

**Files:**
- Modify: `not_dot_net/frontend/workflow_editor.py:720-766` (`compute_warnings`)
- Modify: `tests/test_workflow_editor.py` (warning test)

- [ ] **Step 1: Extend `compute_warnings`**

Inside the `for step in wf.steps:` loop in `compute_warnings`, after the existing `for f in step.fields: ... options_key` block, append:

```python
                checkbox_names = {f.name for f in step.fields if f.type == "checkbox"}
                for f in step.fields:
                    if not f.visible_when:
                        continue
                    for k in f.visible_when:
                        if k not in checkbox_names:
                            warnings.append(
                                f"[{wf_key}/{step.key}/{f.name}] visible_when references "
                                f"'{k}' which is not a checkbox in the same step"
                            )
```

- [ ] **Step 2: Add a test**

In `tests/test_workflow_editor.py`, append (uses the existing `WorkflowEditorDialog.create(admin_user)` pattern that other tests in this file use):

```python
async def test_compute_warnings_flags_dangling_visible_when(user: User, admin_user):
    from not_dot_net.config import FieldConfig, WorkflowConfig, WorkflowStepConfig
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog

    await workflows_config.set(WorkflowsConfig(workflows={
        "demo": WorkflowConfig(
            label="Demo",
            steps=[WorkflowStepConfig(
                key="s1", type="form",
                fields=[
                    # dangling: 'zrr' does not exist in this step
                    FieldConfig(name="zrr_topic", type="text",
                                visible_when={"zrr": True}),
                    # wrong type: 'status' exists but is not a checkbox
                    FieldConfig(name="status", type="select"),
                    FieldConfig(name="cdd_end", type="date",
                                visible_when={"status": "CDD"}),
                ],
                actions=["submit"],
            )],
        ),
    }))

    captured = {}

    @ui.page("/_warn_visible_when")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_warn_visible_when")
    dlg = captured["dlg"]
    warnings = dlg.compute_warnings()
    assert any("zrr_topic" in w and "'zrr'" in w for w in warnings)
    assert any("cdd_end" in w and "'status'" in w for w in warnings)
```

The imports at the top of this test file (`workflows_config`, `WorkflowsConfig`, `ui`, `User`, `pytest` fixtures) are already present.

- [ ] **Step 3: Run editor tests**

```
uv run pytest tests/test_workflow_editor.py -v
```

Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add not_dot_net/frontend/workflow_editor.py tests/test_workflow_editor.py
git commit -m "feat(editor): warn on dangling visible_when references"
```

---

## Task 8: Final regression and push

- [ ] **Step 1: Run the full suite**

```
uv run pytest --tb=short
```

Expected: all green, no skipped tests beyond pre-existing skips.

- [ ] **Step 2: Manual smoke check (optional but recommended)**

Start the dev server (`uv run python -m not_dot_net.cli serve --host localhost --port 8088`), open `/` → Settings → Workflows → onboarding → newcomer_info step → Add a `checkbox` field named `zrr` (label "ZRR access required") → Add a `text` field "ZRR research topic" with the More expander's visible_when set to `zrr = True` → Save. Then open a new onboarding request token page and confirm the topic field appears only when the checkbox is ticked.

- [ ] **Step 3: Push**

Ask the user before pushing — per session memory, every push needs explicit consent.

---

## Notes for the implementer

- **Why a single mutable `state` dict instead of binding each container directly to a checkbox:** the predicate could in theory reference any sibling field (the spec says checkbox-only for v1, but the engine code shouldn't assume that). Recomputing visibility from a fresh `_collect_data(fields)` snapshot keeps the engine future-proof for richer predicates without rewriting the reactivity wiring.
- **Why no destructive clearing on toggle:** if an admin toggles ZRR off and back on while editing, they don't lose data. Hidden values stay in `data` and are simply not validated.
- **Why same-step only for v1:** cross-step visible_when would need engine-side data from prior steps to be readable in the current step's predicate, plus validation rules that span step boundaries. Out of scope.
- **Why no encryption support for checkbox fields:** booleans don't carry sensitive payloads on their own; the dependent text/file fields can still set `encrypted=True` independently.
