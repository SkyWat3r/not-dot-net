import pytest
from not_dot_net.config import Settings, WorkflowStepConfig, WorkflowConfig


def _make_settings(**kwargs):
    defaults = dict(jwt_secret="x" * 34, storage_secret="x" * 34)
    defaults.update(kwargs)
    return Settings(**defaults)


def test_settings_has_workflows():
    s = _make_settings()
    assert hasattr(s, "workflows")
    assert isinstance(s.workflows, dict)


def test_default_workflows_include_onboarding_and_vpn():
    s = _make_settings()
    assert "onboarding" in s.workflows
    assert "vpn_access" in s.workflows


def test_workflow_config_has_required_fields():
    s = _make_settings()
    wf = s.workflows["vpn_access"]
    assert wf.label == "VPN Access Request"
    assert wf.start_role == "staff"
    assert wf.target_email_field == "target_email"
    assert len(wf.steps) >= 2


def test_step_config_fields():
    s = _make_settings()
    step = s.workflows["vpn_access"].steps[0]
    assert step.key == "request"
    assert step.type == "form"
    assert step.assignee_role == "staff"
    assert len(step.fields) >= 2
    assert "submit" in step.actions


def test_step_field_config():
    s = _make_settings()
    field = s.workflows["vpn_access"].steps[0].fields[0]
    assert field.name == "target_name"
    assert field.type == "text"
    assert field.required is True


def test_notification_config():
    s = _make_settings()
    notifs = s.workflows["vpn_access"].notifications
    assert len(notifs) >= 1
    assert notifs[0].event == "submit"
    assert "director" in notifs[0].notify


def test_settings_has_mail_config():
    s = _make_settings()
    assert hasattr(s, "mail")
    assert s.mail.dev_mode is True  # default for dev


def test_onboarding_has_partial_save_step():
    s = _make_settings()
    newcomer_step = s.workflows["onboarding"].steps[1]
    assert newcomer_step.key == "newcomer_info"
    assert newcomer_step.partial_save is True
    assert newcomer_step.assignee == "target_person"
