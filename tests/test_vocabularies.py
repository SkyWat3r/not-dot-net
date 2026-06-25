import pytest
from not_dot_net.backend.vocabularies import (
    VocabularyTerm, StoredVocabulary, VocabulariesConfig,
    vocabularies_config, term_label,
)
from not_dot_net.backend.vocabularies import BUILTIN_VOCABULARIES


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
