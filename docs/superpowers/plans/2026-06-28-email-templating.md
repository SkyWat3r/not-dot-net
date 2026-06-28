# Email Templating Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the thin, link-less workflow/booking emails with configurable osTicket-style templates — rich Jinja defaults, admin-editable in Settings, a shared base layout, and deep links into the relevant action.

**Architecture:** A new `backend/email_templates.py` owns the data model, code-level default templates + layout, a declarative event registry, and a sandboxed Jinja renderer. Admin overrides live in a `mail_templates` ConfigSection (merge-on-read over defaults). `notifications.py`, `workflow_service.py`, and `booking_service.py` build a context dict and call one async `render_email(key, ctx)`. A Settings editor (`frontend/admin_email_templates.py`) exposes per-event subject/body + base layout with live preview and reset.

**Tech Stack:** Python 3.10+, Pydantic v2, Jinja2 3.1.6 (`SandboxedEnvironment`, already a transitive dep), `markupsafe.Markup`, NiceGUI (CodeMirror), SQLAlchemy async, pytest + `nicegui.testing.User`.

## Global Constraints

- **No new dependency.** Jinja2 3.1.6 and markupsafe are already available transitively — do not add to `pyproject.toml`.
- **No Alembic migration.** `mail_templates` is a new ConfigSection row, created lazily on first `set()`, like every other section.
- **`send_mail(to, subject, body_html)` is unchanged** — it stays the single enqueue entry point; the outbox pipeline is untouched.
- **Single-language templates.** No EN/FR variants for template *content*. Editor UI chrome (labels/buttons) does get EN+FR i18n keys.
- **Autoescape on.** All rendering uses `SandboxedEnvironment(autoescape=True)`; the admin's literal HTML passes through, variable *values* are escaped. The rendered body is wrapped into the layout as `Markup` (`{{ content }}`) so it is not double-escaped.
- **Deep links are computed in code**, never typed by admins. Targets: `request_url={base}/workflow/request/{id}`, `token_url={base}/workflow/token/{token}`, `bookings_url={base}/?tab=bookings`, `app_url={base}/`. `{base}` = `org_config.base_url.rstrip("/")`.
- **Common context vars** present in every template + the layout: `app_name`, `app_url`, `recipient_name`.
- **Editor gated on `manage_settings`** with a callback-level `check_permission` guard (catch `PermissionError`), mirroring `frontend/vocabularies_editor.py`.
- **Admin Jinja errors must never crash a workflow transition or the outbox** — `render_email` catches `jinja2.TemplateError` and falls back to the code default for that event (logged WARNING).

---

## File Structure

| File | Responsibility | Created/Modified |
|------|----------------|------------------|
| `not_dot_net/backend/email_templates.py` | model, defaults, layout, event registry, `_render`, `get_template`, `render_email` | Create |
| `not_dot_net/backend/notifications.py` | build workflow context + send; drop `TEMPLATES`/old `render_email` | Modify |
| `not_dot_net/backend/workflow_service.py` | repoint `_send_token_link` + `account_created` send | Modify |
| `not_dot_net/backend/booking_service.py` | context-builders + `render_email`; subjects into templates | Modify |
| `not_dot_net/frontend/shell.py` | `?tab=` deep-link support | Modify |
| `not_dot_net/frontend/admin_email_templates.py` | editor UI | Create |
| `not_dot_net/frontend/admin_settings.py` | mount editor expansion | Modify |
| `not_dot_net/frontend/i18n.py` | EN+FR keys for editor chrome | Modify |
| `tests/test_email_templates.py` | renderer + merge + fallback + per-event links | Create |
| `tests/test_notifications.py`, `tests/test_workflow_notifications_integration.py`, `tests/test_booking_reminders.py` | update body/link assertions | Modify |
| `tests/test_shell_tab_deeplink.py` | `?tab=` selection | Create |
| `tests/test_admin_email_templates.py` | save/reset/preview | Create |

---

## Task 1: Core module — model, defaults, registry, pure renderer

**Files:**
- Create: `not_dot_net/backend/email_templates.py`
- Test: `tests/test_email_templates.py`

**Interfaces:**
- Produces:
  - `class EmailTemplate(BaseModel)` with `subject: str`, `body: str`
  - `DEFAULT_LAYOUT: str`, `DEFAULT_TEMPLATES: dict[str, EmailTemplate]`
  - `@dataclass(frozen=True) class EmailEvent` with `key: str`, `group: str`, `label: str`, `variables: list[str]`, `sample: dict`
  - `EMAIL_EVENTS: list[EmailEvent]`
  - `COMMON_VARIABLES: list[str]`
  - `def _render(template: EmailTemplate, layout: str, context: dict) -> tuple[str, str]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_email_templates.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_email_templates.py -v`
Expected: FAIL — `ModuleNotFoundError: not_dot_net.backend.email_templates`.

- [ ] **Step 3: Write minimal implementation**

```python
# not_dot_net/backend/email_templates.py
"""Configurable email templates: code-level defaults + DB overrides, rendered
with a sandboxed Jinja environment. `render_email(key, context)` is the single
entry point used by notifications, workflow and booking send sites."""

import logging
from dataclasses import dataclass

from jinja2 import TemplateError
from jinja2.sandbox import SandboxedEnvironment
from markupsafe import Markup
from pydantic import BaseModel, Field

from not_dot_net.backend.app_config import section

logger = logging.getLogger("not_dot_net.email_templates")

COMMON_VARIABLES = ["app_name", "app_url", "recipient_name"]


class EmailTemplate(BaseModel):
    subject: str
    body: str


@dataclass(frozen=True)
class EmailEvent:
    key: str
    group: str
    label: str
    variables: list[str]
    sample: dict


DEFAULT_LAYOUT = """\
<div style="font-family:Arial,Helvetica,sans-serif;max-width:560px;margin:auto;
            color:#222;font-size:14px;line-height:1.5">
  <div style="background:#0F52AC;color:#fff;padding:12px 16px;font-size:16px">
    {{ app_name }}
  </div>
  <div style="padding:16px">
    {{ content }}
  </div>
  <div style="padding:12px 16px;color:#888;font-size:12px;border-top:1px solid #eee">
    <a href="{{ app_url }}" style="color:#0F52AC">{{ app_name }}</a> —
    this is an automated message, please do not reply.
  </div>
</div>"""


def _t(subject: str, body: str) -> EmailTemplate:
    return EmailTemplate(subject=subject, body=body)


DEFAULT_TEMPLATES: dict[str, EmailTemplate] = {
    "submit": _t(
        "A new {{ workflow_label }} request needs your attention",
        "<p>Hello {{ recipient_name }},</p>"
        "<p>A new <strong>{{ workflow_label }}</strong> request"
        "{% if requester_name %} from {{ requester_name }}{% endif %}"
        " has been submitted and requires your action.</p>"
        "<p><a href=\"{{ request_url }}\">Review the request</a></p>",
    ),
    "step_assigned": _t(
        "Action required: {{ step_label }} for {{ workflow_label }}",
        "<p>Hello {{ recipient_name }},</p>"
        "<p>You have a pending action on <strong>{{ workflow_label }}</strong>: "
        "{{ step_label }}.</p>"
        "<p><a href=\"{{ request_url }}\">Open the request</a></p>",
    ),
    "approve": _t(
        "Your {{ workflow_label }} request has been approved",
        "<p>Hello {{ recipient_name }},</p>"
        "<p>Your <strong>{{ workflow_label }}</strong> request has been approved.</p>"
        "<p><a href=\"{{ request_url }}\">View your request</a></p>",
    ),
    "reject": _t(
        "Your {{ workflow_label }} request was rejected",
        "<p>Hello {{ recipient_name }},</p>"
        "<p>Your <strong>{{ workflow_label }}</strong> request was rejected.</p>"
        "<p><a href=\"{{ request_url }}\">View your request</a></p>",
    ),
    "token_link": _t(
        "Please complete your information for {{ workflow_label }}",
        "<p>Hello {{ recipient_name }},</p>"
        "<p>Please complete your information for <strong>{{ workflow_label }}</strong> "
        "by visiting the link below:</p>"
        "<p><a href=\"{{ token_url }}\">{{ token_url }}</a></p>",
    ),
    "corrections_with_link": _t(
        "Corrections needed for your {{ workflow_label }} submission",
        "<p>Hello {{ recipient_name }},</p>"
        "<p>The administration team has requested corrections on your "
        "<strong>{{ workflow_label }}</strong> submission.</p>"
        "<p>Please update your information here:</p>"
        "<p><a href=\"{{ token_url }}\">{{ token_url }}</a></p>",
    ),
    "request_corrections": _t(
        "Corrections needed for your {{ workflow_label }} submission",
        "<p>Hello {{ recipient_name }},</p>"
        "<p>The administration team has requested corrections on your "
        "<strong>{{ workflow_label }}</strong> submission.</p>"
        "<p>Please visit the link you received previously to update your information.</p>",
    ),
    "complete": _t(
        "Your {{ workflow_label }} is complete — welcome!",
        "<p>Hello {{ recipient_name }},</p>"
        "<p>Your <strong>{{ workflow_label }}</strong> onboarding is now complete. "
        "Your account has been created.</p>"
        "<p><a href=\"{{ app_url }}\">Sign in to the intranet</a></p>",
    ),
    # The initial password is deliberately NOT in this email.
    "account_created": _t(
        "{{ workflow_label }}: your account is ready",
        "<p>Hello {{ display_name }},</p>"
        "<p>Your account has been created:</p>"
        "<ul>"
        "<li><strong>Login:</strong> {{ sam }}</li>"
        "<li><strong>Email:</strong> {{ mail }}</li>"
        "</ul>"
        "<p>Your administrator will give you your initial password; you will be "
        "asked to change it on first login.</p>"
        "<p><a href=\"{{ app_url }}\">Sign in to the intranet</a></p>",
    ),
    "booking_reminder": _t(
        "[{{ app_name }}] Your booking ends {{ delay_label }}",
        "<p>Hello {{ recipient_name }},</p>"
        "<p>Your booking is ending soon.</p>"
        "<table>"
        "<tr><td><strong>Resource</strong></td><td>{{ resource_name }}</td></tr>"
        "<tr><td><strong>Start date</strong></td><td>{{ start_date }}</td></tr>"
        "<tr><td><strong>End date</strong></td><td>{{ end_date }}</td></tr>"
        "<tr><td><strong>OS</strong></td><td>{{ os }}</td></tr>"
        "<tr><td><strong>Software</strong></td><td>{{ software }}</td></tr>"
        "</table>"
        "<p>Please prepare to return or release the resource at the end of your booking.</p>"
        "<p><a href=\"{{ bookings_url }}\">View your bookings</a></p>",
    ),
    "resource_status": _t(
        "[{{ app_name }}] {{ subject_line }}",
        "<p>Hello {{ recipient_name }},</p>"
        "<p>{{ headline }}</p>"
        "<table>"
        "<tr><td><strong>Resource</strong></td><td>{{ resource_name }}</td></tr>"
        "<tr><td><strong>Location</strong></td><td>{{ location }}</td></tr>"
        "</table>"
        "<p><a href=\"{{ bookings_url }}\">View your bookings</a></p>",
    ),
    "resource_out_of_service": _t(
        "[{{ app_name }}] Resource out of service: {{ resource_name }}",
        "<p>Hello {{ recipient_name }},</p>"
        "<p>{{ headline }}</p>"
        "<table>"
        "<tr><td><strong>Resource</strong></td><td>{{ resource_name }}</td></tr>"
        "<tr><td><strong>Location</strong></td><td>{{ location }}</td></tr>"
        "</table>"
        "<p><a href=\"{{ bookings_url }}\">View bookings</a></p>",
    ),
}


EMAIL_EVENTS: list[EmailEvent] = [
    EmailEvent("submit", "Workflow", "New request submitted",
               ["workflow_label", "requester_name", "request_url"],
               {"workflow_label": "VPN Access", "requester_name": "Marie Curie",
                "request_url": "http://x/workflow/request/123"}),
    EmailEvent("step_assigned", "Workflow", "Step assigned",
               ["workflow_label", "step_label", "request_url"],
               {"workflow_label": "VPN Access", "step_label": "Manager approval",
                "request_url": "http://x/workflow/request/123"}),
    EmailEvent("approve", "Workflow", "Request approved",
               ["workflow_label", "request_url"],
               {"workflow_label": "VPN Access", "request_url": "http://x/workflow/request/123"}),
    EmailEvent("reject", "Workflow", "Request rejected",
               ["workflow_label", "request_url"],
               {"workflow_label": "VPN Access", "request_url": "http://x/workflow/request/123"}),
    EmailEvent("token_link", "Workflow", "Complete your information",
               ["workflow_label", "token_url"],
               {"workflow_label": "Onboarding", "token_url": "http://x/workflow/token/abc"}),
    EmailEvent("corrections_with_link", "Workflow", "Corrections needed (with link)",
               ["workflow_label", "token_url"],
               {"workflow_label": "Onboarding", "token_url": "http://x/workflow/token/abc"}),
    EmailEvent("request_corrections", "Workflow", "Corrections needed",
               ["workflow_label"],
               {"workflow_label": "Onboarding"}),
    EmailEvent("complete", "Workflow", "Onboarding complete",
               ["workflow_label", "app_url"],
               {"workflow_label": "Onboarding"}),
    EmailEvent("account_created", "Workflow", "Account created",
               ["workflow_label", "display_name", "sam", "mail", "app_url"],
               {"workflow_label": "Onboarding", "display_name": "Marie Curie",
                "sam": "curiem", "mail": "marie.curie@lpp.fr"}),
    EmailEvent("booking_reminder", "Booking", "Booking ending soon",
               ["delay_label", "resource_name", "start_date", "end_date", "os",
                "software", "bookings_url"],
               {"delay_label": "in 3 days", "resource_name": "Laptop-07",
                "start_date": "2026-07-01", "end_date": "2026-07-10",
                "os": "Ubuntu 24.04", "software": "Python, MATLAB",
                "bookings_url": "http://x/?tab=bookings"}),
    EmailEvent("resource_status", "Booking", "Resource status change",
               ["subject_line", "headline", "resource_name", "location", "bookings_url"],
               {"subject_line": "Your resource is ready for pickup",
                "headline": "Your booked resource is ready for pickup.",
                "resource_name": "Laptop-07", "location": "Room 12",
                "bookings_url": "http://x/?tab=bookings"}),
    EmailEvent("resource_out_of_service", "Booking", "Resource out of service",
               ["headline", "resource_name", "location", "bookings_url"],
               {"headline": "Laptop-07 has been marked out of service.",
                "resource_name": "Laptop-07", "location": "Room 12",
                "bookings_url": "http://x/?tab=bookings"}),
]


class MailTemplatesConfig(BaseModel):
    layout: str = DEFAULT_LAYOUT
    overrides: dict[str, EmailTemplate] = Field(default_factory=dict)


mail_templates_config = section("mail_templates", MailTemplatesConfig, label="Email Templates")

_env = SandboxedEnvironment(autoescape=True)


def _render(template: EmailTemplate, layout: str, context: dict) -> tuple[str, str]:
    subject = _env.from_string(template.subject).render(**context)
    inner = _env.from_string(template.body).render(**context)
    body_html = _env.from_string(layout).render(content=Markup(inner), **context)
    return subject, body_html
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_email_templates.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/backend/email_templates.py tests/test_email_templates.py
git commit -m "feat(mail): email template model, defaults, registry, sandboxed renderer"
```

---

## Task 2: Config-backed `render_email` + `get_template` (merge-on-read + fallback)

**Files:**
- Modify: `not_dot_net/backend/email_templates.py`
- Test: `tests/test_email_templates.py`

**Interfaces:**
- Consumes: `mail_templates_config`, `DEFAULT_TEMPLATES`, `DEFAULT_LAYOUT`, `_render` (Task 1)
- Produces:
  - `async def get_template(key: str) -> EmailTemplate`
  - `async def render_email(key: str, context: dict) -> tuple[str, str]`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_email_templates.py
import pytest
from not_dot_net.backend.email_templates import (
    get_template, render_email, mail_templates_config, MailTemplatesConfig,
    EmailTemplate, DEFAULT_TEMPLATES,
)

BASE_CTX = {"app_name": "LPP", "app_url": "http://x/", "recipient_name": "A",
            "workflow_label": "VPN", "request_url": "http://x/workflow/request/1"}


@pytest.mark.asyncio
async def test_get_template_falls_back_to_default():
    tmpl = await get_template("approve")
    assert tmpl.subject == DEFAULT_TEMPLATES["approve"].subject


@pytest.mark.asyncio
async def test_override_wins_over_default():
    await mail_templates_config.set(MailTemplatesConfig(
        overrides={"approve": EmailTemplate(subject="Custom!", body="<p>custom</p>")}))
    tmpl = await get_template("approve")
    assert tmpl.subject == "Custom!"
    subject, body = await render_email("approve", BASE_CTX)
    assert subject == "Custom!"
    assert "<p>custom</p>" in body


@pytest.mark.asyncio
async def test_new_default_key_works_with_old_saved_config():
    # A saved config that predates an event must still render that event.
    await mail_templates_config.set(MailTemplatesConfig(overrides={}))
    subject, _ = await render_email("reject", BASE_CTX)
    assert "rejected" in subject.lower()


@pytest.mark.asyncio
async def test_broken_override_falls_back_to_default(caplog):
    await mail_templates_config.set(MailTemplatesConfig(
        overrides={"approve": EmailTemplate(subject="{{ oops", body="<p>x</p>")}))  # bad Jinja
    subject, _ = await render_email("approve", BASE_CTX)
    assert subject == DEFAULT_TEMPLATES["approve"].subject.replace(
        "{{ workflow_label }}", "VPN")  # default rendered, not the broken override
    assert any("failed to render" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_email_templates.py -k "get_template or override or new_default or broken" -v`
Expected: FAIL — `ImportError: cannot import name 'get_template'`.

- [ ] **Step 3: Write minimal implementation**

Append to `not_dot_net/backend/email_templates.py`:

```python
async def get_template(key: str) -> EmailTemplate:
    cfg = await mail_templates_config.get()
    return cfg.overrides.get(key) or DEFAULT_TEMPLATES[key]


async def render_email(key: str, context: dict) -> tuple[str, str]:
    cfg = await mail_templates_config.get()
    template = cfg.overrides.get(key) or DEFAULT_TEMPLATES[key]
    layout = cfg.layout or DEFAULT_LAYOUT
    try:
        return _render(template, layout, context)
    except TemplateError:
        logger.warning("Email template %r failed to render; using default", key,
                       exc_info=True)
        return _render(DEFAULT_TEMPLATES[key], DEFAULT_LAYOUT, context)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_email_templates.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/backend/email_templates.py tests/test_email_templates.py
git commit -m "feat(mail): config-backed render_email with merge-on-read + safe fallback"
```

---

## Task 3: Refactor workflow notifications to use templates + deep links

**Files:**
- Modify: `not_dot_net/backend/notifications.py`
- Test: `tests/test_notifications.py` (update), `tests/test_workflow_notifications_integration.py` (update)

**Interfaces:**
- Consumes: `email_templates.render_email` (Task 2)
- Produces: unchanged public `notify(...)` / `resolve_recipients(...)` signatures; removes `TEMPLATES` and the old `render_email(event, workflow_label, **kwargs)` from `notifications.py`.

- [ ] **Step 1: Write the failing test**

Replace the template-oriented tests in `tests/test_notifications.py` with behavior tests. Add:

```python
# tests/test_notifications.py  (add; remove any import of `render_email` from notifications)
import pytest
from unittest.mock import AsyncMock, patch
from not_dot_net.backend.notifications import notify, _display_from_email


def test_display_from_email_uses_local_part():
    assert _display_from_email("marie.curie@lpp.fr") == "marie.curie"
    assert _display_from_email("") == ""


@pytest.mark.asyncio
async def test_notify_submit_includes_request_deeplink():
    req = FakeRequest(token=None, id="req-1")
    sent_bodies = []

    async def fake_send(to, subject, body):
        sent_bodies.append((to, subject, body))

    async def email_for(uid):  # not used for role recipients
        return None

    async def users_by_role(role):
        class U: ...
        u = U(); u.email = "director@lpp.fr"; return [u]

    with patch("not_dot_net.backend.mail.send_mail", new=fake_send), \
         patch("not_dot_net.config.org_config.get", new=AsyncMock(
             return_value=type("O", (), {"base_url": "http://x", "app_name": "LPP"})())):
        await notify(req, "submit", "request", VPN_WORKFLOW, email_for, users_by_role)

    assert sent_bodies, "an email should be sent"
    _, _, body = sent_bodies[0]
    assert "http://x/workflow/request/req-1" in body


@pytest.mark.asyncio
async def test_notify_submit_with_token_uses_token_link():
    req = FakeRequest(token="tok-9", id="req-2")
    sent = []
    with patch("not_dot_net.backend.mail.send_mail",
               new=lambda to, s, b: sent.append(b) or _async_none()), \
         patch("not_dot_net.config.org_config.get", new=AsyncMock(
             return_value=type("O", (), {"base_url": "http://x", "app_name": "LPP"})())):
        async def users_by_role(role):
            class U: ...
            u = U(); u.email = "n@lpp.fr"; return [u]
        await notify(req, "submit", "request", VPN_WORKFLOW, AsyncMock(), users_by_role)
    assert any("http://x/workflow/token/tok-9" in b for b in sent)
```

Add this tiny helper near the top of the test module if not already present:

```python
async def _async_none():
    return None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_notifications.py -k "deeplink or token_link or display_from_email" -v`
Expected: FAIL — `_display_from_email` import error / no request URL in body.

- [ ] **Step 3: Write minimal implementation**

In `not_dot_net/backend/notifications.py`: delete the `TEMPLATES` dict and the old `render_email` function. Add the helper and rewrite `notify`:

```python
def _display_from_email(email: str) -> str:
    return (email or "").split("@")[0]


async def notify(
    request,
    event: str,
    step_key: str,
    workflow: WorkflowConfig,
    get_user_email,
    get_users_by_role,
    get_users_by_permission=None,
) -> list[str]:
    """Fire notifications for a workflow event. Returns list of emails sent to."""
    from not_dot_net.backend.mail import send_mail
    from not_dot_net.backend.email_templates import render_email
    from not_dot_net.config import org_config

    org_cfg = await org_config.get()
    base_url = org_cfg.base_url.rstrip("/")
    app_name = (org_cfg.app_name or "not-dot-net").strip() or "not-dot-net"

    rules = _matching_rules(workflow, event, step_key)
    if not rules:
        return []

    all_sent = []
    for rule in rules:
        recipients = await resolve_recipients(
            rule.notify, request, get_user_email, get_users_by_role, get_users_by_permission,
        )
        template_key = event
        base_ctx = {
            "app_name": app_name,
            "app_url": f"{base_url}/",
            "workflow_label": workflow.label,
            "request_url": f"{base_url}/workflow/request/{request.id}",
            "step_label": step_key,
            "requester_name": "",
        }
        if request.token and event in ("submit", "request_corrections"):
            template_key = "token_link" if event == "submit" else "corrections_with_link"
            base_ctx["token_url"] = f"{base_url}/workflow/token/{request.token}"

        for email in recipients:
            ctx = {**base_ctx, "recipient_name": _display_from_email(email)}
            subject, body = await render_email(template_key, ctx)
            await send_mail(email, subject, body)
            all_sent.append(email)

    return all_sent
```

Keep `render_email` removed from `notifications.py` — `from not_dot_net.backend.notifications import render_email` no longer works (callers are repointed in Task 4).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_notifications.py tests/test_workflow_notifications_integration.py -v`
Expected: PASS. Update any remaining assertion in `test_workflow_notifications_integration.py` that checked the old one-sentence bodies to assert on the new content/links instead (e.g. `assert "/workflow/request/" in body`).

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/backend/notifications.py tests/test_notifications.py tests/test_workflow_notifications_integration.py
git commit -m "refactor(mail): workflow notifications render via templates with deep links"
```

---

## Task 4: Repoint workflow_service send sites

**Files:**
- Modify: `not_dot_net/backend/workflow_service.py` (`_send_token_link` ~181-193, `account_created` send ~1047-1062)
- Test: `tests/test_workflow_notifications_integration.py` (add token-link body assertion)

**Interfaces:**
- Consumes: `email_templates.render_email`
- Produces: no signature changes.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_workflow_notifications_integration.py  (add)
import pytest
from unittest.mock import patch, AsyncMock
from not_dot_net.backend.workflow_service import _send_token_link


@pytest.mark.asyncio
async def test_send_token_link_body_has_token_url():
    class Req: id = "r1"; target_email = "newcomer@lpp.fr"; token = "tok-1"
    class Wf: label = "Onboarding"
    captured = {}

    async def fake_send(to, subject, body):
        captured["to"], captured["subject"], captured["body"] = to, subject, body

    with patch("not_dot_net.backend.mail.send_mail", new=fake_send), \
         patch("not_dot_net.config.org_config.get", new=AsyncMock(
             return_value=type("O", (), {"base_url": "http://x", "app_name": "LPP"})())):
        await _send_token_link(Req(), Wf())

    assert captured["to"] == "newcomer@lpp.fr"
    assert "http://x/workflow/token/tok-1" in captured["body"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_workflow_notifications_integration.py -k token_url -v`
Expected: FAIL — old `_send_token_link` imports `notifications.render_email` (now removed) → ImportError, or body lacks token URL.

- [ ] **Step 3: Write minimal implementation**

Rewrite `_send_token_link` in `not_dot_net/backend/workflow_service.py`:

```python
async def _send_token_link(req, wf):
    """Send the token link email directly to the target person."""
    from not_dot_net.backend.mail import send_mail
    from not_dot_net.backend.email_templates import render_email
    from not_dot_net.config import org_config

    if not req.target_email or not req.token:
        return
    org_cfg = await org_config.get()
    base_url = org_cfg.base_url.rstrip("/")
    app_name = (org_cfg.app_name or "not-dot-net").strip() or "not-dot-net"
    ctx = {
        "app_name": app_name,
        "app_url": f"{base_url}/",
        "recipient_name": req.target_email.split("@")[0],
        "workflow_label": wf.label,
        "token_url": f"{base_url}/workflow/token/{req.token}",
    }
    subject, body = await render_email("token_link", ctx)
    await send_mail(req.target_email, subject, body)
```

In the `account_created` send block (currently ~1047-1062), replace the import and call:

```python
    from not_dot_net.backend.mail import send_mail
    from not_dot_net.backend.email_templates import render_email
    from not_dot_net.config import org_config
    contact_email = (request.target_email or "").strip()
    if contact_email:
        wf_cfg = await workflows_config.get()
        wf = wf_cfg.workflows.get(request.type)
        workflow_label = (wf.label if wf else request.type) or "Workflow"
        org_cfg = await org_config.get()
        base_url = org_cfg.base_url.rstrip("/")
        app_name = (org_cfg.app_name or "not-dot-net").strip() or "not-dot-net"
        subject, body = await render_email("account_created", {
            "app_name": app_name,
            "app_url": f"{base_url}/",
            "recipient_name": display_name,
            "display_name": display_name,
            "workflow_label": workflow_label,
            "sam": sam,
            "mail": new_user.mail,
        })
        await send_mail(contact_email, subject, body)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_workflow_notifications_integration.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/backend/workflow_service.py tests/test_workflow_notifications_integration.py
git commit -m "refactor(mail): repoint token-link and account-created emails to templates"
```

---

## Task 5: Refactor booking emails to templates

**Files:**
- Modify: `not_dot_net/backend/booking_service.py` (`render_resource_status_body` ~174-183, `render_booking_reminder_body` ~427-447, `_notify_status_change` ~208-240, `_booking_reminder_subject` ~421-425, send sites ~219, ~236, ~493)
- Test: `tests/test_booking_reminders.py` (update)

**Interfaces:**
- Consumes: `email_templates.render_email`, `org_config`
- Produces:
  - `def _booking_reminder_context(*, user, booking, resource, delay_label, bookings_url) -> dict`
  - `def _resource_status_context(*, user, resource, subject_line, headline, bookings_url) -> dict`
  - removes `render_resource_status_body`, `render_booking_reminder_body`, `_booking_reminder_subject` (subjects now in templates).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_booking_reminders.py  (add; remove imports of the deleted render_* fns)
from not_dot_net.backend.booking_service import (
    _booking_reminder_context, _resource_status_context,
)


def test_booking_reminder_context_shape():
    class U: full_name = "Marie Curie"; email = "m@lpp.fr"
    class B: start_date = "2026-07-01"; end_date = "2026-07-10"; os_choice = "Ubuntu"; \
             software_tags = ["Python", "MATLAB"]
    class R: name = "Laptop-07"
    ctx = _booking_reminder_context(user=U(), booking=B(), resource=R(),
                                    delay_label="in 3 days",
                                    bookings_url="http://x/?tab=bookings")
    assert ctx["recipient_name"] == "Marie Curie"
    assert ctx["resource_name"] == "Laptop-07"
    assert ctx["software"] == "Python, MATLAB"
    assert ctx["delay_label"] == "in 3 days"
    assert ctx["bookings_url"] == "http://x/?tab=bookings"


def test_resource_status_context_defaults_blank_location():
    class U: full_name = ""; email = "m@lpp.fr"
    class R: name = "Laptop-07"; location = None
    ctx = _resource_status_context(user=U(), resource=R(),
                                   subject_line="Ready", headline="It is ready.",
                                   bookings_url="http://x/?tab=bookings")
    assert ctx["recipient_name"] == "m@lpp.fr"   # falls back to email
    assert ctx["location"] == "-"
    assert ctx["subject_line"] == "Ready"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_booking_reminders.py -k context -v`
Expected: FAIL — `ImportError: cannot import name '_booking_reminder_context'`.

- [ ] **Step 3: Write minimal implementation**

In `not_dot_net/backend/booking_service.py`:

(a) Replace `render_booking_reminder_body` / `render_resource_status_body` with pure context-builders:

```python
def _booking_reminder_context(*, user, booking, resource, delay_label, bookings_url) -> dict:
    return {
        "recipient_name": user.full_name or user.email,
        "delay_label": delay_label,
        "resource_name": resource.name,
        "start_date": str(booking.start_date),
        "end_date": str(booking_last_day(booking.end_date)),
        "os": booking.os_choice or "-",
        "software": ", ".join(booking.software_tags or []) or "-",
        "bookings_url": bookings_url,
    }


def _resource_status_context(*, user, resource, subject_line, headline, bookings_url) -> dict:
    return {
        "recipient_name": user.full_name or user.email,
        "subject_line": subject_line,
        "headline": headline,
        "resource_name": resource.name,
        "location": resource.location or "-",
        "bookings_url": bookings_url,
    }
```

(b) Add a small helper to compute common booking context bits (call once per send site):

```python
async def _booking_email_env() -> tuple[str, str]:
    cfg = await org_config.get()
    app_name = (cfg.app_name or "not-dot-net").strip() or "not-dot-net"
    base_url = cfg.base_url.rstrip("/")
    return app_name, f"{base_url}/?tab=bookings"
```

(c) Rewrite `_notify_status_change` to render via templates. Keep `_STATUS_NOTICE` (per-status `(subject_line, headline)`). Replace the two `send_mail` blocks:

```python
async def _notify_status_change(resource: Resource, new_status: ResourceStatus,
                                today: date) -> None:
    from not_dot_net.backend.email_templates import render_email
    app_name, bookings_url = await _booking_email_env()
    async with session_scope() as session:
        booking_user = await _current_booking_user(session, resource.id, today)

        if new_status in _STATUS_NOTICE:
            if booking_user is None or not booking_user.email:
                return
            subject_line, headline = _STATUS_NOTICE[new_status]
            ctx = _resource_status_context(user=booking_user, resource=resource,
                                           subject_line=subject_line, headline=headline,
                                           bookings_url=bookings_url)
            ctx["app_name"] = app_name
            ctx["app_url"] = bookings_url.split("/?")[0] + "/"
            subject, body = await render_email("resource_status", ctx)
            await send_mail(booking_user.email, subject, body)
            return

        if new_status is ResourceStatus.OUT_OF_SERVICE:
            targets = await _out_of_service_recipients(session)
            if booking_user is not None:
                targets.append(booking_user)
            seen: set[str] = set()
            headline = f"{resource.name} has been marked out of service."
            for u in targets:
                if not u.email or u.email in seen:
                    continue
                seen.add(u.email)
                ctx = _resource_status_context(user=u, resource=resource,
                                               subject_line="", headline=headline,
                                               bookings_url=bookings_url)
                ctx["app_name"] = app_name
                ctx["app_url"] = bookings_url.split("/?")[0] + "/"
                subject, body = await render_email("resource_out_of_service", ctx)
                await send_mail(u.email, subject, body)
```

(d) Rewrite the reminder send site (`send_booking_end_reminders`, ~493). Remove `_booking_reminder_subject`; render subject+body from the template:

```python
            # inside the per-row loop, replacing the send_mail(...) call:
            from not_dot_net.backend.email_templates import render_email
            app_name, bookings_url = await _booking_email_env()
            delay_label = _booking_reminder_delay_label(days_until_end)
            ctx = _booking_reminder_context(user=user, booking=booking, resource=resource,
                                            delay_label=delay_label, bookings_url=bookings_url)
            ctx["app_name"] = app_name
            ctx["app_url"] = bookings_url.split("/?")[0] + "/"
            subject, body = await render_email("booking_reminder", ctx)
            await send_mail(user.email, subject, body)
```

Remove now-unused `escape` import if nothing else uses it (grep first; leave if other call sites remain).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_booking_reminders.py -v`
Expected: PASS. Update any assertion that referenced the old `render_*_body` output to assert on `_*_context` dicts or on a `render_email` result (e.g. `"/?tab=bookings" in body`).

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/backend/booking_service.py tests/test_booking_reminders.py
git commit -m "refactor(mail): booking status + reminder emails render via templates"
```

---

## Task 6: `?tab=` deep-link in the shell

**Files:**
- Modify: `not_dot_net/frontend/shell.py` (`main_page` ~40-71)
- Test: `tests/test_shell_tab_deeplink.py` (Create)

**Interfaces:**
- Consumes: nothing new.
- Produces: `main_page(user=..., tab: str | None = None)` selecting the initial tab from `?tab=`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_shell_tab_deeplink.py
import pytest
from nicegui.testing import User


@pytest.mark.asyncio
async def test_tab_query_param_selects_bookings(user: User):
    await user.open("/?tab=bookings")
    # The bookings panel content renders (resource list / empty-state heading).
    await user.should_see("bookings")  # adjust to a stable string from render_bookings


@pytest.mark.asyncio
async def test_unknown_tab_falls_back_to_dashboard(user: User):
    await user.open("/?tab=does-not-exist")
    await user.should_see("dashboard")  # adjust to a stable dashboard string
```

> Implementer note: match the `should_see` strings to actual rendered text (use the same assertions other shell tests use). The point is selection, not exact copy.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_shell_tab_deeplink.py -v`
Expected: FAIL — `?tab=bookings` does not change the initial tab (defaults to saved/dashboard).

- [ ] **Step 3: Write minimal implementation**

In `not_dot_net/frontend/shell.py`, add `tab` to the page handler signature and resolve it. Change:

```python
    async def main_page(
        user: Optional[User] = Depends(current_active_user_optional),
        tab: Optional[str] = None,
    ):
```

After `available_tabs` is built and before `initial_tab` (currently lines ~69-71):

```python
        tab_keys = {
            "dashboard": dashboard_label,
            "people": people_label,
            "bookings": bookings_label,
            "pages": pages_label,
            "new_request": new_request_label,
            "audit": audit_label,
            "settings": settings_label,
            "ad_accounts": ad_accounts_label,
            "users": users_label,
        }
        requested_tab = tab_keys.get(tab or "")
        saved_tab = app.storage.user.get("active_tab")
        if requested_tab in available_tabs:
            initial_tab = requested_tab
        elif saved_tab in available_tabs:
            initial_tab = saved_tab
        else:
            initial_tab = dashboard_label
```

(Replace the existing two `saved_tab` / `initial_tab` lines.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_shell_tab_deeplink.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/frontend/shell.py tests/test_shell_tab_deeplink.py
git commit -m "feat(shell): ?tab= query param deep-links to a tab (for email links)"
```

---

## Task 7: Admin editor for email templates

**Files:**
- Create: `not_dot_net/frontend/admin_email_templates.py`
- Modify: `not_dot_net/frontend/admin_settings.py` (add expansion ~64-68 area), `not_dot_net/frontend/i18n.py` (EN+FR keys)
- Test: `tests/test_admin_email_templates.py` (Create)

**Interfaces:**
- Consumes: `email_templates` (`EMAIL_EVENTS`, `DEFAULT_TEMPLATES`, `DEFAULT_LAYOUT`, `EmailTemplate`, `MailTemplatesConfig`, `mail_templates_config`, `_render`), `check_permission`
- Produces: `async def render(user) -> None`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_admin_email_templates.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_admin_email_templates.py -v`
Expected: FAIL — module/behaviour not present (or assertions fail before editor wired).

> Note: these tests exercise the config + render contract the editor relies on (no NiceGUI rendering needed to prove the data path). The editor UI itself is verified manually + by the import smoke test in Step 4.

- [ ] **Step 3: Write minimal implementation**

Create `not_dot_net/frontend/admin_email_templates.py` (mirror `vocabularies_editor.py` structure — `render(user)`, `check_permission`, CodeMirror via `ui.codemirror`, refresh with `@ui.refreshable`):

```python
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
        ui.button(t("save"), on_click=save)
        ui.button(t("reset_to_default"), on_click=reset).props("flat color=negative")
```

Wire into `not_dot_net/frontend/admin_settings.py` — add near the other editor expansions (after field_definitions, ~line 68):

```python
from not_dot_net.frontend.admin_email_templates import render as render_email_templates
# ...
    with ui.expansion(t("email_templates"), icon="mail").classes("w-full"):
        await render_email_templates(user)
```

Add i18n keys to `not_dot_net/frontend/i18n.py` (EN + FR), in both locale dicts:

```python
# EN
"email_templates": "Email Templates",
"email_templates_help": "Customize the emails the intranet sends. Defaults are used until you override them.",
"email_layout": "Base layout",
"email_layout_help": "Shared HTML wrapper around every email body. Use {{ content }} where the message goes.",
"available_variables": "Available variables",
"subject": "Subject",
"preview": "Preview",
"reset_to_default": "Reset to default",
"reset_done": "Reset to default",
# FR
"email_templates": "Modèles d'e-mail",
"email_templates_help": "Personnalisez les e-mails envoyés par l'intranet. Les modèles par défaut sont utilisés tant que vous ne les remplacez pas.",
"email_layout": "Mise en page de base",
"email_layout_help": "Encadré HTML partagé autour de chaque e-mail. Placez {{ content }} là où le message doit apparaître.",
"available_variables": "Variables disponibles",
"subject": "Objet",
"preview": "Aperçu",
"reset_to_default": "Réinitialiser",
"reset_done": "Réinitialisé",
```

(If `saved` / `save` keys already exist, reuse them — don't duplicate.)

- [ ] **Step 4: Run tests + import smoke check**

Run: `uv run pytest tests/test_admin_email_templates.py -v`
Expected: PASS.
Run: `uv run python -c "import not_dot_net.frontend.admin_email_templates; import not_dot_net.frontend.admin_settings"`
Expected: no ImportError.

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/frontend/admin_email_templates.py not_dot_net/frontend/admin_settings.py not_dot_net/frontend/i18n.py tests/test_admin_email_templates.py
git commit -m "feat(mail): Settings editor for email templates + base layout"
```

---

## Task 8: Full-suite regression + cleanup

**Files:**
- Modify: any test still asserting old email copy.

- [ ] **Step 1: Run the whole suite**

Run: `uv run pytest`
Expected: all pass. Investigate any failure referencing old one-sentence bodies or removed symbols (`notifications.render_email`, `notifications.TEMPLATES`, `render_resource_status_body`, `render_booking_reminder_body`, `_booking_reminder_subject`).

- [ ] **Step 2: Grep for dangling references**

Run:
```bash
grep -rn "notifications import render_email\|notifications.TEMPLATES\|render_resource_status_body\|render_booking_reminder_body\|_booking_reminder_subject" not_dot_net/ tests/
```
Expected: no matches in `not_dot_net/`; fix any test still importing them.

- [ ] **Step 3: Commit any test fixes**

```bash
git add -A
git commit -m "test(mail): update email assertions to templated bodies + links"
```

---

## Self-Review

**Spec coverage:**
- Data model & storage (`MailTemplatesConfig`, merge-on-read) → Task 1 + 2 ✓
- Event registry (`EMAIL_EVENTS`, variables, sample) → Task 1 ✓
- Sandboxed renderer + autoescape + Markup → Task 1 (`_render`) ✓
- Deep links (request/token/bookings/app) → Task 3 (workflow), 4 (token/account), 5 (booking) ✓
- Refactor notifications/workflow/booking → Tasks 3/4/5 ✓
- Shell `?tab=` → Task 6 ✓
- Editor (per-event subject/body, variable help, live preview, reset, base layout) → Task 7 ✓
- Error handling: bad Jinja falls back, preview surfaces errors → Task 2 (fallback test) + Task 7 (preview try/except) ✓
- i18n for editor chrome → Task 7 ✓
- No migration / `send_mail` unchanged → Global Constraints, honored throughout ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code. The only "adjust to actual rendered string" notes are in Task 6 shell tests where exact copy depends on existing render output — flagged explicitly, not a hidden placeholder.

**Type consistency:** `render_email(key, context)` async signature is consistent across Tasks 2/3/4/5/7. `EmailTemplate(subject, body)`, `MailTemplatesConfig(layout, overrides)`, `_render(template, layout, context)`, `get_template(key)` names match across all tasks. Context-builder names `_booking_reminder_context` / `_resource_status_context` are consistent between Task 5 definition and its test.
