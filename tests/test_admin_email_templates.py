import pytest
from not_dot_net.backend.email_templates import (
    mail_templates_config, MailTemplatesConfig, EmailTemplate, render_email,
    DEFAULT_TEMPLATES, _render, DEFAULT_LAYOUT, EMAIL_EVENTS,
)


@pytest.mark.asyncio
async def test_saving_override_changes_render_output():
    cfg = await mail_templates_config.get()
    cfg.overrides["approve"] = EmailTemplate(subject="Bonjour", body="<p>Approuvé</p>")
    await mail_templates_config.set(cfg)
    subject, body = await render_email("approve", {
        "app_name": "LPP", "app_url": "http://x/", "recipient_name": "A",
        "workflow_label": "VPN", "request_url": "http://x/r/1"})
    assert subject == "Bonjour"
    assert "Approuvé" in body


@pytest.mark.asyncio
async def test_reset_drops_override():
    await mail_templates_config.set(MailTemplatesConfig(
        overrides={"approve": EmailTemplate(subject="x", body="y")}))
    cfg = await mail_templates_config.get()
    cfg.overrides.pop("approve", None)
    await mail_templates_config.set(cfg)
    subject, _ = await render_email("approve", {
        "app_name": "LPP", "app_url": "http://x/", "recipient_name": "A",
        "workflow_label": "VPN", "request_url": "http://x/r/1"})
    assert subject == DEFAULT_TEMPLATES["approve"].subject.replace(
        "{{ workflow_label }}", "VPN")


def test_preview_renders_event_sample_without_error():
    # The editor preview path: render a template against its event sample.
    for ev in EMAIL_EVENTS:
        ctx = {"app_name": "LPP", "app_url": "http://x/", "recipient_name": "Sample",
               **ev.sample}
        subject, body = _render(DEFAULT_TEMPLATES[ev.key], DEFAULT_LAYOUT, ctx)
        assert subject and body
