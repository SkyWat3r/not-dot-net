# Workflow conditional fields (checkbox + visible_when)

## Goal

Let admins build conditional sections in the workflow editor: a checkbox
toggles the visibility of a group of related fields. The first user is the
ZRR (zone à régime restrictif) section in onboarding — but ZRR itself stays
out of the code; the workflow editor expresses it as data.

## Non-goals

- Cross-step conditionals. The checkbox and its dependent fields must live
  in the same step.
- Predicate language richer than equality on a single sibling field.
- Migration of existing fields. The new properties are opt-in; everything
  ships with `visible_when=None` and behaves unchanged.

## Schema

`config.FieldConfig` gains:

- `type` — extended to accept `"checkbox"`. Stored as a Python `bool`,
  serialized in `WorkflowRequest.data` as JSON `true`/`false`.
- `visible_when: dict[str, Any] | None` — when set, the field is rendered
  iff `data.get(key) == value` for the single (key, value) pair. When the
  field is hidden, `required` is skipped and any saved value is preserved
  (no destructive clears on toggle, to avoid surprising data loss if the
  admin toggles back).

Both additions default to backward-compatible values (`type` keeps its
existing string union; `visible_when=None`), so no migration is needed.

## Engine changes

`workflow_step.render_step_form`:

- New branch in `_render_field` for `type == "checkbox"` — a Quasar
  `ui.checkbox(label=...)`, value bound to `data[name]` as bool.
- `_render_field` reads `field_cfg.visible_when` and wraps the rendered
  element in a container whose visibility is bound to the predicate.
  After the loop renders all fields, each checkbox referenced by some
  `visible_when` rule gets an `on_change` handler that re-evaluates the
  predicate for every dependent and toggles `.set_visibility()` on its
  container. Multiple checkboxes with independent dependents are
  supported by iterating per-checkbox.
- `validated_submit` skips required-field checks for hidden fields:
  the same predicate function used for visibility decides whether a
  field counts toward `missing`.

`workflow_service` and `workflow_engine` need no changes — the engine
already treats `data` as an opaque dict; hidden fields with no value
just stay absent.

## Workflow editor changes

`workflow_editor.py`:

- The field-type select gains `"checkbox"` (with an i18n label).
- The per-row "More…" expander gets a `visible_when` editor: two
  selects — sibling field (filtered to checkbox-type fields in the same
  step) and value (`true`/`false`). Empty = no rule.
- Cross-field warnings: emit one when a `visible_when` references a
  field that doesn't exist or isn't a checkbox in the current step.

## Tests

- `test_workflow_step.py` (or new `test_conditional_fields.py`):
  rendered form hides dependent fields when checkbox is False; shows
  them when True; required-validation skips hidden fields.
- `test_workflow_engine.py`: submitting with a hidden required field
  empty does not raise; submitting with the field shown and empty does.
- `test_workflow_editor.py`: editor saves and loads a checkbox + a
  field with `visible_when`; warning fires for a dangling reference.
- `test_workflow_config.py`: round-trip serialization of a config that
  uses the new properties.

## i18n

Two new keys: `field_type_checkbox`, `wf_visible_when_help`. EN + FR.

## Out of scope (follow-ups)

- ZRR-specific field set (will be added by the admin via the editor
  once the feature lands).
- Cross-step conditionals.
- Multi-condition predicates (`AND`/`OR`), value lists, regex matchers.
