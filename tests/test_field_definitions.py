from not_dot_net.config import FieldConfig, FieldRef, WorkflowStepConfig, resolve_field_ref
from not_dot_net.backend.field_definitions import FieldDefinition


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
