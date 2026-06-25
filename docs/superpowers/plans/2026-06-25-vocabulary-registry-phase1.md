# Vocabulary Registry (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hardcoded `OrgConfig` option lists with an app-wide, runtime-definable Vocabulary registry, and ship nationalities as a built-in vocabulary.

**Architecture:** A new `backend/vocabularies.py` holds Pydantic models (`VocabularyTerm`, `StoredVocabulary`, `VocabulariesConfig`) stored in one `app_setting` JSON row via the existing `ConfigSection` pattern, plus code-provided *built-in* vocabularies (`nationalities` from a bundled JSON, `roles` from `roles_config`) merged in by a small resolution API. Existing consumers (workflow step forms, the workflow editor picker/warnings, the directory tenure selects) are rewired to that API; the 6 `OrgConfig` list fields are then removed and seeded into the registry by an idempotent startup migration. A bespoke admin editor manages stored vocabularies.

**Tech Stack:** Python (match existing syntax: PEP 695 generics `class C[T]`, `X | None` unions), Pydantic v2, SQLAlchemy 2.x async, NiceGUI 3.4+, pytest with the `nicegui.testing.user_plugin`.

## Global Constraints

- **Storage backing:** ConfigSection JSON blob for stored vocabularies; built-ins are code-provided and NOT stored in the blob. No new DB table, no Alembic migration.
- **Term shape:** a term is `code` (stable stored value) + `labels` (locale→string map, second locale optional). `term_label()` resolves `labels.get(locale) or any-label or code`.
- **`options_key` attribute name is unchanged.** Preserve every existing key: `teams`, `sites`, `employment_statuses`, `employers`, `transport_modes`, `funding_sources`, `roles`. Do not rename them, do not touch `default_workflows.py`.
- **Permission:** managing vocabularies reuses `manage_settings`. No new permission.
- **Vocabulary key is immutable** after creation; only labels are editable.
- **i18n parity:** `validate_translations()` runs at startup and fails on missing keys. Every new `t()` key MUST be added to BOTH `"en"` and `"fr"` in `not_dot_net/frontend/i18n.py`.
- **Bugfix discipline:** any bug found mid-implementation gets a failing reproducer test first.
- **Run the full suite** (`uv run pytest`) before each task's commit; the baseline is green.

## File Structure

- **Create:**
  - `not_dot_net/backend/vocabularies.py` — models, built-ins, resolution API, seeding. One responsibility: the vocabulary registry.
  - `not_dot_net/backend/data/nationalities.json` — curated ISO-keyed EN/FR demonym dataset (package data, shipped by flit).
  - `not_dot_net/frontend/vocabularies_editor.py` — bespoke admin editor surface.
  - `tests/test_vocabularies.py`, `tests/test_vocabularies_seeding.py`, `tests/test_vocabularies_editor.py`.
- **Modify:**
  - `not_dot_net/config.py` — remove the 6 list fields from `OrgConfig`.
  - `not_dot_net/frontend/workflow_step.py` — select rendering via the registry; `resolve_display_values` for code→label display.
  - `not_dot_net/frontend/workflow_editor.py` — picker + `compute_warnings` read a vocab-keys snapshot; drop `_org_list_field_names`.
  - `not_dot_net/frontend/directory.py` — tenure status/employer selects via the registry.
  - `not_dot_net/frontend/workflow_detail.py` — resolve display values before `render_approval`.
  - `not_dot_net/frontend/admin_settings.py` — mount the Vocabularies expansion; skip `"vocabularies"` in the auto-render loop.
  - `not_dot_net/app.py` — call `ensure_vocabularies_seeded()` at startup.
  - `not_dot_net/frontend/i18n.py` — new editor labels (en + fr).
  - `not_dot_net/backend/personnel_import.py` — docstring mention only.
  - `tests/conftest.py` — noop `ensure_vocabularies_seeded` for `user`-fixture tests.
  - `tests/test_config_sections.py`, `tests/test_workflow_editor.py` — update assertions for the moved lists.

---

## Task 1: Vocabulary models + `term_label`

**Files:**
- Create: `not_dot_net/backend/vocabularies.py`
- Test: `tests/test_vocabularies.py`

**Interfaces:**
- Produces: `VocabularyTerm(code: str, labels: dict[str,str], active: bool=True)`; `StoredVocabulary(key: str, label: dict[str,str], allow_custom: bool=False, terms: list[VocabularyTerm]=[])`; `VocabulariesConfig(vocabularies: dict[str, StoredVocabulary]={})`; `vocabularies_config: ConfigSection[VocabulariesConfig]`; `term_label(term: VocabularyTerm, locale: str) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_vocabularies.py
import pytest
from not_dot_net.backend.vocabularies import (
    VocabularyTerm, StoredVocabulary, VocabulariesConfig,
    vocabularies_config, term_label,
)


def test_term_label_prefers_locale():
    term = VocabularyTerm(code="FR", labels={"en": "French", "fr": "Français"})
    assert term_label(term, "fr") == "Français"
    assert term_label(term, "en") == "French"


def test_term_label_falls_back_to_any_then_code():
    only_en = VocabularyTerm(code="X", labels={"en": "Ex"})
    assert term_label(only_en, "fr") == "Ex"          # missing locale -> any label
    no_labels = VocabularyTerm(code="RAW", labels={})
    assert term_label(no_labels, "fr") == "RAW"         # no labels -> code


async def test_vocabularies_config_roundtrip():
    cfg = VocabulariesConfig(vocabularies={
        "funding_sources": StoredVocabulary(
            key="funding_sources", label={"en": "Funding sources"},
            terms=[VocabularyTerm(code="CNES", labels={"en": "CNES"})],
        )
    })
    await vocabularies_config.set(cfg)
    out = await vocabularies_config.get()
    assert out.vocabularies["funding_sources"].terms[0].code == "CNES"
    assert out.vocabularies["funding_sources"].allow_custom is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_vocabularies.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'not_dot_net.backend.vocabularies'`

- [ ] **Step 3: Write minimal implementation**

```python
# not_dot_net/backend/vocabularies.py
"""App-wide vocabulary registry — runtime-definable named option lists.

Stored vocabularies live in one `app_setting` JSON row (the ConfigSection
idiom). Built-in vocabularies (nationalities, roles) are provided by code and
merged in by the resolution API; they are not stored in the blob.
"""

from pydantic import BaseModel, Field

from not_dot_net.backend.app_config import section


class VocabularyTerm(BaseModel):
    code: str                      # stable stored value: "FR", "PostDoc", "CNES"
    labels: dict[str, str] = Field(default_factory=dict)  # locale -> label
    active: bool = True            # inactive: hidden from new picks, kept for old data


class StoredVocabulary(BaseModel):
    key: str                       # immutable registry key
    label: dict[str, str] = Field(default_factory=dict)   # vocabulary's own name
    allow_custom: bool = False     # combo-box free entry
    terms: list[VocabularyTerm] = Field(default_factory=list)


class VocabulariesConfig(BaseModel):
    vocabularies: dict[str, StoredVocabulary] = Field(default_factory=dict)


vocabularies_config = section("vocabularies", VocabulariesConfig, label="Vocabularies")


def term_label(term: VocabularyTerm, locale: str) -> str:
    """Display label for a term: requested locale, else any label, else the code."""
    return term.labels.get(locale) or next(iter(term.labels.values()), "") or term.code
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_vocabularies.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/backend/vocabularies.py tests/test_vocabularies.py
git commit -m "feat(vocabularies): term + stored vocabulary models, term_label"
```

---

## Task 2: Built-in vocabularies (nationalities dataset + providers)

**Files:**
- Create: `not_dot_net/backend/data/nationalities.json`
- Modify: `not_dot_net/backend/vocabularies.py`
- Test: `tests/test_vocabularies.py`

**Interfaces:**
- Consumes: `VocabularyTerm` (Task 1).
- Produces: `BuiltinVocabulary(key: str, label: dict[str,str], load_terms: Callable[[], Awaitable[list[VocabularyTerm]]], editable: bool=False)`; `BUILTIN_VOCABULARIES: dict[str, BuiltinVocabulary]` with keys `"nationalities"` and `"roles"`.

**Nationalities dataset:** a curated, version-controlled JSON array. Each entry is
`{"code": <ISO 3166-1 alpha-2>, "en": <English demonym>, "fr": <French demonym, masculine singular>}`.
Populate the **full ISO 3166-1 set** (≥190 entries). English demonyms from Wikipedia
"List of adjectival and demonymic forms of place names"; French from Wikipédia
"Liste des gentilés". This is curated data, verified by the test in this task — not a placeholder.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_vocabularies.py
from not_dot_net.backend.vocabularies import BUILTIN_VOCABULARIES


async def test_nationalities_builtin_loads_bilingual():
    terms = await BUILTIN_VOCABULARIES["nationalities"].load_terms()
    assert len(terms) >= 190
    by_code = {t.code: t for t in terms}
    assert by_code["FR"].labels == {"en": "French", "fr": "Français"}
    assert by_code["DE"].labels == {"en": "German", "fr": "Allemand"}
    assert by_code["JP"].labels == {"en": "Japanese", "fr": "Japonais"}
    assert BUILTIN_VOCABULARIES["nationalities"].editable is False


async def test_roles_builtin_reflects_roles_config():
    from not_dot_net.backend.roles import roles_config, RolesConfig, RoleDefinition
    await roles_config.set(RolesConfig(roles={"it": RoleDefinition(label="IT Staff")}))
    terms = await BUILTIN_VOCABULARIES["roles"].load_terms()
    assert any(t.code == "it" and t.labels["en"] == "IT Staff" for t in terms)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_vocabularies.py::test_nationalities_builtin_loads_bilingual -v`
Expected: FAIL with `ImportError: cannot import name 'BUILTIN_VOCABULARIES'`

- [ ] **Step 3a: Create the nationalities dataset**

Create `not_dot_net/backend/data/nationalities.json` with the full ISO 3166-1 set.
Starter rows establishing the exact shape (extend to ≥190 entries from the cited sources):

```json
[
  {"code": "FR", "en": "French", "fr": "Français"},
  {"code": "DE", "en": "German", "fr": "Allemand"},
  {"code": "IT", "en": "Italian", "fr": "Italien"},
  {"code": "ES", "en": "Spanish", "fr": "Espagnol"},
  {"code": "PT", "en": "Portuguese", "fr": "Portugais"},
  {"code": "GB", "en": "British", "fr": "Britannique"},
  {"code": "IE", "en": "Irish", "fr": "Irlandais"},
  {"code": "BE", "en": "Belgian", "fr": "Belge"},
  {"code": "NL", "en": "Dutch", "fr": "Néerlandais"},
  {"code": "CH", "en": "Swiss", "fr": "Suisse"},
  {"code": "US", "en": "American", "fr": "Américain"},
  {"code": "CA", "en": "Canadian", "fr": "Canadien"},
  {"code": "JP", "en": "Japanese", "fr": "Japonais"},
  {"code": "CN", "en": "Chinese", "fr": "Chinois"},
  {"code": "IN", "en": "Indian", "fr": "Indien"},
  {"code": "BR", "en": "Brazilian", "fr": "Brésilien"},
  {"code": "MA", "en": "Moroccan", "fr": "Marocain"},
  {"code": "DZ", "en": "Algerian", "fr": "Algérien"},
  {"code": "TN", "en": "Tunisian", "fr": "Tunisien"},
  {"code": "SN", "en": "Senegalese", "fr": "Sénégalais"}
]
```

- [ ] **Step 3b: Add the built-in providers**

```python
# add to not_dot_net/backend/vocabularies.py
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

_NATIONALITIES_PATH = Path(__file__).parent / "data" / "nationalities.json"


@dataclass
class BuiltinVocabulary:
    key: str
    label: dict[str, str]
    load_terms: Callable[[], Awaitable[list[VocabularyTerm]]]
    editable: bool = False


async def _load_nationalities() -> list[VocabularyTerm]:
    entries = json.loads(_NATIONALITIES_PATH.read_text(encoding="utf-8"))
    return [
        VocabularyTerm(code=e["code"], labels={"en": e["en"], "fr": e["fr"]})
        for e in entries
    ]


async def _load_roles() -> list[VocabularyTerm]:
    from not_dot_net.backend.roles import roles_config
    cfg = await roles_config.get()
    return [
        VocabularyTerm(code=key, labels={"en": definition.label or key})
        for key, definition in cfg.roles.items()
    ]


BUILTIN_VOCABULARIES: dict[str, BuiltinVocabulary] = {
    "nationalities": BuiltinVocabulary(
        key="nationalities",
        label={"en": "Nationalities", "fr": "Nationalités"},
        load_terms=_load_nationalities,
    ),
    "roles": BuiltinVocabulary(
        key="roles",
        label={"en": "Roles", "fr": "Rôles"},
        load_terms=_load_roles,
    ),
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_vocabularies.py -v`
Expected: PASS (all). If the nationalities count assertion fails, the dataset is incomplete — finish populating it.

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/backend/vocabularies.py not_dot_net/backend/data/nationalities.json tests/test_vocabularies.py
git commit -m "feat(vocabularies): built-in nationalities (bilingual) and roles providers"
```

---

## Task 3: Resolution API (`resolve_terms`, `field_options`, `list_vocabularies`)

**Files:**
- Modify: `not_dot_net/backend/vocabularies.py`
- Test: `tests/test_vocabularies.py`

**Interfaces:**
- Consumes: `vocabularies_config`, `term_label`, `BUILTIN_VOCABULARIES`.
- Produces: `resolve_terms(key: str, *, active_only: bool=True) -> list[VocabularyTerm]`; `FieldOptions(options: dict[str,str], allow_custom: bool)`; `field_options(key: str, locale: str) -> FieldOptions`; `VocabularyView(key: str, label: dict[str,str], source: str, editable: bool)`; `list_vocabularies() -> list[VocabularyView]`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_vocabularies.py
from not_dot_net.backend.vocabularies import (
    resolve_terms, field_options, list_vocabularies, FieldOptions,
)


async def test_resolve_terms_stored_builtin_and_missing():
    await vocabularies_config.set(VocabulariesConfig(vocabularies={
        "funding_sources": StoredVocabulary(
            key="funding_sources", label={"en": "Funding sources"},
            terms=[
                VocabularyTerm(code="CNES", labels={"en": "CNES"}),
                VocabularyTerm(code="OLD", labels={"en": "Old"}, active=False),
            ],
        )
    }))
    stored = await resolve_terms("funding_sources")
    assert [t.code for t in stored] == ["CNES"]                  # active_only drops OLD
    assert len(await resolve_terms("funding_sources", active_only=False)) == 2
    assert len(await resolve_terms("nationalities")) >= 190      # built-in
    assert await resolve_terms("does_not_exist") == []          # graceful


async def test_field_options_returns_code_to_label_and_allow_custom():
    await vocabularies_config.set(VocabulariesConfig(vocabularies={
        "teams": StoredVocabulary(
            key="teams", label={"en": "Teams"}, allow_custom=True,
            terms=[VocabularyTerm(code="Plasma", labels={"en": "Plasma"})],
        )
    }))
    fo = await field_options("teams", "en")
    assert fo == FieldOptions(options={"Plasma": "Plasma"}, allow_custom=True)
    nat = await field_options("nationalities", "fr")
    assert nat.options["FR"] == "Français"
    assert nat.allow_custom is False


async def test_list_vocabularies_merges_stored_and_builtin():
    await vocabularies_config.set(VocabulariesConfig(vocabularies={
        "teams": StoredVocabulary(key="teams", label={"en": "Teams"}),
    }))
    views = {v.key: v for v in await list_vocabularies()}
    assert views["teams"].source == "stored" and views["teams"].editable is True
    assert views["nationalities"].source == "builtin" and views["nationalities"].editable is False
    assert "roles" in views
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_vocabularies.py::test_resolve_terms_stored_builtin_and_missing -v`
Expected: FAIL with `ImportError: cannot import name 'resolve_terms'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to not_dot_net/backend/vocabularies.py


@dataclass(frozen=True)
class FieldOptions:
    options: dict[str, str]    # code -> display label
    allow_custom: bool


@dataclass(frozen=True)
class VocabularyView:
    key: str
    label: dict[str, str]
    source: str                # "stored" | "builtin"
    editable: bool


async def resolve_terms(key: str, *, active_only: bool = True) -> list[VocabularyTerm]:
    """Terms for a key: stored registry first, then built-in providers, else []."""
    cfg = await vocabularies_config.get()
    stored = cfg.vocabularies.get(key)
    if stored is not None:
        terms = stored.terms
    else:
        builtin = BUILTIN_VOCABULARIES.get(key)
        if builtin is None:
            return []
        terms = await builtin.load_terms()
    return [t for t in terms if t.active] if active_only else list(terms)


async def field_options(key: str, locale: str) -> FieldOptions:
    """Render spec for a select bound to `key`: {code: label} + allow_custom."""
    cfg = await vocabularies_config.get()
    stored = cfg.vocabularies.get(key)
    allow_custom = stored.allow_custom if stored is not None else False
    terms = await resolve_terms(key)
    return FieldOptions(
        options={t.code: term_label(t, locale) for t in terms},
        allow_custom=allow_custom,
    )


async def list_vocabularies() -> list[VocabularyView]:
    """All vocabularies for admin/editor: stored (editable) + built-ins."""
    cfg = await vocabularies_config.get()
    views = [
        VocabularyView(key=v.key, label=v.label, source="stored", editable=True)
        for v in cfg.vocabularies.values()
    ]
    views += [
        VocabularyView(key=b.key, label=b.label, source="builtin", editable=b.editable)
        for b in BUILTIN_VOCABULARIES.values() if b.key not in cfg.vocabularies
    ]
    return views
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_vocabularies.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/backend/vocabularies.py tests/test_vocabularies.py
git commit -m "feat(vocabularies): resolution API (resolve_terms, field_options, list_vocabularies)"
```

---

## Task 4: Idempotent seeding + startup wiring

**Files:**
- Modify: `not_dot_net/backend/vocabularies.py`, `not_dot_net/app.py`, `tests/conftest.py`
- Test: `tests/test_vocabularies_seeding.py`

**Interfaces:**
- Consumes: `vocabularies_config`, models (Task 1).
- Produces: `ensure_vocabularies_seeded() -> None`.

The seed reads the **raw `org` `app_setting` row** so admin-customized list values survive, falling back to embedded defaults. It is idempotent: once the `vocabularies` section is non-empty, it returns immediately. It does NOT read `OrgConfig` (which loses those fields in Task 8), so it carries its own `_SEED_DEFAULTS`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_vocabularies_seeding.py
import pytest
from not_dot_net.backend.app_config import AppSetting
from not_dot_net.backend.db import session_scope
from not_dot_net.backend.vocabularies import (
    ensure_vocabularies_seeded, vocabularies_config, _SEED_DEFAULTS,
)


async def _set_org_raw(value: dict):
    async with session_scope() as session:
        session.add(AppSetting(key="org", value=value))
        await session.commit()


async def test_seed_uses_customized_org_values():
    await _set_org_raw({"app_name": "X", "funding_sources": ["ANR", "ERC"]})
    await ensure_vocabularies_seeded()
    cfg = await vocabularies_config.get()
    assert set(cfg.vocabularies) == set(_SEED_DEFAULTS)
    funding = [t.code for t in cfg.vocabularies["funding_sources"].terms]
    assert funding == ["ANR", "ERC"]                    # customized values win
    teams = [t.code for t in cfg.vocabularies["teams"].terms]
    assert teams == _SEED_DEFAULTS["teams"]             # untouched key -> defaults


async def test_seed_is_idempotent():
    await ensure_vocabularies_seeded()
    await ensure_vocabularies_seeded()                  # second run is a no-op
    cfg = await vocabularies_config.get()
    assert set(cfg.vocabularies) == set(_SEED_DEFAULTS)


async def test_seed_falls_back_to_defaults_without_org_row():
    await ensure_vocabularies_seeded()
    cfg = await vocabularies_config.get()
    assert [t.code for t in cfg.vocabularies["sites"].terms] == _SEED_DEFAULTS["sites"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_vocabularies_seeding.py -v`
Expected: FAIL with `ImportError: cannot import name 'ensure_vocabularies_seeded'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to not_dot_net/backend/vocabularies.py
from not_dot_net.backend.app_config import AppSetting
from not_dot_net.backend.db import session_scope

_SEED_DEFAULTS: dict[str, list[str]] = {
    "teams": ["Plasma Physics", "Instrumentation", "Space Weather",
              "Theory & Simulation", "Administration"],
    "sites": ["Palaiseau", "Jussieu"],
    "employment_statuses": ["CDD", "CDI", "Intern", "PhD", "PostDoc", "Visiting Researcher"],
    "employers": ["CNRS", "Sorbonne Université", "Polytechnique", "CNES", "Other"],
    "transport_modes": ["Train", "Avion", "Voiture personnelle",
                        "Voiture de service", "Autre"],
    "funding_sources": ["Sorbonne Université", "Polytechnique", "CNES",
                        "ANR", "ESA", "Autre"],
}

_SEED_LABELS: dict[str, dict[str, str]] = {
    "teams": {"en": "Teams", "fr": "Équipes"},
    "sites": {"en": "Sites", "fr": "Sites"},
    "employment_statuses": {"en": "Employment statuses", "fr": "Statuts d'emploi"},
    "employers": {"en": "Employers", "fr": "Employeurs"},
    "transport_modes": {"en": "Transport modes", "fr": "Modes de transport"},
    "funding_sources": {"en": "Funding sources", "fr": "Sources de financement"},
}


async def _read_raw_org_lists() -> dict:
    async with session_scope() as session:
        row = await session.get(AppSetting, "org")
        return dict(row.value) if row and isinstance(row.value, dict) else {}


async def ensure_vocabularies_seeded() -> None:
    """One-time migration: fold the legacy OrgConfig lists into the registry.

    Reads the raw `org` row so admin-customized values survive; falls back to
    embedded defaults. Idempotent — a no-op once the registry is populated.
    """
    cfg = await vocabularies_config.get()
    if cfg.vocabularies:
        return
    raw = await _read_raw_org_lists()
    vocabularies = {}
    for key, default in _SEED_DEFAULTS.items():
        values = raw.get(key) if isinstance(raw.get(key), list) else None
        values = values or default
        vocabularies[key] = StoredVocabulary(
            key=key,
            label=_SEED_LABELS[key],
            terms=[VocabularyTerm(code=v, labels={"en": v}) for v in values],
        )
    await vocabularies_config.set(VocabulariesConfig(vocabularies=vocabularies))
```

- [ ] **Step 4: Wire into app startup**

In `not_dot_net/app.py`, inside `async def startup()`, immediately after the dev
table-create block (the `if dev_mode: await create_db_and_tables()` lines, ~line 113),
add an unconditional call (prod tables already exist via migrations):

```python
        from not_dot_net.backend.vocabularies import ensure_vocabularies_seeded
        await ensure_vocabularies_seeded()
        logger.info("Vocabularies seeded")
```

- [ ] **Step 5: Noop the seed for `user`-fixture tests**

In `tests/conftest.py`, inside the `if "user" in request.fixturenames:` block (next to
the existing `_noop_create_tables` / `_noop_default_admin` patches), add:

```python
        import not_dot_net.backend.vocabularies as vocabularies_module

        async def _noop_seed_vocabularies():
            return None

        monkeypatch.setattr(vocabularies_module, "ensure_vocabularies_seeded",
                            _noop_seed_vocabularies)
```

(Integration tests that need vocabularies set them up explicitly via
`vocabularies_config.set(...)`.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_vocabularies_seeding.py -v && uv run pytest -q`
Expected: seeding tests PASS; full suite still green.

- [ ] **Step 7: Commit**

```bash
git add not_dot_net/backend/vocabularies.py not_dot_net/app.py tests/conftest.py tests/test_vocabularies_seeding.py
git commit -m "feat(vocabularies): idempotent startup seed from legacy OrgConfig lists"
```

---

## Task 5: Rewire workflow step select rendering to the registry

**Files:**
- Modify: `not_dot_net/frontend/workflow_step.py` (`_render_field` select branch, ~127-131; remove `_resolve_options`, ~616-644)
- Test: `tests/test_widgets.py`

**Interfaces:**
- Consumes: `field_options` (Task 3).
- Produces: select fields whose stored value is the term `code`, display is the label; `allow_custom` vocabularies render as a free-entry combo box.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_widgets.py — add (uses the existing `user` fixture pattern in that file)
import pytest
from nicegui.testing import User
from not_dot_net.backend.vocabularies import (
    vocabularies_config, VocabulariesConfig, StoredVocabulary, VocabularyTerm,
)


async def test_select_field_renders_vocabulary_labels(user: User):
    await vocabularies_config.set(VocabulariesConfig(vocabularies={
        "nationalities_demo": StoredVocabulary(
            key="nationalities_demo", label={"en": "Nat"},
            terms=[VocabularyTerm(code="FR", labels={"en": "French", "fr": "Français"})],
        )
    }))
    from not_dot_net.frontend.workflow_step import _render_field
    from nicegui import ui
    from not_dot_net.config import FieldConfig
    fields: dict = {}

    @ui.page("/_t_select")
    async def _p():
        await _render_field(
            FieldConfig(name="nat", type="select", options_key="nationalities_demo"),
            data={}, fields=fields, files={}, on_file_upload=None,
            max_upload_size_mb=2, width_class="w-full",
        )

    await user.open("/_t_select")
    # The select's options map code -> label.
    assert fields["nat"].options == {"FR": "French"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_widgets.py::test_select_field_renders_vocabulary_labels -v`
Expected: FAIL — `fields["nat"].options` is the old list `[]` (or AttributeError), not `{"FR": "French"}`.

- [ ] **Step 3: Rewrite the select branch**

In `not_dot_net/frontend/workflow_step.py`, replace the `elif field_cfg.type == "select":` branch (lines ~127-131) with:

```python
    elif field_cfg.type == "select":
        from not_dot_net.backend.vocabularies import field_options
        spec = await field_options(field_cfg.options_key, get_locale())
        select = ui.select(
            label=label, options=spec.options,
            value=value if value in spec.options else None,
            new_value_mode="add-unique" if spec.allow_custom else None,
            with_input=spec.allow_custom,
        ).props("outlined dense stack-label").classes(width_class)
        if spec.allow_custom:
            select.props("use-input fill-input hide-selected")
        fields[field_cfg.name] = select
```

Then delete the now-unused `_resolve_options` function (lines ~616-644). Confirm
`get_locale` is imported in this module (it is — `render_approval` uses it).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_widgets.py -v && uv run pytest -q`
Expected: new test PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/frontend/workflow_step.py tests/test_widgets.py
git commit -m "feat(vocabularies): workflow select fields resolve from the registry"
```

---

## Task 6: Rewire the workflow editor picker + warnings

**Files:**
- Modify: `not_dot_net/frontend/workflow_editor.py` (`create()` ~83; `_org_list_field_names` ~32; `org_keys` ~774; `compute_warnings` ~977-1004)
- Test: `tests/test_workflow_editor.py` (replace the `_org_list_field_names` test ~468)

**Interfaces:**
- Consumes: `list_vocabularies` (Task 3).
- Produces: `WorkflowEditorDialog._vocab_keys: list[str]` snapshot used by the picker and warnings.

- [ ] **Step 1: Write the failing test**

Delete the existing `test_org_list_keys_introspected` (~467-474, it imports the
soon-deleted `_org_list_field_names`) and add these. They follow the file's established
pattern: create the dialog inside an `@ui.page` and capture it after `user.open` (because
`create()` builds `ui.dialog()` and needs a client slot).

```python
# tests/test_workflow_editor.py
async def test_options_key_picker_lists_registry_keys(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    from not_dot_net.backend.vocabularies import (
        vocabularies_config, VocabulariesConfig, StoredVocabulary)
    await vocabularies_config.set(VocabulariesConfig(vocabularies={
        "employment_statuses": StoredVocabulary(key="employment_statuses", label={"en": "S"}),
    }))
    captured = {}

    @ui.page("/_vk1")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_vk1")
    keys = captured["dlg"]._vocab_keys
    assert "employment_statuses" in keys   # stored
    assert "nationalities" in keys          # built-in
    assert "roles" in keys                   # built-in


async def test_compute_warnings_flags_unknown_options_key(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    from not_dot_net.backend.vocabularies import (
        vocabularies_config, VocabulariesConfig, StoredVocabulary)
    await vocabularies_config.set(VocabulariesConfig(vocabularies={
        "teams": StoredVocabulary(key="teams", label={"en": "T"})}))
    await workflows_config.set(WorkflowsConfig(workflows={"wf": WorkflowConfig(
        label="WF", steps=[WorkflowStepConfig(key="s", type="form", fields=[
            FieldConfig(name="f", type="select", options_key="ghost")])])}))
    captured = {}

    @ui.page("/_vk2")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_vk2")
    assert any("options_key 'ghost'" in w for w in captured["dlg"].compute_warnings())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_workflow_editor.py::test_options_key_picker_lists_registry_keys -v`
Expected: FAIL with `AttributeError: 'WorkflowEditorDialog' object has no attribute '_vocab_keys'`

- [ ] **Step 3: Snapshot vocab keys in `create()`**

In `not_dot_net/frontend/workflow_editor.py`, in the async `create()` classmethod
(before `instance._build()`):

```python
        from not_dot_net.backend.vocabularies import list_vocabularies
        instance._vocab_keys = [v.key for v in await list_vocabularies()]
```

- [ ] **Step 4: Use the snapshot in the picker and warnings; drop `_org_list_field_names`**

At ~774, change:

```python
            org_keys = [None, *self._vocab_keys]
```

In `compute_warnings` (~978), change `org_list_keys = set(_org_list_field_names())` to:

```python
        org_list_keys = set(self._vocab_keys)
```

In the same function, update the warning message (~1001-1004) so it no longer says
"OrgConfig list field":

```python
                    if f.options_key and f.options_key not in org_list_keys:
                        warnings.append(
                            f"[{wf_key}/{step.key}/{f.name}] options_key '{f.options_key}' is not a known vocabulary"
                        )
```

Delete the `_org_list_field_names` function (~32-37) and its `OrgConfig` import if now unused.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_workflow_editor.py -v && uv run pytest -q`
Expected: PASS; full suite green.

- [ ] **Step 6: Commit**

```bash
git add not_dot_net/frontend/workflow_editor.py tests/test_workflow_editor.py
git commit -m "feat(vocabularies): editor picker + warnings read the vocabulary registry"
```

---

## Task 7: Rewire directory tenure selects to the registry

**Files:**
- Modify: `not_dot_net/frontend/directory.py` (`_tenure_add_dialog` ~699-708, `_tenure_edit_dialog` ~749-762)
- Test: `tests/test_tenure_onboarding.py`

**Interfaces:**
- Consumes: `field_options` (Task 3).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tenure_onboarding.py — add (no user fixture: tests the shared helper)
async def test_tenure_options_from_registry():
    from not_dot_net.backend.vocabularies import (
        vocabularies_config, VocabulariesConfig, StoredVocabulary, VocabularyTerm)
    await vocabularies_config.set(VocabulariesConfig(vocabularies={
        "employment_statuses": StoredVocabulary(
            key="employment_statuses", label={"en": "S"},
            terms=[VocabularyTerm(code="PostDoc", labels={"en": "PostDoc"})]),
        "employers": StoredVocabulary(
            key="employers", label={"en": "E"},
            terms=[VocabularyTerm(code="CNRS", labels={"en": "CNRS"})]),
    }))
    from not_dot_net.frontend.directory import _tenure_options
    status, employer = await _tenure_options("en")
    assert status == {"PostDoc": "PostDoc"}
    assert employer == {"CNRS": "CNRS"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tenure_onboarding.py -k tenure_options_from_registry -v`
Expected: FAIL with `ImportError: cannot import name '_tenure_options'`

- [ ] **Step 3: Add the shared helper and use it in both dialogs**

In `not_dot_net/frontend/directory.py`, add a module-level helper:

```python
async def _tenure_options(locale: str) -> tuple[dict[str, str], dict[str, str]]:
    """(status_options, employer_options) for the tenure dialogs, from the registry."""
    from not_dot_net.backend.vocabularies import field_options
    status = (await field_options("employment_statuses", locale)).options
    employer = (await field_options("employers", locale)).options
    return status, employer
```

In `_tenure_add_dialog`, replace the `cfg = await org_config.get()` usage and the two
`ui.select(cfg.employment_statuses...)` / `ui.select(cfg.employers...)` lines with:

```python
    from not_dot_net.frontend.i18n import get_locale
    status_opts, employer_opts = await _tenure_options(get_locale())
    ...
        status_input = ui.select(status_opts, label=t("status")).props("outlined dense stack-label")
        employer_input = ui.select(employer_opts, label=t("employer")).props("outlined dense stack-label")
```

Apply the same change in `_tenure_edit_dialog`, preserving the `value=tenure.status` /
`value=tenure.employer` kwargs (the stored value is a code that matches the seeded
`code == label`). Remove the now-unused `from not_dot_net.config import org_config` /
`cfg = await org_config.get()` lines if nothing else in the function needs them.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_tenure_onboarding.py -v && uv run pytest -q`
Expected: PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/frontend/directory.py tests/test_tenure_onboarding.py
git commit -m "feat(vocabularies): directory tenure selects read from the registry"
```

---

## Task 8: Remove the 6 list fields from `OrgConfig`

**Files:**
- Modify: `not_dot_net/config.py` (`OrgConfig` ~76-97), `not_dot_net/backend/personnel_import.py` (docstring ~8)
- Test: `tests/test_config_sections.py` (update ~8-36)

**Interfaces:**
- After this task `OrgConfig` has only `app_name` and `base_url`. Consumers were rewired in Tasks 5-7; seeding (Task 4) carries its own defaults.

- [ ] **Step 1: Update the failing tests first**

In `tests/test_config_sections.py`, rewrite `test_org_config_defaults` and
`test_org_config_roundtrip` so they no longer reference the moved fields:

```python
async def test_org_config_defaults():
    from not_dot_net.config import org_config
    cfg = await org_config.get()
    assert cfg.app_name
    assert cfg.base_url


async def test_org_config_roundtrip():
    from not_dot_net.config import org_config, OrgConfig
    custom = OrgConfig(app_name="X", base_url="http://x")
    await org_config.set(custom)
    result = await org_config.get()
    assert result.app_name == "X"
    assert result.base_url == "http://x"
```

(Leave `test_org_config_ignores_legacy_allowed_origins` as-is — extra keys are still ignored,
which now also covers the removed list keys.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config_sections.py -v`
Expected: the two rewritten tests FAIL only if fields are still present and conflict — more
importantly, run `uv run pytest -q` and confirm nothing *else* references the removed fields
at import time. Note any failures to fix in Step 3.

- [ ] **Step 3: Remove the fields**

In `not_dot_net/config.py`, reduce `OrgConfig` to:

```python
class OrgConfig(BaseModel):
    app_name: str = "LPP Intranet"
    base_url: str = "http://localhost:8088"
```

In `not_dot_net/backend/personnel_import.py`, update the docstring line that reads
`- status is already one of OrgConfig.employment_statuses;` to
`- status is already one of the "employment_statuses" vocabulary;`.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: green. Fix any remaining reference to a removed `OrgConfig` field by pointing it
at the registry (there should be none after Tasks 5-7).

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/config.py not_dot_net/backend/personnel_import.py tests/test_config_sections.py
git commit -m "refactor(vocabularies): drop the 6 list fields from OrgConfig (now in the registry)"
```

---

## Task 9: Code→label display in the approval view

**Files:**
- Modify: `not_dot_net/frontend/workflow_step.py` (add `resolve_display_values`), `not_dot_net/frontend/workflow_detail.py` (~393 call site)
- Test: `tests/test_workflow_notifications_integration.py` (or a new `tests/test_workflow_display_values.py`)

**Interfaces:**
- Consumes: `resolve_terms`, `term_label` (Task 3); `WorkflowConfig` field definitions.
- Produces: `resolve_display_values(workflow, data: dict, locale: str) -> dict[str, str]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_workflow_display_values.py
import pytest
from not_dot_net.backend.vocabularies import (
    vocabularies_config, VocabulariesConfig, StoredVocabulary, VocabularyTerm)
from not_dot_net.config import WorkflowConfig, WorkflowStepConfig, FieldConfig
from not_dot_net.frontend.workflow_step import resolve_display_values


async def test_resolve_display_values_maps_codes_to_labels():
    await vocabularies_config.set(VocabulariesConfig(vocabularies={
        "nat": StoredVocabulary(key="nat", label={"en": "Nat"}, terms=[
            VocabularyTerm(code="FR", labels={"en": "French", "fr": "Français"})]),
    }))
    wf = WorkflowConfig(label="WF", steps=[WorkflowStepConfig(
        key="s", type="form", fields=[
            FieldConfig(name="nationality", type="select", options_key="nat"),
            FieldConfig(name="comment", type="textarea"),
        ])])
    out = await resolve_display_values(wf, {"nationality": "FR", "comment": "hi"}, "fr")
    assert out["nationality"] == "Français"     # code resolved to label
    assert out["comment"] == "hi"                # non-vocabulary value untouched


async def test_resolve_display_values_passes_through_unknown_codes():
    wf = WorkflowConfig(label="WF", steps=[WorkflowStepConfig(
        key="s", type="form", fields=[
            FieldConfig(name="nationality", type="select", options_key="nat")])])
    out = await resolve_display_values(wf, {"nationality": "ZZ"}, "en")
    assert out["nationality"] == "ZZ"            # custom/unknown code shown verbatim
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_workflow_display_values.py -v`
Expected: FAIL with `ImportError: cannot import name 'resolve_display_values'`

- [ ] **Step 3: Implement `resolve_display_values`**

```python
# add to not_dot_net/frontend/workflow_step.py
from not_dot_net.backend.vocabularies import resolve_terms, term_label


async def resolve_display_values(workflow, data: dict, locale: str) -> dict[str, str]:
    """Map a request's stored values to display strings, resolving select codes
    (which may differ from their label, e.g. nationalities) to their label."""
    field_keys = {
        f.name: f.options_key
        for s in workflow.steps for f in s.fields
        if f.type == "select" and f.options_key
    }
    resolved: dict[str, str] = {}
    for key, value in data.items():
        options_key = field_keys.get(key)
        if options_key and value:
            terms = {t.code: t for t in await resolve_terms(options_key, active_only=False)}
            term = terms.get(value)
            resolved[key] = term_label(term, locale) if term else value
        else:
            resolved[key] = value
    return resolved
```

- [ ] **Step 4: Use it at the approval call site**

In `not_dot_net/frontend/workflow_detail.py` (~393), resolve before rendering:

```python
            from not_dot_net.frontend.workflow_step import resolve_display_values
            from not_dot_net.frontend.i18n import get_locale
            display_data = await resolve_display_values(wf, req.data, get_locale())
            render_approval(display_data, wf, step_config, handle_approve, handle_reject, corrections_fn)
```

(`render_approval` is display-only; passing the resolved dict changes nothing else.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_workflow_display_values.py -v && uv run pytest -q`
Expected: PASS; full suite green.

- [ ] **Step 6: Commit**

```bash
git add not_dot_net/frontend/workflow_step.py not_dot_net/frontend/workflow_detail.py tests/test_workflow_display_values.py
git commit -m "feat(vocabularies): resolve select codes to labels in the approval view"
```

---

## Task 10: Bespoke Vocabularies admin editor

**Files:**
- Create: `not_dot_net/frontend/vocabularies_editor.py`
- Modify: `not_dot_net/frontend/admin_settings.py` (mount expansion ~60; skip `"vocabularies"` in loop ~64), `not_dot_net/frontend/i18n.py` (new keys, en+fr)
- Test: `tests/test_vocabularies_editor.py`

**Interfaces:**
- Consumes: `list_vocabularies`, `vocabularies_config`, models (Tasks 1, 3); `display_name_to_key` (`workflow_editor_options.py`).
- Produces: `render(user)` async entry point that registers/edits stored vocabularies.

**Behavior:** list stored (editable) + built-in (`source=="builtin"`, read-only, badged)
vocabularies; select one to edit its terms table (`code | en | fr | active`) with
add/remove/reorder, the `allow_custom` toggle, and the vocabulary label; create a new
vocabulary (name-first → `display_name_to_key(name, taken)`), key immutable; save validates
that codes are unique within a vocabulary (raises on duplicate); delete a stored vocabulary.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_vocabularies_editor.py
import pytest
from nicegui.testing import User
from not_dot_net.backend.vocabularies import (
    vocabularies_config, VocabulariesConfig, StoredVocabulary, VocabularyTerm)


async def test_save_vocabulary_persists_and_rejects_duplicate_codes():
    from not_dot_net.frontend.vocabularies_editor import save_vocabulary
    ok = StoredVocabulary(key="grades", label={"en": "Grades"}, terms=[
        VocabularyTerm(code="A", labels={"en": "A"}),
        VocabularyTerm(code="B", labels={"en": "B"})])
    await save_vocabulary(ok)
    cfg = await vocabularies_config.get()
    assert [t.code for t in cfg.vocabularies["grades"].terms] == ["A", "B"]

    dup = StoredVocabulary(key="grades", label={"en": "Grades"}, terms=[
        VocabularyTerm(code="A", labels={"en": "A"}),
        VocabularyTerm(code="A", labels={"en": "A2"})])
    with pytest.raises(ValueError, match="duplicate code"):
        await save_vocabulary(dup)


async def test_editor_lists_stored_and_builtin(user: User, admin_user):
    from nicegui import ui
    from not_dot_net.frontend.vocabularies_editor import render as render_vocabularies
    await vocabularies_config.set(VocabulariesConfig(vocabularies={
        "teams": StoredVocabulary(key="teams", label={"en": "Teams"})}))

    @ui.page("/_voc1")
    async def _page():
        await render_vocabularies(admin_user)

    await user.open("/_voc1")
    await user.should_see("Teams")          # stored vocabulary
    await user.should_see("Nationalities")  # built-in vocabulary
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_vocabularies_editor.py::test_save_vocabulary_persists_and_rejects_duplicate_codes -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'not_dot_net.frontend.vocabularies_editor'`

- [ ] **Step 3: Implement the editor module**

```python
# not_dot_net/frontend/vocabularies_editor.py
"""Bespoke admin editor for stored vocabularies (Settings -> Vocabularies)."""

from nicegui import ui

from not_dot_net.backend.permissions import check_permission
from not_dot_net.backend.vocabularies import (
    VocabulariesConfig, StoredVocabulary, VocabularyTerm,
    vocabularies_config, list_vocabularies,
)
from not_dot_net.frontend.i18n import t, get_locale
from not_dot_net.frontend.workflow_editor_options import display_name_to_key


async def save_vocabulary(vocabulary: StoredVocabulary) -> None:
    """Validate (unique codes) and upsert one stored vocabulary."""
    codes = [term.code for term in vocabulary.terms]
    dupes = {c for c in codes if codes.count(c) > 1}
    if dupes:
        raise ValueError(f"duplicate code(s): {', '.join(sorted(dupes))}")
    cfg = await vocabularies_config.get()
    cfg.vocabularies[vocabulary.key] = vocabulary
    await vocabularies_config.set(cfg)


async def delete_vocabulary(key: str) -> None:
    cfg = await vocabularies_config.get()
    cfg.vocabularies.pop(key, None)
    await vocabularies_config.set(cfg)


async def render(user) -> None:
    await check_permission(user, "manage_settings")
    container = ui.column().classes("w-full")

    async def refresh():
        container.clear()
        views = await list_vocabularies()
        cfg = await vocabularies_config.get()
        with container:
            for view in sorted(views, key=lambda v: v.key):
                with ui.row().classes("w-full items-center gap-2"):
                    ui.label(view.label.get(get_locale()) or view.key).classes("font-medium")
                    if view.source == "builtin":
                        ui.badge(t("vocab_system")).props("color=grey")
                    else:
                        stored = cfg.vocabularies[view.key]
                        ui.button(t("edit"),
                                  on_click=lambda s=stored: _open_term_editor(s, refresh)
                                  ).props("flat dense")
                        ui.button(icon="delete",
                                  on_click=lambda k=view.key: _confirm_delete(k, refresh)
                                  ).props("flat dense color=negative")
            ui.button(t("vocab_new"), icon="add",
                      on_click=lambda: _prompt_new_vocabulary(cfg, refresh)).props("flat")

    await refresh()


def _prompt_new_vocabulary(cfg: VocabulariesConfig, on_done) -> None:
    dlg = ui.dialog()
    with dlg, ui.card():
        ui.label(t("vocab_new"))
        name = ui.input(t("vocab_name")).props("outlined dense")

        async def create():
            key = display_name_to_key(name.value or "", set(cfg.vocabularies), fallback_prefix="vocab")
            await save_vocabulary(StoredVocabulary(key=key, label={get_locale(): name.value or key}))
            dlg.close()
            await on_done()

        ui.button("OK", on_click=create).props("color=primary")
    dlg.open()


def _confirm_delete(key: str, on_done) -> None:
    dlg = ui.dialog()
    with dlg, ui.card():
        ui.label(t("vocab_confirm_delete", key=key))

        async def do():
            await delete_vocabulary(key)
            dlg.close()
            await on_done()

        with ui.row():
            ui.button(t("delete"), on_click=do).props("color=negative")
            ui.button(t("cancel"), on_click=dlg.close).props("flat")
    dlg.open()


def _open_term_editor(vocabulary: StoredVocabulary, on_done) -> None:
    """Edit the terms table (code | en | fr | active) + allow_custom, then save."""
    dlg = ui.dialog().props("maximized")
    working = vocabulary.model_copy(deep=True)
    with dlg, ui.card().classes("w-full h-full"):
        ui.label(working.key).classes("text-h6")
        allow = ui.switch(t("vocab_allow_custom"), value=working.allow_custom)
        rows = ui.column().classes("w-full")

        def render_rows():
            rows.clear()
            with rows:
                for i, term in enumerate(working.terms):
                    with ui.row().classes("items-center gap-2"):
                        ui.input(t("vocab_code"), value=term.code,
                                 on_change=lambda e, i=i: _set(working, i, "code", e.value)
                                 ).props("outlined dense")
                        ui.input("EN", value=term.labels.get("en", ""),
                                 on_change=lambda e, i=i: _set_label(working, i, "en", e.value)
                                 ).props("outlined dense")
                        ui.input("FR", value=term.labels.get("fr", ""),
                                 on_change=lambda e, i=i: _set_label(working, i, "fr", e.value)
                                 ).props("outlined dense")
                        ui.switch(t("active"), value=term.active,
                                  on_change=lambda e, i=i: _set(working, i, "active", e.value))
                        ui.button(icon="delete",
                                  on_click=lambda i=i: (_del(working, i), render_rows())
                                  ).props("flat dense color=negative")

        def add_row():
            working.terms.append(VocabularyTerm(code="", labels={}))
            render_rows()

        async def save():
            working.allow_custom = allow.value
            try:
                await save_vocabulary(working)
            except ValueError as exc:
                ui.notify(str(exc), color="negative")
                return
            dlg.close()
            await on_done()

        ui.button(t("vocab_add_term"), icon="add", on_click=add_row).props("flat")
        render_rows()
        with ui.row():
            ui.button(t("save"), on_click=save).props("color=primary")
            ui.button(t("cancel"), on_click=dlg.close).props("flat")
    dlg.open()


def _set(voc: StoredVocabulary, i: int, attr: str, value) -> None:
    setattr(voc.terms[i], attr, value)


def _set_label(voc: StoredVocabulary, i: int, locale: str, value: str) -> None:
    if value:
        voc.terms[i].labels[locale] = value
    else:
        voc.terms[i].labels.pop(locale, None)


def _del(voc: StoredVocabulary, i: int) -> None:
    del voc.terms[i]
```

- [ ] **Step 4: Mount in admin settings + skip auto-render**

In `not_dot_net/frontend/admin_settings.py`:

(a) add the import near the top: `from not_dot_net.frontend.vocabularies_editor import render as render_vocabularies`

(b) after the personnel-import expansion block (~62), add:

```python
    with ui.expansion(t("vocabularies"), icon="list").classes("w-full"):
        await render_vocabularies(user)
```

(c) in the registry loop (`for prefix, cfg_section in sorted(registry.items()):`, ~64),
skip the vocabularies section (it has its own editor):

```python
    for prefix, cfg_section in sorted(registry.items()):
        if prefix == "vocabularies":
            continue
        current = await cfg_section.get()
```

- [ ] **Step 5: Add i18n keys (en + fr)**

In `not_dot_net/frontend/i18n.py`, add to BOTH the `"en"` and `"fr"` dicts (parity is
enforced by `validate_translations()`):

```python
        # en
        "vocabularies": "Vocabularies",
        "vocab_system": "system",
        "vocab_new": "New vocabulary",
        "vocab_name": "Name",
        "vocab_confirm_delete": "Delete vocabulary '{key}'?",
        "vocab_allow_custom": "Allow custom entries",
        "vocab_code": "Code",
        "vocab_add_term": "Add term",
```
```python
        # fr
        "vocabularies": "Vocabulaires",
        "vocab_system": "système",
        "vocab_new": "Nouveau vocabulaire",
        "vocab_name": "Nom",
        "vocab_confirm_delete": "Supprimer le vocabulaire « {key} » ?",
        "vocab_allow_custom": "Autoriser les saisies libres",
        "vocab_code": "Code",
        "vocab_add_term": "Ajouter un terme",
```

(Reuse existing keys for `edit`, `delete`, `cancel`, `save`, `active` — confirm they exist;
add any that don't, in both locales.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_vocabularies_editor.py -v && uv run pytest -q`
Expected: PASS; full suite green; `validate_translations()` raises nothing at startup.

- [ ] **Step 7: Commit**

```bash
git add not_dot_net/frontend/vocabularies_editor.py not_dot_net/frontend/admin_settings.py not_dot_net/frontend/i18n.py tests/test_vocabularies_editor.py
git commit -m "feat(vocabularies): bespoke admin editor for stored vocabularies"
```

---

## Final verification

- [ ] Run the whole suite: `uv run pytest -q` — green.
- [ ] Manual smoke (dev): `uv run python -m not_dot_net.cli serve --host localhost --port 8088`, log in, open Settings → Vocabularies, create a vocabulary and add terms; add a `nationalities` select field to a workflow in the workflow editor and confirm the combo shows demonyms; submit and confirm the approval view shows the label, not the code.
- [ ] Confirm `not_dot_net/backend/data/nationalities.json` ships in the built package (flit includes package data under `not_dot_net/`).
