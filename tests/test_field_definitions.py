import logging

from not_dot_net.config import FieldConfig, FieldRef, WorkflowStepConfig, resolve_field_ref
from not_dot_net.backend.field_definitions import (
    FieldDefinition, FieldDefinitionsConfig, resolve_step_fields,
)


def test_resolve_inherits_unset_properties():
    defn = FieldDefinition(key="phone", type="phone", label="Phone",
                           required=True, half_width=True)
    resolved = resolve_field_ref(FieldRef(ref="phone"), defn)
    assert resolved.name == "phone"
    assert resolved.type == "phone"
    assert resolved.label == "Phone"
    assert resolved.required is True
    assert resolved.half_width is True
    assert resolved.visible_when is None


def test_resolve_override_to_false_beats_inherit():
    defn = FieldDefinition(key="phone", type="phone", required=True)
    resolved = resolve_field_ref(FieldRef(ref="phone", required=False), defn)
    assert resolved.required is False


def test_resolve_name_not_overridable_and_visible_when_is_local():
    defn = FieldDefinition(key="phone", type="phone")
    ref = FieldRef(ref="phone", visible_when={"needs": True})
    resolved = resolve_field_ref(ref, defn)
    assert resolved.name == "phone"
    assert resolved.visible_when == {"needs": True}


def test_step_fields_union_deserializes_both_shapes():
    step = WorkflowStepConfig.model_validate({
        "key": "s1", "type": "form",
        "fields": [
            {"name": "note", "type": "text"},
            {"ref": "phone", "required": True},
        ],
    })
    assert isinstance(step.fields[0], FieldConfig)
    assert isinstance(step.fields[1], FieldRef)
    assert step.fields[1].ref == "phone"
    assert step.fields[1].required is True


async def test_resolve_step_fields_mixes_inline_and_refs_in_order():
    cfg = FieldDefinitionsConfig(definitions={
        "phone": FieldDefinition(key="phone", type="phone", label="Phone"),
    })
    step = WorkflowStepConfig(key="s", type="form", fields=[
        FieldConfig(name="note", type="text"),
        FieldRef(ref="phone", required=True),
    ])
    resolved = await resolve_step_fields(step, cfg=cfg)
    assert [f.name for f in resolved] == ["note", "phone"]
    assert resolved[1].type == "phone"
    assert resolved[1].required is True


async def test_resolve_step_fields_drops_dangling_ref(caplog):
    cfg = FieldDefinitionsConfig(definitions={})
    step = WorkflowStepConfig(key="s", type="form", fields=[
        FieldConfig(name="note", type="text"),
        FieldRef(ref="gone"),
    ])
    with caplog.at_level(logging.WARNING):
        resolved = await resolve_step_fields(step, cfg=cfg)
    assert [f.name for f in resolved] == ["note"]
    assert "gone" in caplog.text
