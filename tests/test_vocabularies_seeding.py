import pytest
from not_dot_net.backend.app_config import AppSetting
from not_dot_net.backend.db import session_scope
from not_dot_net.backend.vocabularies import (
    ensure_vocabularies_seeded, vocabularies_config, _SEED_DEFAULTS, term_label,
)


async def test_seeded_terms_have_no_language_labels():
    """Migrated legacy values are untranslated codes — they must NOT be stuffed
    into the English (or any) label box. The value displays via the code, so it
    renders identically in every locale (as it did before the registry)."""
    await ensure_vocabularies_seeded()
    cfg = await vocabularies_config.get()
    for vocab in cfg.vocabularies.values():
        for term in vocab.terms:
            assert term.labels == {}                      # no false language claim
            assert term_label(term, "en") == term.code    # displays via the code
            assert term_label(term, "fr") == term.code


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
