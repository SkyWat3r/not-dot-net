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
