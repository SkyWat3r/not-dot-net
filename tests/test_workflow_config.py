import pytest
from not_dot_net.config import WorkflowStepConfig, WorkflowConfig
from not_dot_net.backend.workflow_service import workflows_config


async def test_settings_has_workflows():
    cfg = await workflows_config.get()
    assert hasattr(cfg, "workflows")
    assert isinstance(cfg.workflows, dict)


async def test_default_workflows_include_onboarding_and_vpn():
    cfg = await workflows_config.get()
    assert "onboarding" in cfg.workflows
    assert "vpn_access" in cfg.workflows


async def test_workflow_config_has_required_fields():
    cfg = await workflows_config.get()
    wf = cfg.workflows["vpn_access"]
    assert wf.label == "VPN Access Request"
    assert wf.start_role == "staff"
    assert wf.target_email_field == "target_email"
    assert len(wf.steps) >= 2


async def test_step_config_fields():
    cfg = await workflows_config.get()
    step = cfg.workflows["vpn_access"].steps[0]
    assert step.key == "request"
    assert step.type == "form"
    assert step.assignee_role == "staff"
    assert len(step.fields) >= 2
    assert "submit" in step.actions


async def test_step_field_config():
    cfg = await workflows_config.get()
    field = cfg.workflows["vpn_access"].steps[0].fields[0]
    assert field.name == "target_name"
    assert field.type == "text"
    assert field.required is True


async def test_notification_config():
    cfg = await workflows_config.get()
    notifs = cfg.workflows["vpn_access"].notifications
    assert len(notifs) >= 1
    assert notifs[0].event == "submit"
    assert "director" in notifs[0].notify


async def test_onboarding_has_partial_save_step():
    cfg = await workflows_config.get()
    newcomer_step = cfg.workflows["onboarding"].steps[1]
    assert newcomer_step.key == "newcomer_info"
    assert newcomer_step.partial_save is True
    assert newcomer_step.assignee == "target_person"
