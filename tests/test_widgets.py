"""Tests for reusable settings widgets."""

import pytest
from nicegui import ui
from nicegui.testing import User

from not_dot_net.frontend.widgets import chip_list_editor, keyed_chip_editor
from not_dot_net.backend.vocabularies import (
    vocabularies_config, VocabulariesConfig, StoredVocabulary, VocabularyTerm,
)


async def test_chip_list_editor_initial_value(user: User):
    """Without suggestions the editor must be a free-form tags input.

    A QSelect with empty options renders selected values as nothing in the
    browser — the settings page showed blank fields for teams/employers/
    transport_modes despite correct server-side values.
    """
    @ui.page("/_w1")
    def _page():
        chip_list_editor(["a", "b", "c"])
    await user.open("/_w1")
    chips = user.find(kind=ui.input_chips).elements.pop()
    assert list(chips.value) == ["a", "b", "c"]


async def test_chip_list_editor_suggestions_keep_current_values_visible(user: User):
    """QSelect only renders chips for values present in options — current
    values outside the suggestion list must be merged into options."""
    @ui.page("/_w1b")
    def _page():
        chip_list_editor(["legacy"], suggestions=["a", "b"])
    await user.open("/_w1b")
    select = user.find(kind=ui.select).elements.pop()
    assert "legacy" in select.options
    assert list(select.value) == ["legacy"]


async def test_chip_list_editor_returns_list_type(user: User):
    @ui.page("/_w2")
    def _page():
        w = chip_list_editor([])
        assert isinstance(w.value, list)
    await user.open("/_w2")


async def test_chip_list_editor_writes_back_list(user: User):
    captured = {}

    @ui.page("/_w3")
    def _page():
        w = chip_list_editor(["x"])
        captured["w"] = w
    await user.open("/_w3")
    captured["w"].value = ["x", "y"]
    assert captured["w"].value == ["x", "y"]


async def test_keyed_chip_editor_initial_value(user: User):
    captured = {}

    @ui.page("/_k1")
    def _page():
        captured["w"] = keyed_chip_editor({"Linux": ["bash"], "Windows": ["powershell"]})
    await user.open("/_k1")
    assert captured["w"].value == {"Linux": ["bash"], "Windows": ["powershell"]}


async def test_keyed_chip_editor_add_remove_key(user: User):
    captured = {}

    @ui.page("/_k2")
    def _page():
        captured["w"] = keyed_chip_editor({"a": ["1"]})
    await user.open("/_k2")
    captured["w"].add_key("b", ["2"])
    assert captured["w"].value == {"a": ["1"], "b": ["2"]}
    captured["w"].remove_key("a")
    assert captured["w"].value == {"b": ["2"]}


async def test_keyed_chip_editor_supports_tooltip(user: User):
    """admin_settings._render_form calls widget.tooltip(hint) for any field
    with a Pydantic description — KeyedChipEditor must implement it or fields
    typed dict[str, list[str]] with a description crash the settings page.
    """
    captured = {}

    @ui.page("/_kt")
    def _page():
        w = keyed_chip_editor({})
        w.tooltip("hint text")
        captured["w"] = w

    await user.open("/_kt")
    assert captured["w"] is not None


async def test_keyed_chip_editor_nested_change_propagates(user: User):
    captured = {}

    @ui.page("/_k3")
    def _page():
        captured["w"] = keyed_chip_editor({"k": ["x"]})
    await user.open("/_k3")
    captured["w"].set_values("k", ["x", "y"])
    assert captured["w"].value == {"k": ["x", "y"]}


async def test_select_field_renders_vocabulary_labels(user: User):
    await vocabularies_config.set(VocabulariesConfig(vocabularies={
        "nationalities_demo": StoredVocabulary(
            key="nationalities_demo", label={"en": "Nat"},
            terms=[VocabularyTerm(code="FR", labels={"en": "French", "fr": "Français"})],
        )
    }))
    from not_dot_net.frontend.workflow_step import _render_field
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
