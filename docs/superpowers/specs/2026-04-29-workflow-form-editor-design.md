# Workflow Form Editor — Design

**Date:** 2026-04-29
**Status:** Draft

## Problem

Editing the `workflows` config section means hand-editing YAML. The schema is
deeply nested (`WorkflowsConfig → dict[str, WorkflowConfig] → list[WorkflowStepConfig]
→ list[FieldConfig]` plus `notifications` and `document_instructions`), and the
YAML editor in `frontend/admin_settings.py:_render_yaml_editor` offers no
guidance, no validation feedback beyond raw Pydantic errors, and no
discoverability of valid values (role keys, permission keys, action names,
field types). Two adjacent sections — `OrgConfig.teams`/`sites`/etc. and
`BookingsConfig.software_tags` — also suffer from awkward editors
(comma-separated text inputs, YAML for the dict).

## Goals

- Replace the YAML editor for `workflows` with a form-based master-detail editor.
- Replace comma-separated text inputs for `list[str]` fields with a chip-style
  input across all sections.
- Replace the YAML editor for `dict[str, list[str]]` fields with a keyed chip
  editor (currently only affects `BookingsConfig.software_tags`).
- Keep YAML available as a secondary tab inside the workflow editor for power
  use (paste-import, backup, fixing edge cases).
- No regression in audit logging, validation, or persistence.

## Non-goals

- Visual workflow graph / DAG editor (engine is linear with `request_corrections`
  loop-back; a tree is sufficient).
- Workflow versioning or migrating in-flight requests on schema change.
- Live preview of the rendered request form.
- Permission matrix UI (already covered by the roles admin).
- Cross-admin edit conflict resolution (last-save-wins, same as today).

## Design

### File layout

**New modules:**

- `not_dot_net/frontend/widgets.py` — reusable input helpers used across the
  settings UI:
  - `chip_list_editor(value: list[str], *, label: str = "", suggestions: list[str] | None = None) → widget`
    — Quasar `q-select` with `multiple use-chips use-input new-value-mode="add-unique"`.
    Returns a widget whose `.value` is a `list[str]`.
  - `keyed_chip_editor(value: dict[str, list[str]], *, key_label: str = "Key") → widget`
    — vertical stack of rows `[key input | chip_list_editor | trash]` plus an
    "Add" button.

- `not_dot_net/frontend/workflow_editor.py` — full-screen dialog + master-detail
  render functions for the workflows config section.

**Modified:**

- `not_dot_net/frontend/admin_settings.py`:
  - In `_render_form`, add two dispatch branches before the catch-all:
    `list[str]` → `chip_list_editor`, `dict[str, list[str]]` → `keyed_chip_editor`.
    Drop the "Comma-separated values" hint.
  - The `workflows` prefix gets a button **"Edit workflows…"** (with a small
    summary line: "N workflows, M steps") that opens
    `workflow_editor.open_dialog(user)`.
  - Re-evaluate `_is_complex`: a section is complex only if it has a nested
    `BaseModel`, not merely a dict. With the chip editors in place, no current
    section other than `workflows` is "complex" — `BookingsConfig` becomes a
    normal form.

### Workflow editor dialog

Triggered from Settings → Workflows. Full-screen `ui.dialog().props("maximized")`
with two top-level tabs: **Form** and **YAML**.

#### Form tab (master-detail)

```
┌──────────────────────────────────────────────────────────┐
│  Workflows editor                          [Form][YAML]  │
├──────────────────┬───────────────────────────────────────┤
│  Workflows       │                                       │
│  ▾ vpn_access  ⋮ │   <editor for selected node>          │
│    • request   ⋮ │                                       │
│    • approval  ⋮ │                                       │
│  ▾ onboarding  ⋮ │                                       │
│    • initiation⋮ │                                       │
│    • newcomer  ⋮ │                                       │
│  + Add workflow  │                                       │
├──────────────────┴───────────────────────────────────────┤
│  ⚠ 3 issues       [Cancel] [Reset defaults] [Save]       │
└──────────────────────────────────────────────────────────┘
```

**Tree (left, ~280 px):** built by hand from the in-memory `WorkflowsConfig`.
Each workflow header shows trash + duplicate icons; each step row has a drag
handle for reordering within its workflow. Selected node highlighted.

**Add workflow / add step:** "Add workflow" prompts for a unique workflow key
(slug-validated: lowercase letters, digits, underscore) and creates an empty
`WorkflowConfig(label=key, steps=[])` in the working copy. "Add step" inside
a workflow prompts for a unique step `key` and appends a
`WorkflowStepConfig(key=key, type="form")`. Duplicate icon on a workflow
opens the same key prompt, then deep-copies the source workflow's steps and
notifications.

**Detection of `OrgConfig` list fields** (used by the field editor's
`options_key` dropdown): introspect `OrgConfig.model_fields` at render time
and include every field whose annotation is `list[str]`.

**Right pane:**

- **Workflow node selected:** form with
  - `label` (text input)
  - `start_role` (select — populated from `RolesConfig`)
  - `target_email_field` (text input — must match a field name in any step;
    cross-field warning shown otherwise)
  - `document_instructions` (`keyed_chip_editor`, key = employment status)
  - **Notification rules** sub-section: table with columns `event` (chip-style
    text input with suggestions taken from the union of action names appearing
    across all steps in the working copy, e.g. submit / approve / reject /
    request_corrections — events are not enumerated by the schema, so we
    suggest from observed actions rather than hardcoding a list),
    `step` (dropdown auto-populated from the workflow's step keys, plus blank
    = "any"), `notify` (chip editor; suggestions = role keys + `requester` +
    `target_person`). Add-row at bottom; rows deletable.

- **Step node selected:** form with
  - `key` (text input — collisions show warning)
  - `type` (select: form / approval)
  - **Assigned to** (radio group collapsing the three fields
    `assignee_role` / `assignee_permission` / `assignee` into one of
    {Role, Permission, Contextual} with the matching dropdown below;
    Contextual options: `target_person`, `requester`)
  - `actions` (chip editor; suggestions: submit, approve, reject,
    request_corrections, cancel)
  - `partial_save` (switch)
  - `corrections_target` (dropdown of step keys; only visible when
    `request_corrections` ∈ actions)
  - **Fields** table — rows: `name`, `type` (dropdown:
    text/email/textarea/date/select/file), `required` (switch), `label` (text),
    `options_key` (dropdown of `OrgConfig` `list[str]` field names), `encrypted`
    (switch), `half_width` (switch), trash. Add-row at bottom; rows draggable
    to reorder.

#### YAML tab

The same `ui.codemirror(language="yaml")` we have today, bound to the same
in-memory model. Switching tabs serializes/parses on transition; parse errors
block the switch with a notify and the user stays on YAML.

### Data flow

- On dialog open: clone `await workflows_config.get()` → working copy
  `WorkflowsConfig` held in dialog-local state.
- Form widgets bind to the working copy via explicit `on_value_change`
  callbacks that mutate the model and refresh affected pieces of the tree
  (e.g. renaming a step key updates the tree label and the
  `corrections_target` dropdowns).
- **Save:** `WorkflowsConfig.model_validate(working_copy.model_dump())` → on
  success, `await workflows_config.set(...)`, audit log
  (`settings/update section=workflows`), close dialog. On `ValidationError`,
  show the first error inline near the relevant section and a banner at the top.
- **Cancel:** if working copy != original, confirm before closing.
- **Reset defaults:** confirm → `await workflows_config.reset()` → reload
  working copy.

### Validation

Two layers:

1. **Pydantic validation on Save** — single `model_validate` call gives all
   schema errors (types, missing required, enum membership). First error →
   notify + scroll to the offending section if identifiable from the error path.

2. **Light cross-field hints in the UI** (advisory, not blocking, shown in a
   "⚠ N issues" pill at the dialog footer that opens a panel listing them with
   click-to-jump):
   - Step `key` collisions within a workflow.
   - Notification rule references a `step` that no longer exists.
   - `corrections_target` references a non-existent step key.
   - `target_email_field` doesn't match any field name in the workflow.
   - Field `options_key` references a list field that doesn't exist on
     `OrgConfig`.

   Advisory because the schema doesn't enforce them today; silent auto-fix
   on rename would surprise the user.

### Edge cases

- **Renaming a step key** does not auto-rewrite references; the user gets the
  warning above and resolves manually. Silent rewrites are a refactoring tool,
  not a config editor.
- **Deleting a workflow with in-flight requests** is out of scope here — the
  editor only edits config; the workflow engine should already tolerate
  unknown workflow types in stored requests (verify during impl, add a test
  if it doesn't).
- **YAML tab with unsaved form changes:** serialize-on-switch, no separate
  dirty tracking per tab.
- **Concurrent edits by two admins:** no locking. Last-save-wins, same as
  today's YAML editor.

## Tests

NiceGUI `User`-based tests, in line with the existing test suite.

`tests/test_widgets.py`:

- `chip_list_editor` round-trips list values.
- `chip_list_editor` adding/removing chips updates `.value`.
- `keyed_chip_editor` adds/removes keys.
- `keyed_chip_editor` nested chip changes propagate.

`tests/test_workflow_editor.py`:

- Open dialog → modify a step `key` → save → `workflows_config.get()` reflects.
- Add a workflow → add a step → fill required fields → save → persisted.
- Switch Form → YAML → edit → switch back → changes visible.
- Save with invalid Pydantic data → notify shown, config unchanged.
- Cancel with dirty state → confirm dialog appears; dismiss keeps working copy.
- Audit log gets `settings/update section=workflows` entry on save.

`tests/test_admin_settings.py` adjustments:

- Bookings section now renders as a form (not YAML).
- Org list[str] fields render as chip editors.

## Migration & rollout

Pure UI change. No DB schema changes, no Alembic migration. Existing config
data continues to load; the new editor reads/writes the same Pydantic model.
The YAML tab inside the dialog is the safety hatch if the form has a bug.

## Out-of-scope follow-ups

- Live preview of the rendered request form using the existing step renderer.
- Workflow templates / clone-from-existing in the "Add workflow" flow.
- Schema-driven generic form builder for future complex sections (revisit
  only if a third complex section appears).
