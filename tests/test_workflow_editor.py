"""Tests for the workflow form editor dialog."""

import pytest
from nicegui import ui
from nicegui.testing import User

from not_dot_net.backend.workflow_service import workflows_config, WorkflowsConfig
from not_dot_net.config import WorkflowConfig, WorkflowStepConfig


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


async def test_open_dialog_clones_current_config(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "demo": WorkflowConfig(label="Demo", steps=[
            WorkflowStepConfig(key="s1", type="form"),
        ]),
    }))

    captured = {}

    @ui.page("/_we1")
    async def _page():
        dlg = await WorkflowEditorDialog.create(admin_user)
        captured["dlg"] = dlg

    await user.open("/_we1")
    dlg = captured["dlg"]
    assert "demo" in dlg.working_copy.workflows
    assert dlg.working_copy.workflows["demo"].steps[0].key == "s1"
    # Mutating the working copy must not touch the persisted config
    dlg.working_copy.workflows["demo"].label = "Mutated"
    persisted = await workflows_config.get()
    assert persisted.workflows["demo"].label == "Demo"


async def test_save_persists_working_copy(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "demo": WorkflowConfig(label="Demo", steps=[]),
    }))

    captured = {}

    @ui.page("/_we2")
    async def _page():
        dlg = await WorkflowEditorDialog.create(admin_user)
        captured["dlg"] = dlg

    await user.open("/_we2")
    dlg = captured["dlg"]
    dlg.working_copy.workflows["demo"].label = "Renamed"
    await dlg.save()

    persisted = await workflows_config.get()
    assert persisted.workflows["demo"].label == "Renamed"


async def test_add_workflow(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={}))

    captured = {}

    @ui.page("/_tree1")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_tree1")
    dlg = captured["dlg"]
    dlg.add_workflow("new_wf")
    assert "new_wf" in dlg.working_copy.workflows
    assert dlg.working_copy.workflows["new_wf"].label == "new_wf"
    assert dlg.selected_workflow == "new_wf"


async def test_add_workflow_rejects_duplicate_key(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={"a": WorkflowConfig(label="A", steps=[])}))

    captured = {}

    @ui.page("/_tree2")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_tree2")
    dlg = captured["dlg"]
    with pytest.raises(ValueError):
        dlg.add_workflow("a")


async def test_add_workflow_rejects_invalid_slug(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={}))

    captured = {}

    @ui.page("/_tree3")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_tree3")
    dlg = captured["dlg"]
    with pytest.raises(ValueError):
        dlg.add_workflow("Has Spaces")


async def test_delete_workflow(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "a": WorkflowConfig(label="A", steps=[]),
        "b": WorkflowConfig(label="B", steps=[]),
    }))

    captured = {}

    @ui.page("/_tree4")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_tree4")
    dlg = captured["dlg"]
    dlg.delete_workflow("a")
    assert "a" not in dlg.working_copy.workflows
    assert dlg.selected_workflow == "b"


async def test_duplicate_workflow_deep_copies_steps(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "src": WorkflowConfig(label="Source", steps=[
            WorkflowStepConfig(key="s1", type="form"),
        ]),
    }))

    captured = {}

    @ui.page("/_tree5")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_tree5")
    dlg = captured["dlg"]
    dlg.duplicate_workflow("src", "copy")
    assert "copy" in dlg.working_copy.workflows
    assert dlg.working_copy.workflows["copy"].steps[0].key == "s1"
    # Mutating copy must not touch source
    dlg.working_copy.workflows["copy"].steps[0].key = "renamed"
    assert dlg.working_copy.workflows["src"].steps[0].key == "s1"


async def test_add_step(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "a": WorkflowConfig(label="A", steps=[]),
    }))

    captured = {}

    @ui.page("/_tree6")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_tree6")
    dlg = captured["dlg"]
    dlg.add_step("a", "step1")
    assert dlg.working_copy.workflows["a"].steps[0].key == "step1"
    assert dlg.working_copy.workflows["a"].steps[0].type == "form"


async def test_add_step_rejects_duplicate_within_workflow(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "a": WorkflowConfig(label="A", steps=[WorkflowStepConfig(key="x", type="form")]),
    }))

    captured = {}

    @ui.page("/_tree7")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_tree7")
    dlg = captured["dlg"]
    with pytest.raises(ValueError):
        dlg.add_step("a", "x")


async def test_delete_step(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "a": WorkflowConfig(label="A", steps=[
            WorkflowStepConfig(key="x", type="form"),
            WorkflowStepConfig(key="y", type="form"),
        ]),
    }))

    captured = {}

    @ui.page("/_tree8")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_tree8")
    dlg = captured["dlg"]
    dlg.delete_step("a", "x")
    keys = [s.key for s in dlg.working_copy.workflows["a"].steps]
    assert keys == ["y"]
