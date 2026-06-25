# Reusable Field Definitions (Vocab Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an admin define a workflow field once in a shared library and reference it from any workflow step, with live "edit once, applies everywhere" semantics and optional per-use overrides.

**Architecture:** A new `field_definitions` ConfigSection (JSON blob, like the Phase-1 vocabulary registry) holds named `FieldDefinition`s. A step's `fields` list widens to a union of inline `FieldConfig` and a new `FieldRef`. One pure merge (`resolve_field_ref`) and one async resolver (`resolve_step_fields`) turn a step into a flat `list[FieldConfig]`; every runtime consumer (render, validate, display, token-filter) goes through the resolver, so the engine stays pure.

**Tech Stack:** Python 3.10+, Pydantic v2, SQLAlchemy 2.x async, NiceGUI 3.4+, pytest + `nicegui.testing.User`.

## Global Constraints

- **Additive & opt-in.** Existing inline fields and stored workflows behave exactly as today. No data migration.
- **No new table, no Alembic migration.** Storage is a ConfigSection JSON row, mirroring `backend/vocabularies.py`.
- **No seeding, no built-in definitions** in v1. The registry starts empty; the admin populates it. Do not add a startup seed call.
- **Permission:** all admin surfaces gate on `manage_settings` via `check_permission`.
- **Definition key is immutable** after creation; created name-first via `display_name_to_key(name, taken, fallback_prefix="field")`.
- **Field identity is not overridable:** a reference's resolved `name` is always the definition's `key`.
- **`label` is a single string** (not locale-keyed), matching today's `FieldConfig.label`.
- **Engine purity:** `backend/workflow_engine.py` must not load config. Resolution happens at the service/frontend boundary.
- **KISS.** Follow existing patterns (`vocabularies.py` / `vocabularies_editor.py`) move-for-move; do not introduce new abstractions.
- **Tests:** baseline is 919 passing. The full suite takes ~120s and repeated runs fill `/tmp/pytest-of-*` (ENOSPC). Run only the new/affected test files during development; run the full suite once at the end.

---

## File Structure

- **New** `not_dot_net/backend/field_definitions.py` — `FieldDefinition`, `FieldDefinitionsConfig`, the `field_definitions_config` section, `resolve_step_fields`, `save_field_definition`, `delete_field_definition`, `FieldDefinitionInUse`.
- **New** `not_dot_net/frontend/field_definitions_editor.py` — admin field-library page (mirrors `vocabularies_editor.py`).
- **New** `tests/test_field_definitions.py` — backend + editor-logic unit tests.
- **New** `tests/test_field_definitions_ui.py` — NiceGUI integration tests.
- **Modify** `not_dot_net/config.py` — `FieldRef`, widened `WorkflowStepConfig.fields` union, `resolve_field_ref`.
- **Modify** `not_dot_net/frontend/workflow_step.py` — resolve in `render_step_form`, `_render_completion_indicator`, `resolve_display_values`.
- **Modify** `not_dot_net/frontend/workflow_detail.py` — resolve in the `field_labels` map.
- **Modify** `not_dot_net/backend/workflow_service.py` — `_filter_step_data` async + resolve.
- **Modify** `not_dot_net/frontend/admin_settings.py` — mount the new editor + skip `field_definitions` in the auto-render loop.
- **Modify** `not_dot_net/frontend/workflow_editor.py` — definitions snapshot, ref mutations, union-safe read paths, dangling-ref warning, ref row + override dialog + "use shared field" picker.
- **Modify** `not_dot_net/frontend/i18n.py` — new translation keys.

---

## Task 1: Data model + pure resolver

**Files:**
- Modify: `not_dot_net/config.py`
- Create: `not_dot_net/backend/field_definitions.py` (models only in this task)
- Test: `tests/test_field_definitions.py`

**Interfaces:**
- Produces:
  - `FieldDefinition(key: str, type: str, label: str = "", required: bool = False, options_key: str | None = None, encrypted: bool = False, half_width: bool = False)` in `backend/field_definitions.py`
  - `FieldDefinitionsConfig(definitions: dict[str, FieldDefinition])` + `field_definitions_config` section
  - `FieldRef(ref: str, type=None, label=None, required=None, options_key=None, encrypted=None, half_width=None, visible_when=None)` in `config.py`
  - `WorkflowStepConfig.fields: list[FieldConfig | FieldRef]` (deserialized left-to-right)
  - `resolve_field_ref(ref: FieldRef, defn: FieldDefinition) -> FieldConfig` in `config.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_field_definitions.py`:

```python
from not_dot_net.config import FieldConfig, FieldRef, WorkflowStepConfig, resolve_field_ref
from not_dot_net.backend.field_definitions import FieldDefinition


def test_resolve_inherits_unset_properties():
    defn = FieldDefinition(key="phone", type="phone", label="Phone",
                           required=True, half_width=True)
    resolved = resolve_field_ref(FieldRef(ref="phone"), defn)
    assert resolved.name == "phone"
    assert resolved.type == "phone"
    assert resolved.label == "Phone"
    assert resolved.required is True
    assert resolved.half_width is True
    assert resolved.visible_when is None


def test_resolve_override_to_false_beats_inherit():
    defn = FieldDefinition(key="phone", type="phone", required=True)
    resolved = resolve_field_ref(FieldRef(ref="phone", required=False), defn)
    assert resolved.required is False


def test_resolve_name_not_overridable_and_visible_when_is_local():
    defn = FieldDefinition(key="phone", type="phone")
    ref = FieldRef(ref="phone", visible_when={"needs": True})
    resolved = resolve_field_ref(ref, defn)
    assert resolved.name == "phone"
    assert resolved.visible_when == {"needs": True}


def test_step_fields_union_deserializes_both_shapes():
    step = WorkflowStepConfig.model_validate({
        "key": "s1", "type": "form",
        "fields": [
            {"name": "note", "type": "text"},
            {"ref": "phone", "required": True},
        ],
    })
    assert isinstance(step.fields[0], FieldConfig)
    assert isinstance(step.fields[1], FieldRef)
    assert step.fields[1].ref == "phone"
    assert step.fields[1].required is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_field_definitions.py -v`
Expected: FAIL — `ImportError: cannot import name 'FieldRef'` / `FieldDefinition`.

- [ ] **Step 3: Create the definition models**

Create `not_dot_net/backend/field_definitions.py`:

```python
"""App-wide reusable field definitions — Vocab registry Phase 2.

A FieldDefinition describes a workflow field once (type, label, required,
vocabulary binding, encrypted, layout). Workflow steps reference it by key
(via config.FieldRef) and resolve it live. Stored in one app_setting JSON
row, the ConfigSection idiom — no table, no migration.
"""

import logging

from pydantic import BaseModel, Field

from not_dot_net.backend.app_config import section
from not_dot_net.config import FieldConfig, FieldRef, WorkflowStepConfig, resolve_field_ref

_log = logging.getLogger(__name__)


class FieldDefinition(BaseModel):
    key: str                       # immutable registry key; also the resolved field's data name
    type: str                      # text | email | textarea | date | select | file | phone | location | checkbox
    label: str = ""
    required: bool = False
    options_key: str | None = None # vocabulary binding (select), resolved via the Phase-1 registry
    encrypted: bool = False
    half_width: bool = False


class FieldDefinitionsConfig(BaseModel):
    definitions: dict[str, FieldDefinition] = Field(default_factory=dict)


field_definitions_config = section("field_definitions", FieldDefinitionsConfig,
                                   label="Field definitions")
```

- [ ] **Step 4: Add `FieldRef`, widen the union, and add the pure resolver in `config.py`**

In `not_dot_net/config.py`, change the imports at the top:

```python
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

from not_dot_net.backend.app_config import section

if TYPE_CHECKING:
    from not_dot_net.backend.field_definitions import FieldDefinition
```

After the `FieldConfig` class and `is_field_visible` function, add:

```python
class FieldRef(BaseModel):
    """A workflow step's reference to a shared FieldDefinition.

    Every override is Optional; None means "inherit live from the definition".
    `name` is intentionally absent — a reference's resolved name is the
    definition key, so a shared field always stores under one data key.
    """
    ref: str
    type: str | None = None
    label: str | None = None
    required: bool | None = None
    options_key: str | None = None
    encrypted: bool | None = None
    half_width: bool | None = None
    visible_when: dict[str, Any] | None = None   # step-local only (never in the definition)


def resolve_field_ref(ref: "FieldRef", defn: "FieldDefinition") -> FieldConfig:
    """Merge a reference over its definition into a concrete FieldConfig."""
    def pick(override, base):
        return override if override is not None else base
    return FieldConfig(
        name=defn.key,
        type=pick(ref.type, defn.type),
        label=pick(ref.label, defn.label),
        required=pick(ref.required, defn.required),
        options_key=pick(ref.options_key, defn.options_key),
        encrypted=pick(ref.encrypted, defn.encrypted),
        half_width=pick(ref.half_width, defn.half_width),
        visible_when=ref.visible_when,
    )


StepField = Annotated[FieldConfig | FieldRef, Field(union_mode="left_to_right")]
```

Then change `WorkflowStepConfig.fields` from `fields: list[FieldConfig] = []` to:

```python
    fields: list[StepField] = []
```

(`StepField` and `FieldRef` must be defined above `WorkflowStepConfig` in the file — move the `FieldRef`/`resolve_field_ref`/`StepField` block above the `WorkflowStepConfig` class, keeping `FieldConfig` and `is_field_visible` first.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_field_definitions.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add not_dot_net/config.py not_dot_net/backend/field_definitions.py tests/test_field_definitions.py
git commit -m "feat(fields): FieldDefinition model + FieldRef union + pure resolver"
```

---

## Task 2: `resolve_step_fields` async resolver

**Files:**
- Modify: `not_dot_net/backend/field_definitions.py`
- Test: `tests/test_field_definitions.py`

**Interfaces:**
- Consumes: `FieldDefinition`, `field_definitions_config` (Task 1); `FieldRef`, `resolve_field_ref` (Task 1)
- Produces: `async resolve_step_fields(step: WorkflowStepConfig, *, cfg: FieldDefinitionsConfig | None = None) -> list[FieldConfig]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_field_definitions.py`:

```python
import pytest
from not_dot_net.backend.field_definitions import (
    FieldDefinitionsConfig, resolve_step_fields,
)


@pytest.mark.anyio
async def test_resolve_step_fields_mixes_inline_and_refs_in_order():
    cfg = FieldDefinitionsConfig(definitions={
        "phone": FieldDefinition(key="phone", type="phone", label="Phone"),
    })
    step = WorkflowStepConfig(key="s", type="form", fields=[
        FieldConfig(name="note", type="text"),
        FieldRef(ref="phone", required=True),
    ])
    resolved = await resolve_step_fields(step, cfg=cfg)
    assert [f.name for f in resolved] == ["note", "phone"]
    assert resolved[1].type == "phone"
    assert resolved[1].required is True


@pytest.mark.anyio
async def test_resolve_step_fields_drops_dangling_ref():
    cfg = FieldDefinitionsConfig(definitions={})
    step = WorkflowStepConfig(key="s", type="form", fields=[
        FieldConfig(name="note", type="text"),
        FieldRef(ref="gone"),
    ])
    resolved = await resolve_step_fields(step, cfg=cfg)
    assert [f.name for f in resolved] == ["note"]
```

Note: the project's pytest is configured for async tests (the suite already runs `async def` tests). If `@pytest.mark.anyio` is not the convention used elsewhere, match the existing convention — check another async test (e.g. `tests/test_vocabularies.py`) and copy its decorator/fixture style verbatim.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_field_definitions.py -k resolve_step_fields -v`
Expected: FAIL — `ImportError: cannot import name 'resolve_step_fields'`.

- [ ] **Step 3: Implement `resolve_step_fields`**

Append to `not_dot_net/backend/field_definitions.py`:

```python
async def resolve_step_fields(
    step: WorkflowStepConfig, *, cfg: FieldDefinitionsConfig | None = None
) -> list[FieldConfig]:
    """Flatten a step's fields: inline fields pass through; references resolve
    against their definition. A reference whose definition is missing is dropped
    (deletion is normally blocked; this guards hand-edited/imported configs)."""
    if cfg is None:
        cfg = await field_definitions_config.get()
    resolved: list[FieldConfig] = []
    for item in step.fields:
        if isinstance(item, FieldRef):
            defn = cfg.definitions.get(item.ref)
            if defn is None:
                _log.warning("step %r references unknown field definition %r — dropped",
                             step.key, item.ref)
                continue
            resolved.append(resolve_field_ref(item, defn))
        else:
            resolved.append(item)
    return resolved
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_field_definitions.py -k resolve_step_fields -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/backend/field_definitions.py tests/test_field_definitions.py
git commit -m "feat(fields): resolve_step_fields async resolver"
```

---

## Task 3: Definition CRUD with delete-in-use protection

**Files:**
- Modify: `not_dot_net/backend/field_definitions.py`
- Test: `tests/test_field_definitions.py`

**Interfaces:**
- Consumes: `field_definitions_config`, `FieldDefinition` (Task 1); `workflows_config`, `WorkflowsConfig` from `not_dot_net.backend.workflow_service`
- Produces:
  - `async save_field_definition(defn: FieldDefinition) -> None`
  - `async delete_field_definition(key: str) -> None` (raises `FieldDefinitionInUse`)
  - `async definition_usages(key: str) -> list[str]` (returns `"wf_key/step_key"` strings)
  - `class FieldDefinitionInUse(Exception)` with `.key` and `.usages`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_field_definitions.py`:

```python
from not_dot_net.backend.field_definitions import (
    field_definitions_config, save_field_definition, delete_field_definition,
    FieldDefinitionInUse,
)
from not_dot_net.backend.workflow_service import workflows_config, WorkflowsConfig
from not_dot_net.config import WorkflowConfig


@pytest.mark.anyio
async def test_save_then_delete_unused_definition():
    await save_field_definition(FieldDefinition(key="phone", type="phone"))
    cfg = await field_definitions_config.get()
    assert "phone" in cfg.definitions
    await delete_field_definition("phone")
    cfg = await field_definitions_config.get()
    assert "phone" not in cfg.definitions


@pytest.mark.anyio
async def test_delete_in_use_definition_is_blocked():
    await save_field_definition(FieldDefinition(key="phone", type="phone"))
    await workflows_config.set(WorkflowsConfig(workflows={
        "onboard": WorkflowConfig(label="Onboard", steps=[
            WorkflowStepConfig(key="info", type="form", fields=[FieldRef(ref="phone")]),
        ]),
    }))
    with pytest.raises(FieldDefinitionInUse) as exc:
        await delete_field_definition("phone")
    assert "onboard/info" in exc.value.usages
    cfg = await field_definitions_config.get()
    assert "phone" in cfg.definitions   # not deleted
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_field_definitions.py -k definition -v`
Expected: FAIL — `ImportError: cannot import name 'save_field_definition'`.

- [ ] **Step 3: Implement CRUD + usage scan**

Append to `not_dot_net/backend/field_definitions.py`:

```python
class FieldDefinitionInUse(Exception):
    def __init__(self, key: str, usages: list[str]):
        self.key = key
        self.usages = usages
        super().__init__(f"field definition '{key}' is used by: {', '.join(usages)}")


async def save_field_definition(defn: FieldDefinition) -> None:
    cfg = await field_definitions_config.get()
    cfg.definitions[defn.key] = defn
    await field_definitions_config.set(cfg)


async def definition_usages(key: str) -> list[str]:
    from not_dot_net.backend.workflow_service import workflows_config
    wf_cfg = await workflows_config.get()
    usages: list[str] = []
    for wf_key, wf in wf_cfg.workflows.items():
        for step in wf.steps:
            for item in step.fields:
                if isinstance(item, FieldRef) and item.ref == key:
                    usages.append(f"{wf_key}/{step.key}")
    return usages


async def delete_field_definition(key: str) -> None:
    usages = await definition_usages(key)
    if usages:
        raise FieldDefinitionInUse(key, usages)
    cfg = await field_definitions_config.get()
    cfg.definitions.pop(key, None)
    await field_definitions_config.set(cfg)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_field_definitions.py -k definition -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/backend/field_definitions.py tests/test_field_definitions.py
git commit -m "feat(fields): definition CRUD with delete-in-use protection"
```

---

## Task 4: Runtime resolution — render, validate, display

**Files:**
- Modify: `not_dot_net/frontend/workflow_step.py`
- Modify: `not_dot_net/frontend/workflow_detail.py`
- Test: `tests/test_field_definitions_ui.py`

**Interfaces:**
- Consumes: `resolve_step_fields`, `field_definitions_config` (Tasks 2/1)
- Produces: workflow step forms and the request detail page render referenced fields resolved.

- [ ] **Step 1: Write the failing integration test**

Create `tests/test_field_definitions_ui.py`. Use the same NiceGUI `user` fixture style as `tests/test_vocabularies_editor.py` (open it first and copy the imports, fixture usage, and page-navigation idioms verbatim). The test:

```python
import pytest
from nicegui.testing import User

from not_dot_net.backend.field_definitions import (
    FieldDefinition, FieldDefinitionsConfig, field_definitions_config,
)
from not_dot_net.backend.workflow_service import workflows_config, WorkflowsConfig
from not_dot_net.config import WorkflowConfig, WorkflowStepConfig, FieldRef


@pytest.mark.anyio
async def test_referenced_field_renders_with_definition_label(user: User) -> None:
    await field_definitions_config.set(FieldDefinitionsConfig(definitions={
        "phone": FieldDefinition(key="phone", type="phone", label="Phone number"),
    }))
    await workflows_config.set(WorkflowsConfig(workflows={
        "mission": WorkflowConfig(label="Mission", steps=[
            WorkflowStepConfig(key="info", type="form", fields=[FieldRef(ref="phone")]),
        ]),
    }))
    # Drive the new-request page for "mission" as a logged-in active user, then:
    await user.open("/")            # adjust to the real new-request navigation
    # ... navigate to the mission new-request form ...
    await user.should_see("Phone number")
```

Match the actual navigation against an existing new-request UI test (search `tests/` for `new_request` or `should_see` on a workflow form) and copy its setup. The assertion that matters: the rendered form shows the **definition's** label for the referenced field.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_field_definitions_ui.py -k renders_with_definition_label -v`
Expected: FAIL — the field renders with the raw ref/empty label (or errors) because `render_step_form` does not resolve refs yet.

- [ ] **Step 3: Resolve in `render_step_form`**

In `not_dot_net/frontend/workflow_step.py`, add the import near the top (with the other backend imports):

```python
from not_dot_net.backend.field_definitions import resolve_step_fields
```

In `render_step_form`, immediately after the `ad_account_creation` early-return and before the `groups` loop, resolve once and use the resolved list everywhere the function currently reads `step.fields`:

```python
    resolved_fields = await resolve_step_fields(step)
```

Then replace, within `render_step_form`:
- the grouping loop header `for field_cfg in step.fields:` → `for field_cfg in resolved_fields:`
- the `referenced = { ... for f in step.fields ... }` comprehension → `... for f in resolved_fields ...`
- inside `_refresh_visibility`, `for f in step.fields:` → `for f in resolved_fields:`
- the `validated_submit` `missing = [... for f in step.fields ...]` → `... for f in resolved_fields ...`
- the partial-save call `_render_completion_indicator(step, data, files or {})` → `_render_completion_indicator(resolved_fields, data, files or {})`

- [ ] **Step 4: Update `_render_completion_indicator` to take resolved fields**

Change its signature and body in `not_dot_net/frontend/workflow_step.py`:

```python
def _render_completion_indicator(fields: list, data: dict, files: dict):
    """Show which required, currently-visible fields are filled (partial save)."""
    from not_dot_net.config import is_field_visible
    required = [f for f in fields if f.required and is_field_visible(f, data)]
    if not required:
        return
    filled = sum(
        1 for f in required
        if (f.type == "file" and files.get(f.name)) or (f.type != "file" and data.get(f.name))
    )
    ui.linear_progress(value=filled / len(required)).classes("w-full mb-2")
    ui.label(f"{filled}/{len(required)}").classes("text-sm text-grey")
```

- [ ] **Step 5: Resolve in `resolve_display_values`**

Replace the `field_keys` comprehension in `resolve_display_values` (`not_dot_net/frontend/workflow_step.py`) with a resolved loop:

```python
async def resolve_display_values(workflow, data: dict, locale: str) -> dict[str, str]:
    """Map a request's stored values to display strings, resolving select codes
    (which may differ from their label, e.g. nationalities) to their label."""
    defs_cfg = await field_definitions_config.get()
    field_keys: dict[str, str] = {}
    for s in workflow.steps:
        for f in await resolve_step_fields(s, cfg=defs_cfg):
            if f.type == "select" and f.options_key:
                field_keys[f.name] = f.options_key
    resolved: dict[str, str] = {}
    for key, value in data.items():
        options_key = field_keys.get(key)
        if options_key and value:
            terms = {term.code: term for term in await resolve_terms(options_key, active_only=False)}
            term = terms.get(value)
            resolved[key] = term_label(term, locale) if term else value
        else:
            resolved[key] = value
    return resolved
```

Add the import at the top of the file:

```python
from not_dot_net.backend.field_definitions import resolve_step_fields, field_definitions_config
```

(Combine with the import added in Step 3 — one import line.)

- [ ] **Step 6: Resolve in `workflow_detail.py` field-label map**

In `not_dot_net/frontend/workflow_detail.py`, replace the `field_labels` comprehension (around lines 93-96):

```python
            from not_dot_net.backend.field_definitions import resolve_step_fields, field_definitions_config
            _defs_cfg = await field_definitions_config.get()
            field_labels = {}
            for step in wf.steps:
                for f in await resolve_step_fields(step, cfg=_defs_cfg):
                    field_labels[f.name] = t(f.label) if f.label else f.name
```

(If the enclosing function is not already `async`, it is — this view awaits DB calls already. Place the import at module top if preferred.)

- [ ] **Step 7: Run the test to verify it passes**

Run: `uv run pytest tests/test_field_definitions_ui.py -k renders_with_definition_label -v`
Expected: PASS.

- [ ] **Step 8: Add an override + edit-once integration test, run it**

Append two tests to `tests/test_field_definitions_ui.py`: (a) a `FieldRef(ref="phone", required=True)` makes the rendered field required (submitting empty shows the required-field notice); (b) editing the definition's label via `save_field_definition` and re-rendering shows the new label. Run:

Run: `uv run pytest tests/test_field_definitions_ui.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add not_dot_net/frontend/workflow_step.py not_dot_net/frontend/workflow_detail.py tests/test_field_definitions_ui.py
git commit -m "feat(fields): resolve referenced fields in render/validate/display"
```

---

## Task 5: Token data filter resolves referenced names (security)

**Files:**
- Modify: `not_dot_net/backend/workflow_service.py`
- Test: `tests/test_field_definitions.py`

**Interfaces:**
- Consumes: `resolve_step_fields` (Task 2)
- Produces: `async _filter_step_data(step_cfg, data) -> dict` (now async); callers in `submit_step`/`save_draft` await it.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_field_definitions.py`:

```python
from not_dot_net.backend.workflow_service import _filter_step_data


@pytest.mark.anyio
async def test_filter_step_data_allows_resolved_ref_name_rejects_injection():
    await save_field_definition(FieldDefinition(key="phone", type="phone"))
    step = WorkflowStepConfig(key="info", type="form", fields=[
        FieldConfig(name="note", type="text"),
        FieldRef(ref="phone"),
    ])
    out = await _filter_step_data(step, {"note": "hi", "phone": "+33...", "returning_user_id": "x"})
    assert out == {"note": "hi", "phone": "+33..."}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_field_definitions.py -k filter_step_data -v`
Expected: FAIL — `_filter_step_data` is sync and `{f.name for f in step_cfg.fields}` raises `AttributeError` on the `FieldRef` (no `.name`), or returns the wrong set.

- [ ] **Step 3: Make `_filter_step_data` async and resolve**

In `not_dot_net/backend/workflow_service.py`, replace `_filter_step_data`:

```python
async def _filter_step_data(step_cfg, data: dict | None) -> dict:
    """Restrict token-submitted data to the current step's declared fields.

    Token holders must not inject arbitrary keys into req.data (e.g.
    returning_user_id, which decides whose tenure record gets created).
    Referenced fields are resolved so their declared (definition-key) names
    are allowed.
    """
    if not data:
        return {}
    from not_dot_net.backend.field_definitions import resolve_step_fields
    allowed = {f.name for f in await resolve_step_fields(step_cfg)}
    return {k: v for k, v in data.items() if k in allowed}
```

Update both call sites to await it (`submit_step` ~line 382, `save_draft` ~line 645):

```python
            data = await _filter_step_data(step_cfg, data)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_field_definitions.py -k filter_step_data -v`
Expected: PASS.
Then guard against regressions in the existing token path:
Run: `uv run pytest tests/test_workflow_config.py tests/test_workflow_detail.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/backend/workflow_service.py tests/test_field_definitions.py
git commit -m "feat(fields): resolve referenced names in token data filter"
```

---

## Task 6: Admin field-library editor

**Files:**
- Create: `not_dot_net/frontend/field_definitions_editor.py`
- Modify: `not_dot_net/frontend/admin_settings.py`
- Modify: `not_dot_net/frontend/i18n.py`
- Test: `tests/test_field_definitions_ui.py`

**Interfaces:**
- Consumes: `field_definitions_config`, `save_field_definition`, `delete_field_definition`, `FieldDefinitionInUse` (Tasks 1/3); `list_vocabularies` (Phase 1); `display_name_to_key`, `check_permission`, `t`/`get_locale`
- Produces: `async render(user) -> None` mounted as Settings → "Field definitions".

- [ ] **Step 1: Write the failing test**

Append to `tests/test_field_definitions_ui.py` (mirror `tests/test_vocabularies_editor.py`):

```python
@pytest.mark.anyio
async def test_admin_can_create_field_definition(user: User) -> None:
    # log in as an admin with manage_settings, open Settings -> Field definitions,
    # create a definition named "Phone number", then assert it persisted:
    # ... drive the UI per the vocabularies_editor test ...
    cfg = await field_definitions_config.get()
    assert any(d.label == "Phone number" or k == "phone_number"
               for k, d in cfg.definitions.items())
```

Copy the login/navigation/permission setup verbatim from `test_vocabularies_editor.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_field_definitions_ui.py -k admin_can_create -v`
Expected: FAIL — editor module/page does not exist.

- [ ] **Step 3: Create the editor module**

Create `not_dot_net/frontend/field_definitions_editor.py`:

```python
"""Bespoke admin editor for reusable field definitions (Settings -> Field definitions)."""

from nicegui import ui

from not_dot_net.backend.permissions import check_permission
from not_dot_net.backend.vocabularies import list_vocabularies
from not_dot_net.backend.field_definitions import (
    FieldDefinition, field_definitions_config,
    save_field_definition, delete_field_definition, FieldDefinitionInUse,
)
from not_dot_net.frontend.i18n import t, get_locale
from not_dot_net.frontend.workflow_editor_options import display_name_to_key

_FIELD_TYPES = ["text", "email", "phone", "textarea", "date", "select",
                "file", "location", "checkbox"]


async def render(user) -> None:
    await check_permission(user, "manage_settings")
    container = ui.column().classes("w-full")

    async def refresh():
        container.clear()
        cfg = await field_definitions_config.get()
        vocab_keys = [None, *[v.key for v in await list_vocabularies()]]
        with container:
            if not cfg.definitions:
                ui.label(t("field_defs_empty")).classes("text-grey text-sm")
            for key in sorted(cfg.definitions):
                defn = cfg.definitions[key]
                with ui.row().classes("w-full items-center gap-2"):
                    ui.label(defn.label or defn.key).classes("font-medium")
                    ui.badge(defn.type).props("color=grey")
                    ui.button(t("edit"),
                              on_click=lambda d=defn: _open_editor(d, vocab_keys, refresh)
                              ).props("flat dense")
                    ui.button(icon="delete",
                              on_click=lambda k=key: _confirm_delete(k, refresh)
                              ).props("flat dense color=negative")
            ui.button(t("field_defs_new"), icon="add",
                      on_click=lambda c=cfg: _prompt_new(c, refresh)).props("flat")

    await refresh()


def _prompt_new(cfg, on_done) -> None:
    dlg = ui.dialog()
    with dlg, ui.card():
        ui.label(t("field_defs_new"))
        name = ui.input(t("field_defs_name")).props("outlined dense")

        async def create():
            if not (name.value or "").strip():
                ui.notify(t("field_defs_name_required"), color="warning")
                return
            key = display_name_to_key(name.value, set(cfg.definitions), fallback_prefix="field")
            await save_field_definition(FieldDefinition(key=key, type="text", label=name.value))
            dlg.close()
            await on_done()

        ui.button(t("save"), on_click=create).props("color=primary")
    dlg.open()


def _confirm_delete(key: str, on_done) -> None:
    dlg = ui.dialog()
    with dlg, ui.card():
        ui.label(t("field_defs_confirm_delete", key=key))

        async def do():
            try:
                await delete_field_definition(key)
            except FieldDefinitionInUse as exc:
                ui.notify(t("field_defs_in_use", usages=", ".join(exc.usages)), color="negative")
                return
            dlg.close()
            await on_done()

        with ui.row():
            ui.button(t("delete"), on_click=do).props("color=negative")
            ui.button(t("cancel"), on_click=dlg.close).props("flat")
    dlg.open()


def _open_editor(defn: FieldDefinition, vocab_keys, on_done) -> None:
    working = defn.model_copy(deep=True)
    dlg = ui.dialog()
    with dlg, ui.card().classes("w-full"):
        ui.label(working.key).classes("text-h6")
        ui.input(t("field_display_name"), value=working.label,
                 on_change=lambda e: setattr(working, "label", e.value)
                 ).props("outlined dense stack-label").classes("w-full")
        type_select = ui.select(_FIELD_TYPES, value=working.type, label=t("field_type"),
                                on_change=lambda e: setattr(working, "type", e.value)
                                ).props("outlined dense stack-label").classes("w-full")
        ui.switch(t("field_required"), value=working.required,
                  on_change=lambda e: setattr(working, "required", e.value))
        ui.switch(t("field_half_width"), value=working.half_width,
                  on_change=lambda e: setattr(working, "half_width", e.value))
        ui.switch(t("field_encrypted"), value=working.encrypted,
                  on_change=lambda e: setattr(working, "encrypted", e.value))
        ui.select(vocab_keys, value=working.options_key, label=t("field_options_key"),
                  on_change=lambda e: setattr(working, "options_key", e.value)
                  ).props("outlined dense stack-label").classes("w-full")

        async def save():
            await save_field_definition(working)
            dlg.close()
            await on_done()

        with ui.row():
            ui.button(t("save"), on_click=save).props("color=primary")
            ui.button(t("cancel"), on_click=dlg.close).props("flat")
    dlg.open()
```

- [ ] **Step 4: Mount it in `admin_settings.py` and skip auto-render**

In `not_dot_net/frontend/admin_settings.py`, add the import near the other editor import:

```python
from not_dot_net.frontend.field_definitions_editor import render as render_field_definitions
```

After the Vocabularies expansion block (lines 63-64), add:

```python
    with ui.expansion(t("field_definitions"), icon="dynamic_form").classes("w-full"):
        await render_field_definitions(user)
```

Extend the auto-render skip (line 69):

```python
        if prefix in ("vocabularies", "field_definitions"):
            continue
```

- [ ] **Step 5: Add i18n keys**

In `not_dot_net/frontend/i18n.py`, add to both `en` and `fr` translation maps (French in parentheses as a guide; use natural French):

```
field_definitions       -> "Field definitions"   (FR: "Définitions de champs")
field_defs_empty        -> "No field definitions yet."  (FR: "Aucune définition de champ.")
field_defs_new          -> "New field definition"  (FR: "Nouvelle définition de champ")
field_defs_name         -> "Field name"  (FR: "Nom du champ")
field_defs_name_required-> "A name is required"  (FR: "Un nom est requis")
field_defs_confirm_delete -> "Delete field definition '{key}'?"  (FR: "Supprimer la définition de champ « {key} » ?")
field_defs_in_use       -> "In use by: {usages}"  (FR: "Utilisée par : {usages}")
field_type              -> "Type"  (FR: "Type")
field_required          -> "Required"  (FR: "Obligatoire")
field_use_shared        -> "Use shared field"  (FR: "Utiliser un champ partagé")
field_shared_badge      -> "shared: {ref}"  (FR: "partagé : {ref}")
field_edit_overrides    -> "Overrides"  (FR: "Surcharges")
field_override_toggle   -> "Override"  (FR: "Surcharger")
```

(`field_display_name`, `field_half_width`, `field_encrypted`, `field_options_key`, `edit`, `delete`, `cancel`, `save` already exist — reuse them.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_field_definitions_ui.py -k admin_can_create -v`
Expected: PASS.

- [ ] **Step 7: Add a delete-blocked UI test, run it**

Append a test that creates a definition, references it from a workflow, drives the delete confirmation, and asserts a negative notification plus that the definition still exists. Run the file:

Run: `uv run pytest tests/test_field_definitions_ui.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add not_dot_net/frontend/field_definitions_editor.py not_dot_net/frontend/admin_settings.py not_dot_net/frontend/i18n.py tests/test_field_definitions_ui.py
git commit -m "feat(fields): admin field-library editor in Settings"
```

---

## Task 7: Workflow editor — ref plumbing, union-safe reads, dangling warning

**Files:**
- Modify: `not_dot_net/frontend/workflow_editor.py`
- Test: `tests/test_field_definitions.py`

**Interfaces:**
- Consumes: `FieldRef`, `resolve_field_ref` (Task 1); `field_definitions_config`, `FieldDefinition` (Task 1)
- Produces (methods on `WorkflowEditorDialog`):
  - `self._field_defs: dict[str, FieldDefinition]` snapshot (set in `create()`)
  - `add_field_ref(wf_key, step_key, ref_key) -> None`
  - `set_field_ref_override(wf_key, step_key, index, attr, value) -> None`
  - union-safe `_is_field_saved`, target-email field-name set, `compute_warnings` (dangling-ref warning)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_field_definitions.py`:

```python
from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
from not_dot_net.backend.field_definitions import FieldDefinition


def _editor_with(working: WorkflowsConfig, defs: dict) -> WorkflowEditorDialog:
    ed = WorkflowEditorDialog(user=None, original=WorkflowsConfig(workflows={}))
    ed.working_copy = working
    ed._vocab_keys = []
    ed._field_defs = defs
    return ed


def test_add_field_ref_appends_fieldref():
    working = WorkflowsConfig(workflows={
        "wf": WorkflowConfig(label="WF", steps=[WorkflowStepConfig(key="s", type="form", fields=[])]),
    })
    ed = _editor_with(working, {"phone": FieldDefinition(key="phone", type="phone")})
    ed.add_field_ref("wf", "s", "phone")
    field = working.workflows["wf"].steps[0].fields[0]
    assert isinstance(field, FieldRef) and field.ref == "phone"


def test_set_field_ref_override_and_clear():
    working = WorkflowsConfig(workflows={
        "wf": WorkflowConfig(label="WF", steps=[
            WorkflowStepConfig(key="s", type="form", fields=[FieldRef(ref="phone")]),
        ]),
    })
    ed = _editor_with(working, {"phone": FieldDefinition(key="phone", type="phone")})
    ed.set_field_ref_override("wf", "s", 0, "required", True)
    assert working.workflows["wf"].steps[0].fields[0].required is True
    ed.set_field_ref_override("wf", "s", 0, "required", None)
    assert working.workflows["wf"].steps[0].fields[0].required is None


def test_compute_warnings_flags_dangling_ref():
    working = WorkflowsConfig(workflows={
        "wf": WorkflowConfig(label="WF", steps=[
            WorkflowStepConfig(key="s", type="form", fields=[FieldRef(ref="gone")]),
        ]),
    })
    ed = _editor_with(working, {})
    warnings = ed.compute_warnings()
    assert any("gone" in w and "field definition" in w for w in warnings)
```

If `WorkflowEditorDialog(user=None, original=...)` signature differs, match `__init__` exactly (it is `def __init__(self, user, original)` per the source).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_field_definitions.py -k "field_ref or dangling" -v`
Expected: FAIL — `add_field_ref` / `set_field_ref_override` not defined; dangling warning absent.

- [ ] **Step 3: Snapshot definitions in `create()`**

In `not_dot_net/frontend/workflow_editor.py`, in the `create()` classmethod after the `_vocab_keys` line (~86):

```python
        from not_dot_net.backend.field_definitions import field_definitions_config
        instance._field_defs = dict((await field_definitions_config.get()).definitions)
```

Also initialize it in `__init__` (near the other attribute defaults, ~72) so direct construction in tests has it:

```python
        self._field_defs: dict = {}
```

Add the import at the top of the file (with the existing config import):

```python
from not_dot_net.config import FieldConfig, FieldRef, NotificationRuleConfig, StepEffectConfig, WorkflowConfig, WorkflowStepConfig, resolve_field_ref
```

- [ ] **Step 4: Add ref mutations**

In the `# --- field-level mutations ---` block of `not_dot_net/frontend/workflow_editor.py`, after `add_field`:

```python
    def add_field_ref(self, wf_key: str, step_key: str, ref_key: str) -> None:
        step = self._find_step(wf_key, step_key)
        step.fields.append(FieldRef(ref=ref_key))
        self._refresh_detail()

    def set_field_ref_override(self, wf_key: str, step_key: str, index: int,
                              attr: str, value) -> None:
        step = self._find_step(wf_key, step_key)
        item = step.fields[index]
        if not isinstance(item, FieldRef):
            raise ValueError("not a field reference")
        setattr(item, attr, value)   # value=None clears the override
```

- [ ] **Step 5: Make read paths union-safe**

In `_is_field_saved` (~327), skip references (they have no editable name):

```python
            for f in step.fields:
                if isinstance(f, FieldConfig) and f.name == field_name:
                    return True
```

The target-email field-name set (~430) and the `field_names` set in `compute_warnings` (~984) must use resolved names. Add a small helper method on the class (place it near `compute_warnings`):

```python
    def _resolved_step_fields(self, step) -> list:
        """Resolve a step's fields against the definitions snapshot.
        Drops dangling references (compute_warnings reports them separately)."""
        out = []
        for item in step.fields:
            if isinstance(item, FieldRef):
                defn = self._field_defs.get(item.ref)
                if defn is not None:
                    out.append(resolve_field_ref(item, defn))
            else:
                out.append(item)
        return out
```

Replace line ~430 `field_names = sorted({f.name for s in wf.steps for f in s.fields if f.name})` with:

```python
            field_names = sorted({f.name for s in wf.steps
                                  for f in self._resolved_step_fields(s) if f.name})
```

- [ ] **Step 6: Update `compute_warnings`**

In `compute_warnings` (`not_dot_net/frontend/workflow_editor.py`), replace the per-step field section so it (a) flags dangling refs and (b) checks options_key/visible_when on resolved fields. Replace the `field_names = {f.name ...}` line (~984) and the inner `for f in step.fields:` blocks (~995-1009) with:

```python
            field_names = {f.name for s in wf.steps for f in self._resolved_step_fields(s)}
            if wf.target_email_field and wf.target_email_field not in field_names:
                warnings.append(
                    f"[{wf_key}] target_email_field '{wf.target_email_field}' does not match any field name"
                )
            for step in wf.steps:
                if "request_corrections" in (step.actions or []):
                    if step.corrections_target and step.corrections_target not in step_keys:
                        warnings.append(
                            f"[{wf_key}/{step.key}] corrections_target '{step.corrections_target}' does not exist"
                        )
                for item in step.fields:
                    if isinstance(item, FieldRef) and item.ref not in self._field_defs:
                        warnings.append(
                            f"[{wf_key}/{step.key}] ref '{item.ref}' is not a known field definition"
                        )
                resolved = self._resolved_step_fields(step)
                for f in resolved:
                    if f.options_key and f.options_key not in org_list_keys:
                        warnings.append(
                            f"[{wf_key}/{step.key}/{f.name}] options_key '{f.options_key}' is not a known vocabulary"
                        )
                checkbox_names = {f.name for f in resolved if f.type == "checkbox"}
                for f in resolved:
                    if not f.visible_when:
                        continue
                    for k in f.visible_when:
                        if k not in checkbox_names:
                            warnings.append(
                                f"[{wf_key}/{step.key}/{f.name}] visible_when references "
                                f"'{k}' which is not a checkbox in the same step"
                            )
```

(The outer `field_names = {...}` that previously sat above the `target_email_field` check is now this resolved version — ensure there is only one.)

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_field_definitions.py -k "field_ref or dangling" -v`
Expected: PASS (3 tests).
Then regression-check the editor:
Run: `uv run pytest tests/test_workflow_editor_options.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add not_dot_net/frontend/workflow_editor.py tests/test_field_definitions.py
git commit -m "feat(fields): workflow-editor ref plumbing, union-safe reads, dangling warning"
```

---

## Task 8: Workflow editor UI — ref row, override dialog, picker

**Files:**
- Modify: `not_dot_net/frontend/workflow_editor.py`
- Test: `tests/test_field_definitions_ui.py`

**Interfaces:**
- Consumes: `add_field_ref`, `set_field_ref_override`, `_field_defs`, `resolve_field_ref` (Task 7)
- Produces: the step editor renders inline fields as today and references as a compact row with an override dialog; a "Use shared field" button adds a reference.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_field_definitions_ui.py` a test that: seeds a definition, opens the workflow editor on a step, clicks "Use shared field", picks the definition, and asserts the step now contains a `FieldRef`. Mirror the editor-driving idioms from any existing `tests/` workflow-editor UI test (search for `open_workflow_editor` usage). Core assertion:

```python
    cfg = await workflows_config.get()
    fields = cfg.workflows["wf"].steps[0].fields  # after Save in the editor
    assert any(isinstance(f, FieldRef) and f.ref == "phone" for f in fields)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_field_definitions_ui.py -k use_shared -v`
Expected: FAIL — no "Use shared field" control exists.

- [ ] **Step 3: Branch the field-render loop**

In `not_dot_net/frontend/workflow_editor.py`, in the Fields panel (the `for idx, field in enumerate(step.fields):` loop ~770), branch on the item type. Wrap the existing inline-rendering body in an `else`, and add the ref branch:

```python
            org_keys = [None, *self._vocab_keys]
            for idx, field in enumerate(step.fields):
                if isinstance(field, FieldRef):
                    self._render_field_ref_row(wf_key, step.key, idx, field, len(step.fields))
                    continue
                with ui.column().classes("w-full"):
                    # ... existing inline rendering, unchanged ...
```

After the existing `ui.button("+ Add field", ...)`, add the picker button:

```python
            def_keys = sorted(self._field_defs)
            if def_keys:
                ui.button(t("field_use_shared"), icon="link",
                          on_click=lambda w=wf_key, sk=step.key, keys=def_keys:
                              self._prompt_use_shared(w, sk, keys)
                          ).props("flat dense color=secondary")
```

- [ ] **Step 4: Add the ref row, picker, and override dialog**

Add these methods to `WorkflowEditorDialog` (near `_render_field_more`):

```python
    def _render_field_ref_row(self, wf_key, step_key, idx, ref, n_fields) -> None:
        defn = self._field_defs.get(ref.ref)
        resolved = resolve_field_ref(ref, defn) if defn else None
        with ui.row().classes("w-full items-center gap-2 no-wrap"):
            ui.badge(t("field_shared_badge", ref=ref.ref)).props("color=secondary")
            if resolved is not None:
                ui.label(resolved.label or resolved.name).classes("grow")
                ui.label(resolved.type).classes("text-grey text-xs")
            else:
                ui.label(t("field_defs_in_use", usages=ref.ref)).classes("text-negative grow")
            ui.button(t("field_edit_overrides"), icon="tune",
                      on_click=lambda w=wf_key, sk=step_key, i=idx, r=ref:
                          self._open_override_dialog(w, sk, i, r)
                      ).props("flat dense")
            ui.button(icon="keyboard_arrow_up",
                      on_click=lambda w=wf_key, sk=step_key, i=idx: self.move_field(w, sk, i, -1)
                      ).props(f"flat dense round size=sm {'disable' if idx == 0 else ''}")
            ui.button(icon="keyboard_arrow_down",
                      on_click=lambda w=wf_key, sk=step_key, i=idx: self.move_field(w, sk, i, +1)
                      ).props(f"flat dense round size=sm {'disable' if idx == n_fields - 1 else ''}")
            ui.button(icon="delete",
                      on_click=lambda w=wf_key, sk=step_key, i=idx: self.delete_field(w, sk, i)
                      ).props("flat dense round color=negative")

    def _prompt_use_shared(self, wf_key, step_key, def_keys) -> None:
        dlg = ui.dialog()
        with dlg, ui.card():
            ui.label(t("field_use_shared"))
            sel = ui.select(def_keys, value=def_keys[0], label=t("field_definitions")
                            ).props("outlined dense stack-label").classes("w-64")

            def add():
                self.add_field_ref(wf_key, step_key, sel.value)
                dlg.close()

            ui.button(t("save"), on_click=add).props("color=primary")
        dlg.open()

    def _open_override_dialog(self, wf_key, step_key, idx, ref) -> None:
        defn = self._field_defs.get(ref.ref)
        dlg = ui.dialog()
        with dlg, ui.card().classes("w-full"):
            ui.label(t("field_shared_badge", ref=ref.ref)).classes("text-h6")

            def row(attr, widget_factory, base_value):
                with ui.row().classes("w-full items-center gap-2"):
                    overridden = getattr(ref, attr) is not None
                    sw = ui.switch(f"{t('field_override_toggle')}: {attr}", value=overridden)
                    w = widget_factory(getattr(ref, attr) if overridden else base_value)
                    w.set_enabled(overridden)

                    def on_toggle(e, _attr=attr, _w=w):
                        _w.set_enabled(e.value)
                        if not e.value:
                            self.set_field_ref_override(wf_key, step_key, idx, _attr, None)
                    sw.on_value_change(on_toggle)
                return w

            base = defn or FieldDefinition(key=ref.ref, type="text")

            label_w = row("label", lambda v: ui.input(t("field_display_name"), value=v or "")
                          .props("outlined dense stack-label"), base.label)
            label_w.on_value_change(lambda e: self.set_field_ref_override(wf_key, step_key, idx, "label", e.value))

            req_w = row("required", lambda v: ui.switch(t("field_required"), value=bool(v)), base.required)
            req_w.on_value_change(lambda e: self.set_field_ref_override(wf_key, step_key, idx, "required", e.value))

            hw_w = row("half_width", lambda v: ui.switch(t("field_half_width"), value=bool(v)), base.half_width)
            hw_w.on_value_change(lambda e: self.set_field_ref_override(wf_key, step_key, idx, "half_width", e.value))

            ui.button(t("save"), on_click=lambda: (dlg.close(), self._refresh_detail())
                      ).props("color=primary")
        dlg.open()
```

Add `from not_dot_net.backend.field_definitions import FieldDefinition` at the top of the file (combine with the import added in Task 7).

Note: keep this override dialog to the high-value properties (`label`, `required`, `half_width`). `type` / `options_key` / `encrypted` / `visible_when` overrides can be added the same way if needed; the data model already supports them (any property is overridable). Do not block the task on rendering all seven — the design allows overriding any property, and the dialog can grow. Document this in the commit message.

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/test_field_definitions_ui.py -k use_shared -v`
Expected: PASS.

- [ ] **Step 6: Run the full field-definitions test set**

Run: `uv run pytest tests/test_field_definitions.py tests/test_field_definitions_ui.py -v`
Expected: PASS (all).

- [ ] **Step 7: Commit**

```bash
git add not_dot_net/frontend/workflow_editor.py tests/test_field_definitions_ui.py
git commit -m "feat(fields): workflow-editor shared-field picker + override dialog"
```

---

## Task 9: Full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the complete suite once**

Run: `uv run pytest -q`
Expected: all tests pass (baseline 919 + the new ~16 field-definition tests). If `/tmp` ENOSPC appears, clear `/tmp/pytest-of-*` and rerun.

- [ ] **Step 2: Manual smoke (optional but recommended)**

Run: `uv run python -m not_dot_net.cli serve --host localhost --port 8088`
Verify: Settings → Field definitions creates a definition; workflow editor → a step → "Use shared field" references it; the new-request form renders the shared field with the definition's label; deleting the in-use definition is refused.

- [ ] **Step 3: Final commit (if smoke fixes were needed)**

```bash
git add -A
git commit -m "test(fields): full-suite green for reusable field definitions"
```

---

## Self-Review

**Spec coverage** (against `2026-06-25-field-definitions-phase2-design.md`):
- §1 Data model → Task 1. §2 Resolution → Tasks 1 (pure) + 2 (async). §3 Consumer audit → Tasks 4 (render/validate/display + workflow_detail), 5 (token filter), 7 (editor reads). §4 Deletion safety → Task 3 (block) + Task 2 (dangling drop). §5 Editor UI → Tasks 6 (library) + 7/8 (workflow editor). §6 Back-compat → no migration/seed anywhere; union deserializes inline as today (Task 1). §7 Out of scope → built-ins/i18n-labels/non-workflow surfaces/export not built. §8 Testing → covered per task + Task 9.
- Engine purity (Global Constraint) → resolution only at service/frontend; `workflow_engine.py` untouched. Note: `get_completion_status` in the engine iterates `step.fields` but has no live caller (dead import in `workflow_step.py`); left untouched and not routed refs. If a future caller appears, it must pass resolved fields.

**Placeholder scan:** every code step contains complete code. UI tests say "mirror the existing vocab/workflow-editor test idioms" rather than inventing navigation that may not match the real pages — this is deliberate (copy the proven setup), and each names the concrete assertion that must hold.

**Type consistency:** `FieldDefinition` (backend), `FieldRef`/`resolve_field_ref`/`StepField` (config), `resolve_step_fields`/`save_field_definition`/`delete_field_definition`/`definition_usages`/`FieldDefinitionInUse` (backend), `add_field_ref`/`set_field_ref_override`/`_resolved_step_fields`/`_field_defs` (editor) — names used identically across tasks. `_filter_step_data` is async after Task 5; both call sites updated.
