"""Pure-predicate tests for FieldConfig.visible_when — no NiceGUI."""

from not_dot_net.config import FieldConfig, is_field_visible


def test_no_rule_means_always_visible():
    f = FieldConfig(name="x", type="text")
    assert is_field_visible(f, {}) is True
    assert is_field_visible(f, {"anything": True}) is True


def test_rule_matches_value():
    f = FieldConfig(name="zrr_topic", type="text", visible_when={"zrr": True})
    assert is_field_visible(f, {"zrr": True}) is True
    assert is_field_visible(f, {"zrr": False}) is False
    assert is_field_visible(f, {}) is False  # missing key counts as mismatch


def test_rule_with_string_value():
    f = FieldConfig(name="cdd_end", type="date", visible_when={"status": "CDD"})
    assert is_field_visible(f, {"status": "CDD"}) is True
    assert is_field_visible(f, {"status": "CDI"}) is False


def test_rule_with_multiple_keys_is_and():
    """Future-proof: if more than one key/value pair, all must match."""
    f = FieldConfig(name="x", type="text", visible_when={"a": True, "b": "y"})
    assert is_field_visible(f, {"a": True, "b": "y"}) is True
    assert is_field_visible(f, {"a": True, "b": "z"}) is False
    assert is_field_visible(f, {"a": False, "b": "y"}) is False
