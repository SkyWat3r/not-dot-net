# Vocabulary Registry — Design (Phase 1)

**Date:** 2026-06-25
**Status:** Approved design, ready for implementation planning
**Scope:** Phase 1 of a two-phase effort. Phase 2 (reusable field definitions) is
documented here only as a follow-up, not specified.

## Motivation

Today the intranet has a fixed set of shared option lists hardcoded in
`OrgConfig` (`teams`, `sites`, `employment_statuses`, `employers`,
`transport_modes`, `funding_sources`). Admins can edit the *contents* of these
lists in Settings, but **cannot create a new named list at runtime** — adding one
requires a code change (a new `OrgConfig` field plus a branch in
`workflow_step._resolve_options`). There is also no maintained list of
nationalities to feed a combo box.

The goal (Redmine-style) is to make the **set of shared vocabularies itself
runtime-definable**: an admin can create/edit/delete named option lists from the
app, and any workflow (or other config-driven surface) can reference them. A
single funding-source vocabulary is then genuinely shared across workflows, and
nationalities is just a built-in vocabulary.

### Decomposition

- **Phase 1 (this spec):** an app-wide **Vocabulary registry**. Generalizes the
  hardcoded `OrgConfig` lists into a DB-backed registry of named, runtime-creatable
  vocabularies; wires existing consumers to it; ships nationalities as a built-in
  vocabulary.
- **Phase 2 (future, separate spec):** **reusable field definitions** — define a
  whole field (type, label, required, encrypted, which vocabulary it binds to)
  once and reference it across workflows. This is workflow-scoped (the only
  config-driven forms are workflow steps) and builds on Phase 1.

Vocabularies are the right shared primitive for the *whole* app: they are already
consumed outside workflows (the directory status select, tenure forms, personnel
import). Reusable field definitions are form-input concepts with nothing to plug
into outside workflows today, so they are deferred.

## Decisions (locked during brainstorming)

| Decision | Choice |
| --- | --- |
| Core unit for Phase 1 | App-wide vocabulary registry |
| Term shape | `code` + locale-keyed `labels` (second locale optional; built-ins seeded bilingual) |
| Existing `OrgConfig` lists | Folded into the registry (single source of truth) |
| Nationalities value form | Demonym / adjectival (e.g. "French" / "Français"), masculine-singular in FR |
| Nationalities source | Curated, version-controlled bundled JSON (no runtime dependency) |
| Storage backing | ConfigSection JSON blob for stored vocabularies + code-provided built-ins |
| Permission to manage | Reuse existing `manage_settings` |
| Vocabulary key | Immutable after creation (label is editable) |

## 1. Data model

New module `backend/vocabularies.py`.

```python
class VocabularyTerm(BaseModel):
    code: str                 # stable stored value: "FR", "PostDoc", "CNES"
    labels: dict[str, str]    # locale -> label: {"en": "French", "fr": "Français"}
    active: bool = True       # inactive = hidden from new picks, kept for old data

class StoredVocabulary(BaseModel):
    key: str                  # immutable registry key: "funding_sources"
    label: dict[str, str]     # the vocabulary's own display name, locale-keyed
    allow_custom: bool = False  # combo-box free entry (default closed)
    terms: list[VocabularyTerm] = []

class VocabulariesConfig(BaseModel):
    vocabularies: dict[str, StoredVocabulary] = {}

vocabularies_config = section("vocabularies", VocabulariesConfig, label="Vocabularies")
```

**Built-in vocabularies** are computed by code, not stored in the blob, and merged
into the resolvable set:

```python
@dataclass
class BuiltinVocabulary:
    key: str
    label: dict[str, str]
    load_terms: Callable[[], Awaitable[list[VocabularyTerm]]]
    editable: bool = False

BUILTIN_VOCABULARIES = {
    "nationalities": ...,  # reads bundled JSON (§4)
    "roles": ...,          # reads roles_config keys (preserves today's "roles" options_key)
}
```

Rationale for the storage split (chosen Approach A over a relational
`Vocabulary`/`VocabularyTerm` table pair): reuses the existing ConfigSection
idiom (`get`/`set`/`reset`, single-row transactional writes), keeps the blob
small (built-ins like ~250 nationalities are *not* stored), and matches the
lab-intranet scale where admins edit small lists occasionally. A relational
schema would add two models, an Alembic migration, a service layer, and DTOs for
no benefit at this scale.

## 2. Resolution & consumer rewiring

A single resolution API in `backend/vocabularies.py` replaces the hardcoded
`workflow_step._resolve_options` `if/elif`:

```python
async def resolve_terms(key, *, active_only=True) -> list[VocabularyTerm]
def term_label(term, locale) -> str          # labels.get(locale) or any-label or code
async def select_options(key, locale) -> dict[str, str]   # {code: label} for ui.select
async def list_vocabularies() -> list[VocabularyView]     # stored + builtin (admin + editor)
```

Resolution order: **stored registry → built-in providers → `[]`** (an unknown key
stays graceful, matching today's empty-list behavior).

Consumers rewired to call this module:

- `frontend/workflow_step.py::_resolve_options` → `select_options(...)`. Selects
  now pass a `{code: label}` dict, so the stored value is the **code** and the
  display is the **label**. For all 6 migrated lists `code == label`, so behavior
  is identical; only nationalities (and future code≠label vocabularies) differ.
- `frontend/directory.py` — the two tenure status selects → registry
  `employment_statuses`.
- `backend/personnel_import.py` — status validation reads registry
  `employment_statuses` (instead of `OrgConfig.employment_statuses`).
- `frontend/workflow_editor.py` — the `options_key` picker and `compute_warnings`
  validate against **registry keys** (`list_vocabularies()`), replacing the
  `OrgConfig` model introspection in `_org_list_field_names()`.

### Stored code vs displayed label (cross-cutting concern)

Because a stored value can now be a `code` that differs from its label, any place
that **shows a submitted field value back** to a human must resolve code→label.
In Phase 1 the only such path that matters is the **workflow request/step detail
renderer**; a `display_value(field, raw)` helper is added there. All 6 migrated
lists are unaffected (`code == label`); nationalities is the one vocabulary that
needs the resolution. Other surfaces adopt the helper if/when they bind a
code≠label vocabulary. This is called out explicitly because relaxing the
"stored value == display string" invariant is exactly the kind of change that
needs every consumer audited.

## 3. Admin UI & permission

A bespoke editor (the auto-generated settings form cannot render nested terms —
the same reason workflows and bookings have bespoke editors), reached from
**Settings → "Vocabularies"** expansion, gated on **`manage_settings`** (no new
permission).

- **Left:** list of vocabularies. Stored ones are editable; built-ins
  (`nationalities`, `roles`) show a "system" badge with read-only terms.
- **Right (selected vocabulary):** the vocabulary label (per locale), the
  `allow_custom` toggle, and a terms table — columns `code | label (en) |
  label (fr) | active` — with add / remove / reorder.
- **Create vocabulary:** name-first → slug, reusing `display_name_to_key`
  (`workflow_editor_options.py`). The **key is immutable after creation** (renaming
  it would orphan referencing workflow fields); only labels are editable.
- **Deleting/renaming a term** keeps stored data intact (codes are stable).
  **Deleting a vocabulary** still referenced by a field surfaces a
  `compute_warnings` dangling-reference warning rather than silently breaking the
  workflow.

## 4. Nationalities

A built-in vocabulary backed by a curated, version-controlled JSON file
(`backend/data/nationalities.json`):

```json
[ {"code": "FR", "en": "French", "fr": "Français"},
  {"code": "DE", "en": "German", "fr": "Allemand"} ]
```

- ~250 entries, keyed by ISO 3166-1 alpha-2 codes.
- The `nationalities` built-in provider loads it into `VocabularyTerm`s
  (`labels = {"en", "fr"}`, `active=True`).
- Demonyms rendered as the masculine-singular adjective (conventional dropdown
  form; can switch to a "Français(e)" style later by regenerating the file).
- "Up to date" = refresh the JSON file. Countries change rarely, so a bundled
  snapshot is more stable than a runtime dependency on a library.
- A workflow field references it with `type="select", options_key="nationalities"`;
  the vocabulary is a closed list (`allow_custom=False`).
- The generation script that produces the JSON from a vetted source is an
  implementation detail for the plan, **not** a runtime dependency of the app.

## 5. Migration / seeding & backward compatibility

- **No Alembic migration** — the registry lives in the `app_setting` JSON row, not
  a new table.
- **Idempotent startup seed** `ensure_vocabularies_seeded()` (same pattern as
  `ensure_default_admin`): if the `vocabularies` section is unseeded, it reads the
  **raw `org` section JSON** (capturing admin-*customized* values, with model
  defaults as fallback) and persists once. Runs in dev and prod startup. Each
  migrated value becomes a `VocabularyTerm` with `code == label`, `active=True`,
  and `labels = {default_locale: value}` (the second locale falls back to it). Each
  vocabulary's own `label` defaults to the prettified key (e.g. `funding_sources`
  → `{"en": "Funding sources"}`), which the admin can refine afterward.
- The 6 list fields are then **removed from `OrgConfig`**, which keeps only
  `app_name` and `base_url`. Their old keys linger harmlessly in the org JSON
  (Pydantic ignores extras by default).
- The **`options_key` attribute name is unchanged** and all existing keys are
  preserved (`teams`, `sites`, `employment_statuses`, `employers`,
  `transport_modes`, `funding_sources`, `roles`), so `default_workflows.py` and
  existing stored workflow configs need no changes.

## 6. Error handling & edge cases

- **Unknown `options_key`** → empty select + editor warning (graceful, as today).
- **`allow_custom` free-entry values** not in the list → stored verbatim and
  displayed verbatim (`term_label` falls back to the raw value).
- **Deleting a referenced vocabulary** → dangling-reference warning in the editor;
  resolution falls back to an empty option set rather than crashing.
- **Duplicate codes within a vocabulary** → rejected on save (validation error).
- **Vocabulary key rename** → disallowed (key immutable; only labels editable).

## 7. Testing

Reproducer-first for any bugfix discovered during implementation (project rule).

- **Unit (`backend/vocabularies.py`):** resolution across stored / built-in /
  missing keys; `term_label` fallback chain (locale → any → code); `select_options`
  returns `{code: label}`; seed idempotency (raw-org-JSON read → seed once → re-run
  is a no-op preserving customized values); duplicate-code rejected on save; key
  immutability; nationalities provider loads ~250 bilingual terms.
- **Editor (`workflow_editor`):** `options_key` picker lists registry keys;
  `compute_warnings` flags an `options_key` not in the registry and a referenced
  vocabulary that was deleted.
- **Integration (`nicegui.testing.User`):** create a vocabulary in the admin UI,
  reference it from a workflow field, render the step form and see options; the
  nationality combo renders demonyms; the directory status select reads from the
  registry.

## 8. Out of scope (Phase 1)

- **Reusable field definitions** — Phase 2, separate spec.
- **`BookingsConfig.os_choices` / `software_tags` migration** — separate;
  `software_tags` is a `dict[str, list[str]]`, not a flat vocabulary.
- **Vocabulary export/import via `data_io.py`** — optional follow-up.
- **Per-term i18n beyond EN/FR** — the labels map supports more locales
  structurally, but only EN/FR are populated.

## Affected files (anticipated)

- **New:** `backend/vocabularies.py`, `backend/data/nationalities.json`,
  `frontend/vocabularies_editor.py` (bespoke admin surface), tests.
- **Modified:** `config.py` (`OrgConfig` loses the 6 list fields),
  `frontend/workflow_step.py` (`_resolve_options` → registry, `{code:label}`
  selects, `display_value`), `frontend/workflow_editor.py` (picker + warnings),
  `frontend/directory.py` (status selects), `backend/personnel_import.py` (status
  validation), `frontend/admin_settings.py` (mount the Vocabularies expansion),
  `app.py` (`ensure_vocabularies_seeded` startup), `frontend/i18n.py` (new labels).
