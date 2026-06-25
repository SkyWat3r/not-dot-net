import pytest
from nicegui.testing import User
from not_dot_net.backend.vocabularies import (
    vocabularies_config, VocabulariesConfig, StoredVocabulary, VocabularyTerm)


def _make_vocab(codes: list[str]) -> StoredVocabulary:
    return StoredVocabulary(
        key="test",
        label={"en": "Test"},
        terms=[VocabularyTerm(code=c, labels={"en": c}) for c in codes],
    )


def test_move_term_up():
    from not_dot_net.frontend.vocabularies_editor import _move
    voc = _make_vocab(["A", "B", "C"])
    _move(voc, 1, -1)
    assert [t.code for t in voc.terms] == ["B", "A", "C"]


def test_move_term_down():
    from not_dot_net.frontend.vocabularies_editor import _move
    voc = _make_vocab(["A", "B", "C"])
    _move(voc, 1, +1)
    assert [t.code for t in voc.terms] == ["A", "C", "B"]


def test_move_term_up_no_op_at_first():
    from not_dot_net.frontend.vocabularies_editor import _move
    voc = _make_vocab(["A", "B", "C"])
    _move(voc, 0, -1)
    assert [t.code for t in voc.terms] == ["A", "B", "C"]


def test_move_term_down_no_op_at_last():
    from not_dot_net.frontend.vocabularies_editor import _move
    voc = _make_vocab(["A", "B", "C"])
    _move(voc, 2, +1)
    assert [t.code for t in voc.terms] == ["A", "B", "C"]


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


@pytest.fixture
async def admin_user():
    """Minimal user object with manage_settings permission."""
    from types import SimpleNamespace
    return SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        email="admin@test",
        is_superuser=True,
        is_active=True,
        role="admin",
    )
