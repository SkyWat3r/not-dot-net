import pytest
from not_dot_net.backend.vocabularies import (
    VocabularyTerm, StoredVocabulary, VocabulariesConfig,
    vocabularies_config, term_label,
    resolve_terms, field_options, list_vocabularies, FieldOptions,
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
