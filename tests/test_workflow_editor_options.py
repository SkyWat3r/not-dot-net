"""Tests for the workflow editor option builders + slugifier."""

from not_dot_net.backend.permissions import PermissionInfo
from not_dot_net.backend.roles import RoleDefinition
from not_dot_net.frontend.workflow_editor_options import (
    _slugify,
    assignee_options,
    event_options,
    recipient_options,
)


_ROLES = {
    "admin": RoleDefinition(label="Administrator", permissions=[]),
    "staff": RoleDefinition(label="Staff", permissions=[]),
}
_PERMS = {
    "approve_workflows": PermissionInfo("approve_workflows", "Approve workflows"),
    "manage_users": PermissionInfo("manage_users", "Manage users"),
}


def test_assignee_options_includes_four_kinds():
    opts = assignee_options(_ROLES, _PERMS)
    kinds = {o["kind"] for o in opts}
    assert kinds == {"role", "permission", "contextual_requester", "contextual_target_person"}


def test_assignee_options_role_value_format():
    opts = assignee_options(_ROLES, _PERMS)
    role_opts = [o for o in opts if o["kind"] == "role"]
    values = sorted(o["value"] for o in role_opts)
    assert values == ["role:admin", "role:staff"]
    labels = [o["label"] for o in role_opts]
    assert all(l.startswith("Anyone with role: ") for l in labels)


def test_assignee_options_contextual_singletons():
    opts = assignee_options(_ROLES, _PERMS)
    contextual_values = {o["value"] for o in opts if o["kind"].startswith("contextual_")}
    assert contextual_values == {"contextual:requester", "contextual:target_person"}


def test_recipient_options_three_groups():
    opts = recipient_options(_ROLES, _PERMS)
    groups = {o["group"] for o in opts}
    assert groups == {
        "People in this request",
        "Roles",
        "Permissions",
    }


def test_recipient_options_value_format():
    opts = recipient_options(_ROLES, _PERMS)
    by_value = {o["value"]: o for o in opts}
    assert "requester" in by_value
    assert "target_person" in by_value
    assert "admin" in by_value
    assert "staff" in by_value
    assert "permission:approve_workflows" in by_value
    assert "permission:manage_users" in by_value


def test_event_options_six_engine_events():
    opts = event_options()
    values = [o["value"] for o in opts]
    assert values == ["submit", "approve", "reject", "request_corrections", "complete", "cancel"]
    labels = [o["label"] for o in opts]
    assert labels == [
        "When submitted",
        "When approved",
        "When rejected",
        "When changes are requested",
        "When completed",
        "When cancelled",
    ]


def test_slugify_basic():
    assert _slugify("Email Address", taken=set()) == "email_address"


def test_slugify_dedup_two():
    assert _slugify("Email", taken={"email"}) == "email_2"


def test_slugify_dedup_three():
    assert _slugify("Email", taken={"email", "email_2"}) == "email_3"


def test_slugify_empty_falls_back_to_field_n():
    assert _slugify("!!!", taken=set()) == "field_1"
    assert _slugify("", taken={"field_1"}) == "field_2"


def test_slugify_collapses_runs_of_punctuation():
    assert _slugify("First Name (legal)", taken=set()) == "first_name_legal"


def test_display_name_to_key_slugifies():
    from not_dot_net.frontend.workflow_editor_options import display_name_to_key
    assert display_name_to_key("Travel request", set()) == "travel_request"


def test_display_name_to_key_prefixes_when_slug_starts_with_digit():
    from not_dot_net.frontend.workflow_editor_options import display_name_to_key
    key = display_name_to_key("3D printer access", set(), fallback_prefix="workflow")
    assert key == "workflow_3d_printer_access"


def test_display_name_to_key_dedups_against_taken():
    from not_dot_net.frontend.workflow_editor_options import display_name_to_key
    assert display_name_to_key("Travel request", {"travel_request"}) == "travel_request_2"


def test_action_options_cover_engine_actions_and_preserve_unknown():
    from not_dot_net.frontend.workflow_editor_options import action_options
    opts = action_options(["submit", "legacy_sign_off"])
    values = [o["value"] for o in opts]
    assert values[:5] == ["submit", "approve", "complete", "request_corrections", "reject"]
    assert "legacy_sign_off" in values
    by_value = {o["value"]: o["label"] for o in opts}
    assert "legacy_sign_off" in by_value["legacy_sign_off"]


def test_assignee_summary_precedence_matches_engine():
    from types import SimpleNamespace
    from not_dot_net.backend.roles import RoleDefinition
    from not_dot_net.frontend.workflow_editor_options import assignee_summary

    roles = {"hr": RoleDefinition(label="HR team", permissions=[])}
    perms = {}

    step = SimpleNamespace(assignee="target_person", assignee_permission="x", assignee_role="hr")
    contextual = assignee_summary(step, roles, perms)
    assert "HR team" not in contextual  # contextual wins over role/permission

    step = SimpleNamespace(assignee=None, assignee_permission=None, assignee_role="hr")
    assert "HR team" in assignee_summary(step, roles, perms)

    step = SimpleNamespace(assignee=None, assignee_permission=None, assignee_role=None)
    assert assignee_summary(step, roles, perms)  # non-empty "nobody yet" text
