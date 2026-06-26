"""Integration tests for FieldRef resolution in render/validate/display paths."""

from types import SimpleNamespace

import pytest
from nicegui import ui
from nicegui.testing import User

from not_dot_net.backend.field_definitions import (
    FieldDefinition,
    FieldDefinitionsConfig,
    field_definitions_config,
    save_field_definition,
)
from not_dot_net.backend.vocabularies import (
    VocabulariesConfig,
    StoredVocabulary,
    VocabularyTerm,
    vocabularies_config,
)
from not_dot_net.backend.workflow_service import WorkflowsConfig, workflows_config
from not_dot_net.config import FieldConfig, FieldRef, WorkflowConfig, WorkflowStepConfig
from not_dot_net.frontend.i18n import t
from not_dot_net.frontend.new_request import render
from not_dot_net.frontend.workflow_step import resolve_display_values


@pytest.fixture
def superuser():
    return SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        email="admin@test",
        is_superuser=True,
        is_active=True,
        role="",
    )


async def test_referenced_field_renders_with_definition_label(user: User, superuser):
    """A FieldRef in a step renders with the definition's label."""
    await field_definitions_config.set(FieldDefinitionsConfig(definitions={
        "phone": FieldDefinition(key="phone", type="phone", label="Phone number"),
    }))
    await workflows_config.set(WorkflowsConfig(workflows={
        "mission": WorkflowConfig(label="Mission", steps=[
            WorkflowStepConfig(key="info", type="form", assignee="requester", fields=[
                FieldRef(ref="phone"),
            ]),
        ]),
    }))

    @ui.page("/_fd_label_test")
    async def _page():
        await render(superuser)

    await user.open("/_fd_label_test")
    user.find(kind=ui.card).click()
    await user.should_see("Phone number")


async def test_referenced_field_required_prevents_empty_submit(user: User, superuser):
    """A FieldRef(required=True) makes the rendered field required — submitting empty shows notice."""
    await field_definitions_config.set(FieldDefinitionsConfig(definitions={
        "contact": FieldDefinition(key="contact", type="text", label="Contact"),
    }))
    await workflows_config.set(WorkflowsConfig(workflows={
        "req_test": WorkflowConfig(label="Req Test", steps=[
            WorkflowStepConfig(key="info", type="form", assignee="requester", fields=[
                FieldRef(ref="contact", required=True),
            ]),
        ]),
    }))

    @ui.page("/_fd_required_test")
    async def _page():
        await render(superuser)

    await user.open("/_fd_required_test")
    user.find(kind=ui.card).click()
    await user.should_see("Contact")
    # Submit without filling in — must see required-field notice
    user.find(t("submit")).click()
    await user.should_see(t("required_field"))


async def test_edit_definition_label_applies_everywhere(user: User, superuser):
    """Editing the definition label and re-rendering shows the new label."""
    await save_field_definition(FieldDefinition(key="job", type="text", label="Job title"))
    await workflows_config.set(WorkflowsConfig(workflows={
        "job_wf": WorkflowConfig(label="Job WF", steps=[
            WorkflowStepConfig(key="info", type="form", assignee="requester", fields=[
                FieldRef(ref="job"),
            ]),
        ]),
    }))

    @ui.page("/_fd_edit_label_before")
    async def _page_before():
        await render(superuser)

    await user.open("/_fd_edit_label_before")
    user.find(kind=ui.card).click()
    await user.should_see("Job title")

    # Now update the definition
    await save_field_definition(FieldDefinition(key="job", type="text", label="Position"))

    @ui.page("/_fd_edit_label_after")
    async def _page_after():
        await render(superuser)

    await user.open("/_fd_edit_label_after")
    user.find(kind=ui.card).click()
    await user.should_see("Position")


async def test_resolve_display_values_maps_referenced_select_code_to_label():
    """resolve_display_values resolves a select code from a FieldRef to its vocabulary label."""
    await vocabularies_config.set(VocabulariesConfig(vocabularies={
        "nat": StoredVocabulary(key="nat", label={"en": "Nat"}, terms=[
            VocabularyTerm(code="FR", labels={"en": "French", "fr": "Français"}),
        ]),
    }))
    await field_definitions_config.set(FieldDefinitionsConfig(definitions={
        "nationality": FieldDefinition(key="nationality", type="select", label="Nationality", options_key="nat"),
    }))
    wf = WorkflowConfig(label="WF", steps=[
        WorkflowStepConfig(key="s", type="form", fields=[
            FieldRef(ref="nationality"),
        ]),
    ])
    out = await resolve_display_values(wf, {"nationality": "FR"}, "fr")
    assert out["nationality"] == "Français"
