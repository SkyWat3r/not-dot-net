from not_dot_net.backend.email_templates import (
    EmailTemplate, DEFAULT_LAYOUT, DEFAULT_TEMPLATES, EMAIL_EVENTS, _render,
)


def test_render_interpolates_and_wraps_in_layout():
    tmpl = EmailTemplate(subject="Hi {{ recipient_name }}", body="<p>{{ workflow_label }}</p>")
    ctx = {"app_name": "LPP", "app_url": "http://x/", "recipient_name": "Alex",
           "workflow_label": "VPN"}
    subject, body = _render(tmpl, DEFAULT_LAYOUT, ctx)
    assert subject == "Hi Alex"
    assert "<p>VPN</p>" in body          # body fragment present
    assert "LPP" in body                 # layout (header/footer) rendered around it


def test_render_autoescapes_variable_values_not_template_html():
    tmpl = EmailTemplate(subject="s", body="<p>Hello {{ recipient_name }}</p>")
    ctx = {"app_name": "LPP", "app_url": "http://x/",
           "recipient_name": "<script>alert(1)</script>", "workflow_label": "w"}
    _, body = _render(tmpl, DEFAULT_LAYOUT, ctx)
    assert "<script>alert(1)</script>" not in body            # value escaped
    assert "&lt;script&gt;" in body
    assert "<p>Hello" in body                                 # literal HTML preserved


def test_render_does_not_double_escape_body_into_layout():
    tmpl = EmailTemplate(subject="s", body="<a href='{{ app_url }}'>open</a>")
    ctx = {"app_name": "LPP", "app_url": "http://x/", "recipient_name": "A", "workflow_label": "w"}
    _, body = _render(tmpl, DEFAULT_LAYOUT, ctx)
    assert "<a href=" in body and "&lt;a" not in body         # body not escaped by layout


def test_every_event_has_a_default_template():
    for ev in EMAIL_EVENTS:
        assert ev.key in DEFAULT_TEMPLATES, f"missing default for {ev.key}"


def test_default_templates_render_with_their_sample_context():
    for ev in EMAIL_EVENTS:
        ctx = {"app_name": "LPP", "app_url": "http://x/", "recipient_name": "Sample",
               **ev.sample}
        subject, body = _render(DEFAULT_TEMPLATES[ev.key], DEFAULT_LAYOUT, ctx)
        assert subject.strip()
        assert body.strip()
