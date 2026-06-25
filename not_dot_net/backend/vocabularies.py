"""App-wide vocabulary registry — runtime-definable named option lists.

Stored vocabularies live in one `app_setting` JSON row (the ConfigSection
idiom). Built-in vocabularies (nationalities, roles) are provided by code and
merged in by the resolution API; they are not stored in the blob.
"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from not_dot_net.backend.app_config import AppSetting, section
from not_dot_net.backend.db import session_scope

_NATIONALITIES_PATH = Path(__file__).parent / "data" / "nationalities.json"


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


async def resolve_terms(
    key: str, *, active_only: bool = True, cfg: "VocabulariesConfig | None" = None
) -> list[VocabularyTerm]:
    """Terms for a key: stored registry first, then built-in providers, else []."""
    if cfg is None:
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
    terms = await resolve_terms(key, cfg=cfg)
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


# ---------------------------------------------------------------------------
# Startup seeding — fold legacy OrgConfig lists into the registry
# ---------------------------------------------------------------------------

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
