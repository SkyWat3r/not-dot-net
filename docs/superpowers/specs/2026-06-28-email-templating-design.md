# Email Templating — Design

**Date:** 2026-06-28
**Status:** Approved (design)

## Problem

The intranet's outbound emails are weak. Workflow notifications
(`backend/notifications.py`) are one-sentence `.format()` strings with **no
link to the app or to the relevant action** — e.g. the entire `approve` body is
*"Your X request has been approved."* Booking emails
(`backend/booking_service.py`) are richer (HTML tables) but use a separate
f-string rendering style, share no layout with workflow mail, and also carry no
deep links. There is no way for an admin to reword any of it.

We want osTicket-style **configurable email templates**: rich defaults out of
the box, admin-editable in the UI, with real content and deep links into the
specific action.

## Goals

1. Every email gains a deep link to the relevant action and richer context.
2. All app email families (workflow, booking, account-created) share one base
   layout and one rendering path.
3. Admins can edit subject + body (Jinja) per event, and edit the shared base
   layout, from Settings — with live preview and reset-to-default.
4. No regression in the durable mail-outbox pipeline (`send_mail` stays the
   single enqueue entry point).

## Non-Goals

- **Per-locale templates.** Each template is a single text the admin writes in
  whatever language they choose (FR for this lab). No EN/FR variants.
- **Security-alert templating.** `backend/security_alerts.py` stays as-is —
  admins should not reword security warnings. (It still benefits indirectly if
  we later route it through the layout, but that is out of scope here.)
- File-based template storage. Templates live in the DB-backed ConfigSection,
  consistent with the rest of the app's config philosophy.

## Approach

ConfigSection-backed overrides over code-level defaults, rendered with a
sandboxed Jinja environment, plus a Settings editor. Jinja2 3.1.6 is already an
available transitive dependency — no new requirement.

### 1. Module: `backend/email_templates.py`

Owns the data model, defaults, event registry, and renderer.

```python
class EmailTemplate(BaseModel):
    subject: str          # Jinja
    body: str             # Jinja, HTML fragment (no <html>/<body> wrapper)

class MailTemplatesConfig(BaseModel):
    layout: str = DEFAULT_LAYOUT                    # base HTML wrapper with {{ content }}
    overrides: dict[str, EmailTemplate] = Field(default_factory=dict)

mail_templates_config = section("mail_templates", MailTemplatesConfig,
                                label="Email Templates")
```

- `DEFAULT_TEMPLATES: dict[str, EmailTemplate]` and `DEFAULT_LAYOUT: str` are
  defined in code so emails are rich with zero admin action.
- **Merge-on-read:** `get_template(key)` returns
  `overrides.get(key) or DEFAULT_TEMPLATES[key]`. Storage holds only what the
  admin overrode, so adding a new event in code later never strands a saved
  config on a missing template (the vocab-registry / FieldDefinition lesson).
- `get_layout()` returns `cfg.layout` (defaulting to `DEFAULT_LAYOUT`).

### 2. Event registry (data over code)

A declarative list is the contract that makes "configurable" usable. It drives
the editor's variable help, the live preview, and documents each event:

```python
@dataclass(frozen=True)
class EmailEvent:
    key: str
    group: str                 # "Workflow" | "Booking"
    label: str
    variables: list[str]       # event-specific vars (beyond the common set)
    sample: dict               # sample context for live preview

EMAIL_EVENTS: list[EmailEvent] = [...]
```

**Common variables** available to every template and the layout:
`app_name`, `app_url`, `recipient_name`.

**Per-event variables (and deep link):**

| key                     | group    | extra variables                                            | link var      |
|-------------------------|----------|------------------------------------------------------------|---------------|
| `submit`                | Workflow | `workflow_label`, `requester_name`, `request_url`          | `request_url` |
| `step_assigned`         | Workflow | `workflow_label`, `step_label`, `request_url`              | `request_url` |
| `approve`               | Workflow | `workflow_label`, `request_url`                            | `request_url` |
| `reject`                | Workflow | `workflow_label`, `request_url`                            | `request_url` |
| `token_link`            | Workflow | `workflow_label`, `token_url`                              | `token_url`   |
| `corrections_with_link` | Workflow | `workflow_label`, `token_url`                              | `token_url`   |
| `request_corrections`   | Workflow | `workflow_label`                                           | —             |
| `complete`              | Workflow | `workflow_label`, `app_url`                                | `app_url`     |
| `account_created`       | Workflow | `workflow_label`, `display_name`, `sam`, `mail`, `app_url` | `app_url`     |
| `booking_reminder`      | Booking  | `resource_name`, `start_date`, `end_date`, `os`, `software`, `bookings_url` | `bookings_url` |
| `resource_status`       | Booking  | `resource_name`, `location`, `headline`, `bookings_url`    | `bookings_url` |
| `resource_out_of_service` | Booking | `resource_name`, `location`, `headline`, `bookings_url`   | `bookings_url` |

Deep-link targets (computed in code, never typed by admins):
- `request_url` = `{base_url}/workflow/request/{request_id}`
- `token_url`   = `{base_url}/workflow/token/{token}`
- `bookings_url`= `{base_url}/?tab=bookings`
- `app_url`     = `{base_url}/`

`base_url` and `app_name` come from `org_config` (already
`base_url="..."`, `app_name="LPP Intranet"`).

### 3. Renderer

```python
def render_email(key: str, context: dict) -> tuple[str, str]:
    tmpl = get_template(key)
    env = SandboxedEnvironment(autoescape=True)
    subject = env.from_string(tmpl.subject).render(**context)
    inner = env.from_string(tmpl.body).render(**context)
    full = env.from_string(get_layout()).render(
        content=Markup(inner), **context)        # content marked safe; no double-escape
    return subject, full
```

- `SandboxedEnvironment(autoescape=True)`: variable *values* (names, etc.) are
  HTML-escaped; the admin's literal template HTML passes through. Sandbox is
  cheap insurance even though only `manage_settings` admins can edit (lab
  intranet, trusted admins — pragmatic security).
- The layout receives the rendered body as `content` plus the common vars, so a
  footer can reference `{{ app_name }}` / `{{ app_url }}`.
- `render_email` is **async** only if it must read the ConfigSection; to keep it
  pure/testable, callers fetch the config once and pass templates in. Decision:
  `get_template`/`get_layout` read `mail_templates_config` (async). Provide an
  inner pure `_render(template, layout, context)` for unit tests, and an async
  `render_email(key, context)` wrapper that fetches config then calls `_render`.

### 4. Default templates & layout

`DEFAULT_LAYOUT` — a minimal responsive HTML wrapper: header band with
`{{ app_name }}`, `{{ content }}`, and a footer (`{{ app_name }}` linked to
`{{ app_url }}` + a "do not reply" line). This is where the admin later adds a
lab logo/signature.

`DEFAULT_TEMPLATES` — each body carries real content + the deep link. Example
`approve`:

```html
<p>Hello {{ recipient_name }},</p>
<p>Your <strong>{{ workflow_label }}</strong> request has been approved.</p>
<p><a href="{{ request_url }}">View your request</a></p>
```

Booking templates keep their existing detail table, moved into the body
fragment and wrapped by the shared layout, plus the `bookings_url` link.

### 5. Refactors

- **`backend/notifications.py`**: delete the `.format()` `TEMPLATES` dict and
  `render_email`. `notify` and `_send_token_link` build a context dict
  (including the computed link vars) and call
  `email_templates.render_email(key, ctx)`. `resolve_recipients` /
  `_matching_rules` are unchanged. Recipient display name resolution:
  `recipient_name` is best-effort (resolve from the recipient `User` where we
  have one; fall back to the email local-part or a neutral greeting).
- **`backend/workflow_service.py`**: `_send_token_link` and the
  `account_created` send site call the new `render_email`. (They currently
  import `notifications.render_email` — repoint to `email_templates`.)
- **`backend/booking_service.py`**: `render_resource_status_body` and
  `render_booking_reminder_body` become thin context-builders that call
  `render_email("resource_status"/"booking_reminder", ctx)`. The
  `_STATUS_NOTICE` / out-of-service subject lines move into the templates'
  `subject` field. Existing `escape()` calls are dropped — Jinja autoescape
  replaces them.

### 6. Shell deep-link support (`frontend/shell.py`)

Add a `tab: str | None = None` kwarg to the `main_page` page handler (NiceGUI
binds `?tab=` query params to handler kwargs). Map stable keys to the
already-computed localized labels:

```python
_TAB_KEYS = {"dashboard": dashboard_label, "people": people_label,
             "bookings": bookings_label, "pages": pages_label, ...}
requested = _TAB_KEYS.get(tab)
initial_tab = requested if requested in available_tabs else (
    saved_tab if saved_tab in available_tabs else dashboard_label)
```

Unknown/forbidden `tab` values silently fall back (no error). This makes
`{base_url}/?tab=bookings` land on the bookings tab.

### 7. Editor (`frontend/admin_email_templates.py`)

A Settings surface (new tab or section within admin settings, following the
existing admin pattern):

- Events grouped by `EmailEvent.group` (Workflow / Booking).
- Per event: subject `ui.input` + CodeMirror body editor (reuse the
  pages.py/workflow_editor.py CodeMirror pattern — `.classes("w-full grow")`,
  no fixed min-height), a side panel listing that event's available variables,
  a **Preview** action rendering the template with `EmailEvent.sample` into an
  HTML preview block, **Reset to default** (drops the override), and **Save**.
- A separate **Base layout** editor (CodeMirror) for `cfg.layout`, with its own
  reset and preview (rendered around a placeholder body).
- Gated on `manage_settings` (same as other admin settings); callback-level
  `check_permission` guard per the NiceGUI pattern.
- Editing is server-side only; saving writes the override/layout into
  `mail_templates_config`.

## Components & isolation

| Unit | Responsibility | Depends on |
|------|----------------|------------|
| `backend/email_templates.py` | model, defaults, event registry, renderer | `app_config.section`, `jinja2.sandbox`, `org_config` |
| `notifications.py` (refactor) | resolve recipients + build context + send | `email_templates`, `mail.send_mail` |
| `booking_service.py` (refactor) | build booking context + send | `email_templates`, `mail.send_mail` |
| `frontend/shell.py` (small change) | `?tab=` deep link | — |
| `frontend/admin_email_templates.py` | editor UI | `email_templates`, CodeMirror |

The renderer is independently testable via the pure `_render(template, layout,
context)`; the event registry is data; the editor only touches the
ConfigSection.

## Error handling

- **Bad admin Jinja** (syntax error / undefined var): rendering must not crash a
  workflow transition or the outbox. `render_email` catches Jinja errors and
  falls back to the code default template for that event (logged at WARNING).
  The editor's Preview surfaces template errors inline so the admin sees them
  before saving.
- **Missing context var**: `SandboxedEnvironment` default renders undefined as
  empty; acceptable (a missing optional var just yields blank). Required link
  vars are always supplied by code.
- **Outbox unchanged**: `send_mail(to, subject, body_html)` still enqueues a row;
  ret/backoff/dev-mode behavior is untouched.

## Testing

- `_render` unit tests: variable interpolation, autoescape of a malicious
  `recipient_name` (`<script>` escaped), layout wrapping, `Markup` no-double-
  escape.
- `get_template` merge-on-read: override wins; unknown stays on default; a new
  default key works with an old saved config that lacks it.
- Renderer fallback: a broken override falls back to the default and logs.
- Per-event render produces the correct deep link from sample context.
- Refactor regression: update `tests/test_notifications.py`,
  `test_workflow_notifications_integration.py`, `test_booking_reminders.py`,
  and any body-asserting tests to the new content/links. Existing email-body
  assertions will change — expected.
- Shell `?tab=` test: `/?tab=bookings` selects bookings; unknown falls back to
  dashboard; forbidden tab (no permission) falls back.
- Editor tests (NiceGUI `User`): save override → `render_email` reflects it;
  reset → reverts to default; preview renders sample.

## Migration / compatibility

- No Alembic migration — `mail_templates` is a new ConfigSection row created
  lazily on first set, like every other section.
- No change to `mail_outbox` schema or `send_mail` signature.
- i18n: add EN+FR keys for the new editor UI strings (labels, buttons, help).
  Template *content* itself is not i18n (single admin-authored text).
```
