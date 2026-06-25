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

from not_dot_net.backend.app_config import section

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
