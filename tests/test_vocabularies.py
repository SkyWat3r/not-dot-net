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
