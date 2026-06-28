"""Settings editor for configurable email templates."""

from nicegui import ui

from not_dot_net.backend.permissions import check_permission
from not_dot_net.backend.email_templates import (
    EMAIL_EVENTS, DEFAULT_TEMPLATES, DEFAULT_LAYOUT, EmailTemplate,
    MailTemplatesConfig, mail_templates_config, _render, COMMON_VARIABLES,
)
from not_dot_net.frontend.i18n import t


async def render(user) -> None:
    await check_permission(user, "manage_settings")
    cfg = await mail_templates_config.get()

    groups: dict[str, list] = {}
    for ev in EMAIL_EVENTS:
        groups.setdefault(ev.group, []).append(ev)

    ui.label(t("email_templates_help")).classes("text-sm text-grey mb-2")

    with ui.expansion(t("email_layout"), icon="dashboard_customize").classes("w-full"):
        _layout_editor(cfg)

    for group, events in groups.items():
        ui.label(group).classes("text-weight-bold mt-2")
        for ev in events:
            with ui.expansion(ev.label, icon="mail").classes("w-full"):
                _event_editor(cfg, ev)


def _current(cfg: MailTemplatesConfig, key: str) -> EmailTemplate:
    return cfg.overrides.get(key) or DEFAULT_TEMPLATES[key]


def _event_editor(cfg: MailTemplatesConfig, ev) -> None:
    tmpl = _current(cfg, ev.key)
    subject = ui.input(t("subject"), value=tmpl.subject).props("stack-label").classes("w-full")
    body = ui.codemirror(value=tmpl.body, language="html").classes("w-full grow") \
        .style("min-height:0")
    variables = COMMON_VARIABLES + [v for v in ev.variables if v not in COMMON_VARIABLES]
    ui.label(t("available_variables") + ": " + ", ".join(f"{{{{ {v} }}}}" for v in variables)) \
        .classes("text-xs text-grey")
    preview = ui.html().classes("w-full border q-pa-sm")

    def do_preview():
        ctx = {"app_name": "LPP Intranet", "app_url": "#", "recipient_name": "Sample",
               **ev.sample}
        try:
            subj, html = _render(EmailTemplate(subject=subject.value, body=body.value),
                                 cfg.layout or DEFAULT_LAYOUT, ctx)
            preview.set_content(f"<div class='text-weight-bold q-mb-sm'>{subj}</div>{html}")
        except Exception as exc:  # surface template errors inline
            preview.set_content(f"<pre class='text-negative'>{exc}</pre>")

    async def save():
        cfg.overrides[ev.key] = EmailTemplate(subject=subject.value, body=body.value)
        await mail_templates_config.set(cfg)
        ui.notify(t("saved"), type="positive")

    async def reset():
        cfg.overrides.pop(ev.key, None)
        await mail_templates_config.set(cfg)
        d = DEFAULT_TEMPLATES[ev.key]
        subject.value = d.subject
        body.value = d.body
        ui.notify(t("reset_done"), type="info")

    with ui.row():
        ui.button(t("preview"), on_click=do_preview).props("flat")
        ui.button(t("save"), on_click=save)
        ui.button(t("reset_to_default"), on_click=reset).props("flat color=negative")


def _layout_editor(cfg: MailTemplatesConfig) -> None:
    ui.label(t("email_layout_help")).classes("text-xs text-grey")
    layout = ui.codemirror(value=cfg.layout or DEFAULT_LAYOUT, language="html") \
        .classes("w-full grow").style("min-height:0")
    preview = ui.html().classes("w-full border q-pa-sm")

    def do_preview():
        ctx = {"app_name": "LPP Intranet", "app_url": "#", "recipient_name": "Sample"}
        sample = EmailTemplate(
            subject="(layout preview)",
            body="<p>Hello {{ recipient_name }}, this is a sample message body.</p>",
        )
        try:
            _, html = _render(sample, layout.value, ctx)
            preview.set_content(html)
        except Exception as exc:  # surface template errors inline
            preview.set_content(f"<pre class='text-negative'>{exc}</pre>")

    async def save():
        cfg.layout = layout.value
        await mail_templates_config.set(cfg)
        ui.notify(t("saved"), type="positive")

    async def reset():
        cfg.layout = DEFAULT_LAYOUT
        await mail_templates_config.set(cfg)
        layout.value = DEFAULT_LAYOUT
        ui.notify(t("reset_done"), type="info")

    with ui.row():
        ui.button(t("preview"), on_click=do_preview).props("flat")
        ui.button(t("save"), on_click=save)
        ui.button(t("reset_to_default"), on_click=reset).props("flat color=negative")
