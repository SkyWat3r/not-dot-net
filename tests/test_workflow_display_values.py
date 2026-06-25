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
