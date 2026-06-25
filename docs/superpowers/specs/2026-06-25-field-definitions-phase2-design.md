# Reusable field definitions — Vocabulary registry, Phase 2

**Status:** Design approved (brainstorming). Ready for implementation plan.
**Scope:** Phase 2 of the vocabulary-registry effort. Builds on Phase 1
(`2026-06-25-vocabulary-registry-design.md`). Workflow-scoped: the only
config-driven forms in the app are workflow steps.

## In plain terms

Today an admin re-types every workflow field by hand — name, type, required,
which dropdown it uses. Ten workflows that each ask for a phone number means ten
separately hand-typed phone fields.

Phase 2 adds a **shared field library**. An admin defines a field once (e.g.
"Phone number") and any workflow step can reference it instead of retyping it.
**Edit the definition once and every workflow that uses it updates** — the
definition is the single source of truth, resolved live at render time.

A step that references a shared field may still **override any property for that
one use** (e.g. required here, optional there) without affecting other workflows.
Unset = track the definition live; set = pinned local override.

The feature is **purely opt-in and additive**: existing workflows keep their
inline fields and behave exactly as before.

## Decisions (locked during brainstorming)

| Decision | Choice |
| --- | --- |
| Reference semantics | **Edit once, applies everywhere** — a step references a definition by key; resolved live. Not a copy/snapshot. |
| Override scope | **Override anything** — a reference may pin any property; unset properties inherit live from the definition. |
| Field identity (`name`) | **Not overridable.** A reference's resolved `name` is always the definition's key, so a shared field stores under one consistent data key everywhere. |
| Label internationalization | **Single string**, matching today's `FieldConfig.label`. Field labels are not i18n'd today; bilingual labels are out of scope. (Vocabularies stay bilingual; field *labels* do not become so.) |
| Representation | **Approach A** — a separate `FieldRef` type; a step's field list becomes a union of inline `FieldConfig` and `FieldRef`. One resolver merges. |
| Engine purity | `workflow_engine.py` stays pure (no DB). Resolution runs at the service/frontend boundary; the engine receives already-resolved fields/names. |
| Deleting an in-use definition | **Block it.** Refuse deletion and report which workflows still reference it. |
| Seeding / built-ins | **None.** The registry starts empty; the admin populates it. No migration of existing inline fields. |
| Storage | ConfigSection JSON blob, mirroring the Phase-1 vocabulary registry. No new table, no Alembic migration. |
| Permission to manage | Reuse existing `manage_settings`. |
| Definition key | Immutable after creation (label/properties editable). |

## 1. Data model

New module `backend/field_definitions.py`, mirroring `backend/vocabularies.py`:

```python
class FieldDefinition(BaseModel):
    key: str                       # immutable registry key; ALSO the resolved field's data name
    type: str                      # text | email | textarea | date | select | file | phone | location | checkbox
    label: str = ""
    required: bool = False
    options_key: str | None = None # vocabulary binding (select), resolved via the Phase-1 registry
    encrypted: bool = False
    half_width: bool = False
    # No visible_when — it references sibling fields in a specific step, so it cannot be shared.

class FieldDefinitionsConfig(BaseModel):
    definitions: dict[str, FieldDefinition] = Field(default_factory=dict)

field_definitions_config = section("field_definitions", FieldDefinitionsConfig,
                                   label="Field definitions")
```

New `FieldRef` in `config.py` (next to `FieldConfig`, the schema home):

```python
class FieldRef(BaseModel):
    ref: str                              # FieldDefinition.key
    # every override is Optional; None = inherit live from the definition
    type: str | None = None
    label: str | None = None
    required: bool | None = None
    options_key: str | None = None
    encrypted: bool | None = None
    half_width: bool | None = None
    visible_when: dict[str, Any] | None = None   # step-local only (never in the definition)
```

`WorkflowStepConfig.fields` widens from `list[FieldConfig]` to
`list[FieldConfig | FieldRef]`.

**Deserialization.** The union uses `union_mode="left_to_right"`. A stored dict
with `name` + `type` loads as `FieldConfig`; one carrying `ref` (and no `name`)
falls through to `FieldRef`. The two shapes are disjoint on required keys, so
this is unambiguous, and every existing stored workflow (all inline) keeps
loading as `FieldConfig` — zero back-compat risk.

## 2. Resolution

Pure merge in `config.py`, next to `is_field_visible`:

```python
def resolve_field_ref(ref: FieldRef, defn: FieldDefinition) -> FieldConfig:
    pick = lambda override, base: override if override is not None else base
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
```

The `pick` helper gives tri-state behavior (inherit / set-true / set-false), so
overriding a bool to `False` is distinct from "not overridden".

An async resolver in `backend/field_definitions.py` resolves a whole step,
preserving field order and passing inline fields through unchanged:

```python
async def resolve_step_fields(
    step: WorkflowStepConfig, *, cfg: FieldDefinitionsConfig | None = None
) -> list[FieldConfig]:
    ...  # inline -> passthrough; ref -> resolve_field_ref against the definition
```

A reference whose definition is missing is **dropped** from the resolved list
(belt-and-suspenders against a hand-edited/imported bad config — see §4; normal
deletion is blocked, so this should not arise in practice). The editor surfaces
such a dangling reference loudly via `compute_warnings`.

**`resolve_step_fields` is the single seam** every consumer adopts. Because it
loads the definitions config, resolution happens at the service/frontend
boundary; the pure engine receives resolved fields/names.

## 3. Consumer audit

Widening `fields` to a union breaks every site that assumes `f.name` /
`f.options_key` / `f.type`. Each must iterate **resolved** fields (or branch on
the union for editor-only raw access). Required changes:

| Site | Change |
| --- | --- |
| `workflow_step.render_step_form` / `_render_field` | render from `resolve_step_fields(step)` |
| `workflow_step.validated_submit` (required check) + partial-save completion indicator | resolved fields |
| `workflow_step.resolve_display_values` (approval-view labels) | resolved fields |
| `workflow_engine._filter_step_data` (token data allow-list) | **security-relevant** — resolve to declared names *before* filtering, so a token holder cannot inject keys |
| `workflow_editor.compute_warnings` | resolve refs to check `options_key`; also flag dangling `ref` |
| `workflow_editor` name-lock / autoslug logic | branch: a reference's name is the definition key and is not editable |
| dashboard / `step_display` field enumeration (if any iterates fields) | resolved |

This audit is mandatory per project practice: relaxing a config invariant
requires checking every consumer that indexes/iterates the shape.

## 4. Deletion safety

`delete_field_definition(key)` scans `WorkflowsConfig` for any step field that is
a `FieldRef` with `ref == key`. If any exist, it **raises** (the editor catches
and notifies, listing the referencing workflow/step keys) — the deletion is
refused. This is the primary protection against broken live forms.

As a secondary guard, `resolve_step_fields` tolerates a dangling reference
(drops it, editor warns) so a manually edited or imported config can never crash
a form render. Both guards are cheap; keep both.

## 5. Editor UI

**A. Field-library admin — new `frontend/field_definitions_editor.py`**, modelled
on `frontend/vocabularies_editor.py`:
- `render(user)` guards on `manage_settings`.
- Mounted in `admin_settings.py` as a **Settings → "Field definitions"**
  expansion; the registry loop adds `field_definitions` to its skip set
  (alongside the existing `vocabularies` skip) so it is not auto-rendered.
- Create is name-first (`display_name_to_key(name, taken, fallback_prefix="field")`);
  key immutable.
- Edit a definition: type select (the field-type list the step editor already
  uses), `label`, `required`, `encrypted`, `half_width`, and — when
  `type == "select"` — an `options_key` picker that lists vocabulary keys from
  `list_vocabularies()` (the same widget used in the step editor today).
- `save_field_definition` / `delete_field_definition` mirror the vocab editor's
  `save`/`delete` (ValueError → `ui.notify(..., color="negative")`).

**B. Step editor (`workflow_editor.py`).** Adding a field to a step gains a
choice:
- **"New field"** → inline `FieldConfig` (today's flow, unchanged).
- **"Use shared field"** → pick a definition key → appends `FieldRef(ref=key)`.

A `FieldRef` renders distinctly in the field list: a **"shared: «key»"** badge,
the definition's inherited values shown as placeholders, and a per-property
**override toggle** (off = track the definition / `None`; on = pin a local
value). `visible_when` is always editable (step-local). The referenced
definition is shown and re-pointable. A new mutation helper
`set_field_ref_override(wf, step, index, attr, value_or_None)` sets/clears one
override; inline fields keep using the existing `set_field_attr`.

`compute_warnings` snapshots known definition keys (`self._field_def_keys`,
captured in `create()` like `_vocab_keys`) and emits
`[wf/step/field] ref '<key>' is not a known field definition` for dangling refs,
plus the existing `options_key` check against the resolved field.

## 6. Back-compat / migration

- No Alembic migration (ConfigSection JSON, like the vocab registry).
- Existing stored workflows contain only inline fields → keep loading and
  behaving exactly as today.
- The feature is additive: references are opt-in. Nothing breaks on deploy.
- The field-definitions registry starts empty; no seeding from existing inline
  fields (rejected as speculative).

## 7. Out of scope

- **Built-in field definitions** (code-provided common fields). Could be a later
  follow-up; v1 is admin-populated only.
- **Internationalized field labels** — labels stay single strings.
- **Reusable fields outside workflow steps** — directory/tenure/import forms are
  not config-driven; nothing to plug into.
- **Field-definition export/import via `data_io.py`** — optional follow-up.
- **Cross-step `visible_when`** — unchanged from Phase 1 (same-step only).

## 8. Testing

Pure / unit:
- `resolve_field_ref` merge — inherit vs override per property; tri-state bool
  (override-to-`False` distinct from inherit); `name` always from the
  definition; `visible_when` taken only from the reference.
- `resolve_step_fields` — mixed inline + reference list preserves order; a
  dangling reference is dropped.

Integration (`nicegui.testing.User`):
- Create a definition in the admin page → reference it from a workflow step →
  render the step form and see the resolved field (label/options from the
  definition).
- Override `required` on the reference → the form enforces it for that workflow
  only.
- Edit the definition's label → an existing workflow's rendered field reflects
  the change ("edit once, applies everywhere").
- **Deletion blocked:** deleting an in-use definition raises / notifies and
  leaves it in place.
- **Security:** a token submission to a step containing a reference accepts the
  resolved (definition-key) name and rejects injected keys.
- Editor: `compute_warnings` flags a dangling reference.

Test-isolation note (from Phase 1): the `user`-fixture conftest no-ops registry
seeding; integration tests that need definitions set
`field_definitions_config.set(...)` themselves.

## 9. Affected files (anticipated)

- **New:** `backend/field_definitions.py`, `frontend/field_definitions_editor.py`,
  tests.
- **Modified:** `config.py` (`FieldRef`, widened `WorkflowStepConfig.fields`,
  `resolve_field_ref`), `frontend/workflow_step.py` (render/validate/display via
  `resolve_step_fields`), `backend/workflow_service.py` +
  `backend/workflow_engine.py` (resolve at the boundary; resolved names into
  `_filter_step_data`), `frontend/workflow_editor.py` (reference picker, override
  UI, mutations, warnings, name-lock branching), `frontend/admin_settings.py`
  (mount + skip), i18n strings.
