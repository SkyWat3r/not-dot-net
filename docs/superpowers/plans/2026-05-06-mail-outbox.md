# Mail outbox — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace synchronous `send_mail` with a durable, retryable, restart-safe outbox: every outbound mail INSERTs a row, a single in-process worker drains the table with capped exponential backoff, and admins can inspect pending/failed mail in Settings.

**Architecture:** A new `mail_outbox` table holds rendered emails. `backend.mail.send_mail(to, subject, body_html)` is the single public API and just enqueues. A `run_outbox_worker` asyncio task started in `app.startup` polls the table, sends via `aiosmtplib` using the *current* `MailConfig`, and applies the retry schedule `[60s, 5m, 15m, 1h, 6h, 24h]` (max 7 attempts, ≈31h before `failed_at`). Read-only Quasar table in Settings → Mail surfaces queue state.

**Tech Stack:** Python 3.10+, SQLAlchemy 2.x async (asyncpg / aiosqlite), Alembic, aiosmtplib, NiceGUI, pytest, pytest-asyncio.

---

## File Structure

**Create:**

- `not_dot_net/backend/mail_outbox.py` — `MailOutbox` model, `_drain_outbox_once`, `_send_one`, `run_outbox_worker`. Constants `BACKOFF`, `MAX_ATTEMPTS=7`, `BATCH_SIZE=50`, `POLL_CEILING_S=60`.
- `alembic/versions/0012_add_mail_outbox.py` — schema migration.
- `tests/test_mail_outbox.py` — drain/retry/worker tests.

**Modify:**

- `not_dot_net/backend/mail.py` — `send_mail(to, subject, body_html)` becomes an enqueue (no `mail_settings` arg).
- `not_dot_net/backend/db.py` — register `not_dot_net.backend.mail_outbox` in `create_db_and_tables` so the table is created in dev.
- `not_dot_net/backend/notifications.py` — drop the `mail_settings` parameter on `notify` and the inner `send_mail(...)` call.
- `not_dot_net/backend/security_alerts.py` — drop `_BACKGROUND_ALERT_TASKS`, `queue_security_alert`, and the `mail_settings` plumbing in `send_security_alert`. Notify functions await `send_mail` directly.
- `not_dot_net/backend/workflow_service.py` — `_send_token_link` and `_fire_notifications` drop the `MailConfig` lookup.
- `not_dot_net/frontend/workflow_token.py` — `send_code` drops the `MailConfig` lookup.
- `not_dot_net/frontend/login.py` — `_audit_failed_superuser_login` calls `await security_alerts.notify_superuser_login_failed(...)` directly (no `queue_security_alert` wrapper).
- `not_dot_net/backend/users.py` — `on_after_login` similarly calls `await security_alerts.notify_superuser_login_success(...)` directly.
- `not_dot_net/app.py` — start the worker in `startup`, cancel in `shutdown`.
- `not_dot_net/frontend/admin_settings.py` — render the outbox panel after the `mail` config form (mirrors the `prefix == "ldap"` hook pattern).
- `not_dot_net/frontend/i18n.py` — EN+FR keys for the outbox panel.
- `tests/test_notifications.py` — `fake_send_mail` signatures drop `mail_settings`; `notify(...)` calls drop `mail_settings=...`.
- `tests/test_workflow_notifications_integration.py` — same.
- `tests/test_security_alerts.py` — `send_security_alert` now takes no `mail_settings`; `assert_any_await` removes the `MailConfig` arg; the queue tests for `_BACKGROUND_ALERT_TASKS` are dropped (the global set is gone).
- `tests/test_resend_notification.py` — same shape adjustments if it patches `send_mail` with the four-arg signature.
- `tests/test_auth_endpoints.py` — `notify_superuser_login_*` tests drop the `mail_settings` arg in their asserts.

---

## Task 1: `MailOutbox` model + migration + dev table registration

**Files:**
- Create: `not_dot_net/backend/mail_outbox.py`
- Create: `alembic/versions/0012_add_mail_outbox.py`
- Modify: `not_dot_net/backend/db.py:75-83` (`create_db_and_tables`)
- Test: `tests/test_mail_outbox.py` (new)

- [ ] **Step 1: Write failing model round-trip test**

Create `tests/test_mail_outbox.py`:

```python
"""Tests for the mail outbox model and worker drain."""
import uuid
from datetime import datetime, timezone

import pytest

from not_dot_net.backend.db import session_scope
from not_dot_net.backend.mail_outbox import MailOutbox


async def test_mail_outbox_round_trip():
    """A new MailOutbox row inserts and reloads with expected defaults."""
    async with session_scope() as session:
        row = MailOutbox(
            to_address="root@test.local",
            subject="hello",
            body_html="<p>body</p>",
            next_attempt_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)

    assert row.id is not None
    assert row.attempts == 0
    assert row.sent_at is None
    assert row.failed_at is None
    assert row.last_error is None
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/test_mail_outbox.py -v
```
Expected: ImportError on `MailOutbox`.

- [ ] **Step 3: Create the model**

Create `not_dot_net/backend/mail_outbox.py`:

```python
"""Durable mail outbox: enqueue + background worker that drains it.

`backend.mail.send_mail` writes rows here; `run_outbox_worker` (started
in `app.startup`) sends them with capped exponential backoff. Single
in-process worker; not safe across multiple replicas.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import Index, String, Text, func, select
from sqlalchemy.orm import Mapped, MappedAsDataclass, mapped_column

from not_dot_net.backend.db import Base, session_scope

logger = logging.getLogger("not_dot_net.mail_outbox")

BACKOFF = [
    timedelta(seconds=60),
    timedelta(minutes=5),
    timedelta(minutes=15),
    timedelta(hours=1),
    timedelta(hours=6),
    timedelta(hours=24),
]
MAX_ATTEMPTS = 7
BATCH_SIZE = 50
POLL_CEILING_S = 60


class MailOutbox(MappedAsDataclass, Base, kw_only=True):
    __tablename__ = "mail_outbox"

    to_address: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(500))
    body_html: Mapped[str] = mapped_column(Text)
    next_attempt_at: Mapped[datetime] = mapped_column()
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default_factory=uuid.uuid4)
    attempts: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=None, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(nullable=True, default=None)
    failed_at: Mapped[datetime | None] = mapped_column(nullable=True, default=None)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)


Index(
    "ix_mail_outbox_pending",
    MailOutbox.sent_at,
    MailOutbox.failed_at,
    MailOutbox.next_attempt_at,
)
Index("ix_mail_outbox_failed_at", MailOutbox.failed_at)
```

- [ ] **Step 4: Register the model in dev table creation**

Edit `not_dot_net/backend/db.py:75-83`. The current body of `create_db_and_tables` imports several modules so their models register on `Base.metadata`. Add `mail_outbox` to that list:

```python
async def create_db_and_tables() -> None:
    if _engine is None:
        raise RuntimeError("DB not initialized — call init_db() first")
    import not_dot_net.backend.workflow_models  # noqa: F401 — register models with Base
    import not_dot_net.backend.booking_models  # noqa: F401 — register models with Base
    import not_dot_net.backend.audit  # noqa: F401 — register models with Base
    import not_dot_net.backend.app_config  # noqa: F401 — register AppSetting with Base
    import not_dot_net.backend.page_models  # noqa: F401 — register Page with Base
    import not_dot_net.backend.encrypted_storage  # noqa: F401 — register EncryptedFile with Base
    import not_dot_net.backend.tenure_service  # noqa: F401 — register UserTenure with Base
    import not_dot_net.backend.mail_outbox  # noqa: F401 — register MailOutbox with Base
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
```

Also add the same `import not_dot_net.backend.mail_outbox  # noqa: F401` to the top of `tests/conftest.py:setup_db` near the other imports so the test SQLite has the table.

- [ ] **Step 5: Run model test to verify it passes**

```
uv run pytest tests/test_mail_outbox.py -v
```
Expected: 1 passed.

- [ ] **Step 6: Write the Alembic migration**

Create `alembic/versions/0012_add_mail_outbox.py`:

```python
"""Add mail_outbox table for durable retryable mail sending.

Revision ID: 0012
Revises: 0011
"""
from alembic import op
import sqlalchemy as sa


revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mail_outbox",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("to_address", sa.String(255), nullable=False),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("failed_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    op.create_index("ix_mail_outbox_created_at", "mail_outbox", ["created_at"])
    op.create_index(
        "ix_mail_outbox_pending",
        "mail_outbox",
        ["sent_at", "failed_at", "next_attempt_at"],
    )
    op.create_index("ix_mail_outbox_failed_at", "mail_outbox", ["failed_at"])


def downgrade() -> None:
    op.drop_index("ix_mail_outbox_failed_at", table_name="mail_outbox")
    op.drop_index("ix_mail_outbox_pending", table_name="mail_outbox")
    op.drop_index("ix_mail_outbox_created_at", table_name="mail_outbox")
    op.drop_table("mail_outbox")
```

- [ ] **Step 7: Run full suite as regression**

```
uv run pytest --tb=short -q
```
Expected: 703 passed (702 before + the new round-trip test).

- [ ] **Step 8: Commit**

```bash
git add not_dot_net/backend/mail_outbox.py not_dot_net/backend/db.py alembic/versions/0012_add_mail_outbox.py tests/conftest.py tests/test_mail_outbox.py
git commit -m "$(cat <<'EOF'
feat(mail): add MailOutbox model and migration

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `send_mail` becomes an enqueue

**Files:**
- Modify: `not_dot_net/backend/mail.py`
- Modify: `not_dot_net/backend/notifications.py` (drop `mail_settings` plumbing)
- Modify: `not_dot_net/backend/security_alerts.py` (drop `_BACKGROUND_ALERT_TASKS`, `queue_security_alert`, `mail_settings` plumbing)
- Modify: `not_dot_net/backend/workflow_service.py` (`_send_token_link`, `_fire_notifications`)
- Modify: `not_dot_net/frontend/workflow_token.py` (`send_code`)
- Modify: `not_dot_net/frontend/login.py` (`_audit_failed_superuser_login`)
- Modify: `not_dot_net/backend/users.py` (`on_after_login`)
- Modify: `tests/test_notifications.py`, `tests/test_workflow_notifications_integration.py`, `tests/test_security_alerts.py`, `tests/test_resend_notification.py`, `tests/test_auth_endpoints.py`

This task changes the public API and updates every caller + their tests in one shot. After this task, mail is enqueued but never delivered (the worker doesn't exist yet); existing tests that mock `send_mail` keep working because they intercept the call before it touches the DB.

- [ ] **Step 1: Write failing enqueue test**

Append to `tests/test_mail_outbox.py`:

```python
async def test_send_mail_enqueues_a_row():
    """send_mail inserts a row in mail_outbox and returns; nothing is sent yet."""
    from sqlalchemy import select
    from not_dot_net.backend.mail import send_mail

    await send_mail("u@test.local", "Hi", "<p>body</p>")

    async with session_scope() as session:
        result = await session.execute(select(MailOutbox))
        rows = list(result.scalars().all())
    assert len(rows) == 1
    row = rows[0]
    assert row.to_address == "u@test.local"
    assert row.subject == "Hi"
    assert row.body_html == "<p>body</p>"
    assert row.sent_at is None
    assert row.failed_at is None
    assert row.attempts == 0
```

- [ ] **Step 2: Refactor `send_mail`**

Replace `not_dot_net/backend/mail.py` entirely:

```python
"""Mail config + the public enqueue API.

`send_mail` is the only public entrypoint. It writes a row to the
`mail_outbox` table; the background worker in `mail_outbox.py` drains
that table and performs the actual SMTP send using the current
`MailConfig`.
"""

import logging
from datetime import datetime, timezone

from pydantic import BaseModel

from not_dot_net.backend.app_config import section
from not_dot_net.backend.db import session_scope

logger = logging.getLogger("not_dot_net.mail")


class MailConfig(BaseModel):
    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_tls: bool = False
    smtp_user: str = ""
    smtp_password: str = ""
    from_address: str = "noreply@not-dot-net.dev"
    dev_mode: bool = True
    dev_catch_all: str = ""


mail_config = section("mail", MailConfig, label="Email / SMTP")


async def send_mail(to: str, subject: str, body_html: str) -> None:
    """Enqueue an outbound mail. Returns when the row is committed.

    The worker (run_outbox_worker) drains the table and performs the
    actual SMTP send using the current MailConfig.
    """
    from not_dot_net.backend.mail_outbox import MailOutbox

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with session_scope() as session:
        row = MailOutbox(
            to_address=to,
            subject=subject,
            body_html=body_html,
            next_attempt_at=now,
        )
        session.add(row)
        await session.commit()
```

- [ ] **Step 3: Drop `mail_settings` from `notifications.notify`**

Edit `not_dot_net/backend/notifications.py`. Find the `notify(...)` function (around line 110-147 — it currently takes `mail_settings: MailConfig` and threads it into `send_mail(...)`). Make these changes:

  - Remove `mail_settings: MailConfig` from `notify`'s signature.
  - Remove the `mail_settings` argument from the inner `send_mail(...)` call (around line 144); it becomes `await send_mail(email, subject, body)`.
  - Drop any `MailConfig` import if it becomes unused.

- [ ] **Step 4: Drop `mail_settings` from `security_alerts`**

Edit `not_dot_net/backend/security_alerts.py`. Make these changes:

  - Remove `_BACKGROUND_ALERT_TASKS: set[asyncio.Task[Any]] = set()` (top of file).
  - Remove the `queue_security_alert(coro)` function entirely.
  - In `send_security_alert(subject, body_html, ...)`: remove the `mail_settings: MailConfig | None = None` kwarg, the `mail_config.get()` lookup, and pass only `(email, subject, body_html)` to `send_mail`. The function signature becomes:

```python
async def send_security_alert(subject: str, body_html: str) -> list[str]:
    """Send one security alert to every configured security recipient."""
    recipients = await get_security_alert_recipients()
    for email in recipients:
        await send_mail(email, subject, body_html)
    return recipients
```

  - Drop `MailConfig`, `mail_config` imports if they become unused (only `_subject` still needs `org_config`).

- [ ] **Step 5: Drop the `queue_security_alert` wrappers in callers**

Edit `not_dot_net/frontend/login.py`. The `_audit_failed_superuser_login` function currently calls `security_alerts.queue_security_alert(security_alerts.notify_superuser_login_failed(...))`. Replace with a direct await:

```python
    await security_alerts.notify_superuser_login_failed(
        user,
        ip=ip,
        user_agent=user_agent,
    )
```

Edit `not_dot_net/backend/users.py`. `on_after_login` similarly calls `security_alerts.queue_security_alert(security_alerts.notify_superuser_login_success(...))`. Replace with:

```python
        await security_alerts.notify_superuser_login_success(
            user,
            ip=ip,
            user_agent=user_agent,
        )
```

The login response is no longer blocked on SMTP because `notify_superuser_login_success` now just enqueues — the actual SMTP send happens in the worker.

- [ ] **Step 6: Drop `MailConfig` lookups in `workflow_service`**

Edit `not_dot_net/backend/workflow_service.py`. Two functions:

  - `_send_token_link(req, wf)` (around line 250-262): drop the `mail_cfg = await mail_config.get()` line and the `mail_cfg` argument to `send_mail(req.target_email, subject, body, mail_cfg)`. The call becomes `await send_mail(req.target_email, subject, body)`.
  - `_fire_notifications(req, event, step_key, wf)` (around line 265-306): drop the `mail_cfg = await mail_config.get()` line and the `mail_settings=mail_cfg` argument to `notify(...)`. Drop the `from not_dot_net.backend.mail import mail_config` import.

- [ ] **Step 7: Drop `MailConfig` lookup in `workflow_token`**

Edit `not_dot_net/frontend/workflow_token.py`. The `send_code` callback (around line 51-65): drop the `mail_cfg = await mail_config.get()` line and the `mail_cfg` argument. Drop `mail_config` from imports if it becomes unused.

- [ ] **Step 8: Update tests**

Run the full suite to find what breaks:

```
uv run pytest --tb=short -q
```

Expected failures cluster around three patterns. Fix each:

  - **`fake_send_mail` signatures**: Tests in `tests/test_notifications.py`, `tests/test_workflow_notifications_integration.py`, `tests/test_resend_notification.py` define `async def fake_send_mail(to, subject, body_html, mail_settings):`. Drop the `mail_settings` parameter from each definition.
  - **`notify(..., mail_settings=...)` calls**: Same files call `notify(... mail_settings=MailConfig(dev_mode=True), ...)`. Drop the `mail_settings=...` keyword from every call. Remove the now-unused `MailConfig` import if applicable.
  - **`send_mail_mock.assert_any_await(..., mail_settings)`**: `tests/test_security_alerts.py` and `tests/test_auth_endpoints.py` assert with the four-arg signature. Drop the trailing `mail_settings` from each `assert_any_await(...)` call.
  - **`_BACKGROUND_ALERT_TASKS` tests**: `tests/test_security_alerts.py` has `test_queue_security_alert_keeps_task_until_completion` and `test_queue_security_alert_logs_schedule_failure`. Delete both (the function is gone). Also drop the `_BACKGROUND_ALERT_TASKS` import.
  - **`send_security_alert` test**: the test that calls `send_security_alert(... mail_settings=MailConfig(dev_mode=True))` drops the kwarg.

After each fix, re-run targeted: `uv run pytest tests/test_notifications.py tests/test_workflow_notifications_integration.py tests/test_security_alerts.py tests/test_resend_notification.py tests/test_auth_endpoints.py -q`.

- [ ] **Step 9: Run full suite**

```
uv run pytest --tb=short -q
```

Expected: 702 passed. (Started at 703 after Task 1; Step 1 of this task added `test_send_mail_enqueues_a_row` (+1); Step 8 deletes the two `_BACKGROUND_ALERT_TASKS` tests (-2). Net −1.)

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
refactor(mail): send_mail is now an enqueue; remove MailConfig plumbing

send_mail(to, subject, body_html) writes a mail_outbox row and returns;
the worker (next task) drains it. Drops the mail_settings parameter from
notify(), send_security_alert(), _send_token_link, _fire_notifications,
and workflow_token.send_code. Removes the in-memory _BACKGROUND_ALERT_TASKS
and queue_security_alert wrapper from security_alerts; login/grant alerts
now await send_mail directly (which is non-blocking).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `_drain_outbox_once` and `_send_one`

**Files:**
- Modify: `not_dot_net/backend/mail_outbox.py`
- Modify: `tests/test_mail_outbox.py`

The drain function reads ready rows and processes each. The send helper handles dev-mode short-circuit, SMTP send, and per-row state transitions.

- [ ] **Step 1: Write failing dev-mode test**

Append to `tests/test_mail_outbox.py`:

```python
async def test_drain_dev_mode_marks_sent_without_smtp():
    from not_dot_net.backend.mail import send_mail
    from not_dot_net.backend.mail_outbox import _drain_outbox_once

    await send_mail("u@test.local", "Hi", "<p>body</p>")

    processed = await _drain_outbox_once()
    assert processed == 1

    async with session_scope() as session:
        result = await session.execute(select(MailOutbox))
        rows = list(result.scalars().all())
    assert rows[0].sent_at is not None
    assert rows[0].attempts == 0  # dev mode does not count as a retry
    assert rows[0].failed_at is None
```

(The `select` import is already there from Task 1's tests; if not, add `from sqlalchemy import select` at the top of the test file.)

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/test_mail_outbox.py::test_drain_dev_mode_marks_sent_without_smtp -v
```
Expected: ImportError on `_drain_outbox_once`.

- [ ] **Step 3: Implement `_drain_outbox_once` and `_send_one`**

Append to `not_dot_net/backend/mail_outbox.py`:

```python
async def _send_one(row: MailOutbox, mail_cfg) -> None:
    """Attempt to deliver one row. Mutates `row` in place; the caller commits."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if mail_cfg.dev_mode:
        effective_to = mail_cfg.dev_catch_all or row.to_address
        logger.info(
            "[MAIL dev] To: %s (original: %s) Subject: %s",
            effective_to, row.to_address, row.subject,
        )
        row.sent_at = now
        return

    import aiosmtplib
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"] = mail_cfg.from_address
    msg["To"] = mail_cfg.dev_catch_all or row.to_address
    msg["Subject"] = row.subject
    msg.set_content(row.body_html, subtype="html")

    try:
        await aiosmtplib.send(
            msg,
            hostname=mail_cfg.smtp_host,
            port=mail_cfg.smtp_port,
            start_tls=mail_cfg.smtp_tls,
            username=mail_cfg.smtp_user or None,
            password=mail_cfg.smtp_password or None,
        )
        row.sent_at = now
    except Exception as exc:
        row.attempts += 1
        row.last_error = str(exc)[:1024]
        if row.attempts >= MAX_ATTEMPTS:
            row.failed_at = now
        else:
            row.next_attempt_at = now + BACKOFF[row.attempts - 1]


async def _drain_outbox_once() -> int:
    """Process up to BATCH_SIZE rows whose next_attempt_at has passed.

    Returns the number of rows processed (regardless of success).
    Per-row commits so a single failure cannot roll back the others.
    """
    from not_dot_net.backend.mail import mail_config

    mail_cfg = await mail_config.get()
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    async with session_scope() as session:
        result = await session.execute(
            select(MailOutbox)
            .where(
                MailOutbox.sent_at.is_(None),
                MailOutbox.failed_at.is_(None),
                MailOutbox.next_attempt_at <= now,
            )
            .order_by(MailOutbox.next_attempt_at)
            .limit(BATCH_SIZE)
        )
        rows = list(result.scalars().all())

    processed = 0
    for row_id in [r.id for r in rows]:
        async with session_scope() as session:
            row = await session.get(MailOutbox, row_id)
            if row is None or row.sent_at is not None or row.failed_at is not None:
                continue
            try:
                await _send_one(row, mail_cfg)
            except Exception:
                logger.exception("Outbox drain unexpectedly raised for row %s", row_id)
            await session.commit()
            processed += 1
    return processed
```

- [ ] **Step 4: Run dev-mode test**

```
uv run pytest tests/test_mail_outbox.py -v
```
Expected: 3 passed (round-trip + enqueue + dev-mode drain).

- [ ] **Step 5: Add SMTP failure tests**

Append to `tests/test_mail_outbox.py`:

```python
async def test_drain_smtp_failure_increments_attempts_and_bumps_next_attempt():
    from unittest.mock import AsyncMock, patch
    from not_dot_net.backend.mail import send_mail, mail_config, MailConfig
    from not_dot_net.backend.mail_outbox import _drain_outbox_once, BACKOFF

    # Production-mode config so _send_one tries the real send path
    cfg = await mail_config.get()
    await mail_config.set(MailConfig(**{**cfg.model_dump(), "dev_mode": False}))

    await send_mail("u@test.local", "Hi", "<p>body</p>")

    with patch(
        "not_dot_net.backend.mail_outbox.aiosmtplib.send",
        new=AsyncMock(side_effect=RuntimeError("smtp down")),
    ):
        await _drain_outbox_once()

    async with session_scope() as session:
        row = (await session.execute(select(MailOutbox))).scalar_one()
    assert row.sent_at is None
    assert row.failed_at is None
    assert row.attempts == 1
    assert row.last_error.startswith("smtp down")
    assert row.next_attempt_at >= datetime.now(timezone.utc).replace(tzinfo=None)


async def test_drain_marks_failed_after_max_attempts():
    from unittest.mock import AsyncMock, patch
    from not_dot_net.backend.mail import send_mail, mail_config, MailConfig
    from not_dot_net.backend.mail_outbox import _drain_outbox_once, MAX_ATTEMPTS

    cfg = await mail_config.get()
    await mail_config.set(MailConfig(**{**cfg.model_dump(), "dev_mode": False}))

    await send_mail("u@test.local", "Hi", "<p>body</p>")

    with patch(
        "not_dot_net.backend.mail_outbox.aiosmtplib.send",
        new=AsyncMock(side_effect=RuntimeError("smtp down")),
    ):
        for _ in range(MAX_ATTEMPTS):
            # Move next_attempt_at into the past so each drain picks the row up
            async with session_scope() as session:
                row = (await session.execute(select(MailOutbox))).scalar_one()
                row.next_attempt_at = datetime.now(timezone.utc).replace(tzinfo=None)
                await session.commit()
            await _drain_outbox_once()

    async with session_scope() as session:
        row = (await session.execute(select(MailOutbox))).scalar_one()
    assert row.attempts == MAX_ATTEMPTS
    assert row.failed_at is not None
    assert row.sent_at is None


async def test_drain_truncates_long_error_messages():
    from unittest.mock import AsyncMock, patch
    from not_dot_net.backend.mail import send_mail, mail_config, MailConfig
    from not_dot_net.backend.mail_outbox import _drain_outbox_once

    cfg = await mail_config.get()
    await mail_config.set(MailConfig(**{**cfg.model_dump(), "dev_mode": False}))

    await send_mail("u@test.local", "Hi", "<p>body</p>")

    big = "x" * 5000
    with patch(
        "not_dot_net.backend.mail_outbox.aiosmtplib.send",
        new=AsyncMock(side_effect=RuntimeError(big)),
    ):
        await _drain_outbox_once()

    async with session_scope() as session:
        row = (await session.execute(select(MailOutbox))).scalar_one()
    assert len(row.last_error) <= 1024
```

- [ ] **Step 6: Run full mail_outbox tests**

```
uv run pytest tests/test_mail_outbox.py -v
```
Expected: 6 passed.

- [ ] **Step 7: Full regression**

```
uv run pytest --tb=short -q
```
Expected: 705 passed (702 after Task 2 + 3 new tests in this task).

- [ ] **Step 8: Commit**

```bash
git add not_dot_net/backend/mail_outbox.py tests/test_mail_outbox.py
git commit -m "$(cat <<'EOF'
feat(mail): _drain_outbox_once + _send_one with capped exponential backoff

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `run_outbox_worker` + app.startup hook

**Files:**
- Modify: `not_dot_net/backend/mail_outbox.py`
- Modify: `not_dot_net/app.py:78-99` (`startup` and `shutdown` closures)
- Modify: `tests/test_mail_outbox.py`

- [ ] **Step 1: Write failing worker-loop test**

Append to `tests/test_mail_outbox.py`:

```python
async def test_run_outbox_worker_processes_pending_then_sleeps():
    """The worker drains a pending row, then sleeps until the next ready row."""
    import asyncio
    from not_dot_net.backend.mail import send_mail
    from not_dot_net.backend.mail_outbox import run_outbox_worker

    await send_mail("u@test.local", "Hi", "<p>body</p>")

    task = asyncio.create_task(run_outbox_worker())
    # Yield enough times for the worker to pick up the row and process it.
    for _ in range(50):
        await asyncio.sleep(0)
        async with session_scope() as session:
            row = (await session.execute(select(MailOutbox))).scalar_one()
        if row.sent_at is not None:
            break

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert row.sent_at is not None
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/test_mail_outbox.py::test_run_outbox_worker_processes_pending_then_sleeps -v
```
Expected: ImportError on `run_outbox_worker`.

- [ ] **Step 3: Implement `run_outbox_worker`**

Append to `not_dot_net/backend/mail_outbox.py`:

```python
async def run_outbox_worker() -> None:
    """Forever: drain pending rows, sleep until the next one is due (≤ 60s)."""
    while True:
        try:
            await _drain_outbox_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Outbox worker iteration failed")

        try:
            sleep_s = await _seconds_until_next_attempt()
        except Exception:
            logger.exception("Failed to compute next outbox wakeup")
            sleep_s = POLL_CEILING_S
        await asyncio.sleep(sleep_s)


async def _seconds_until_next_attempt() -> float:
    """Return seconds until the soonest pending row's next_attempt_at,
    bounded above by POLL_CEILING_S. Empty queue → ceiling."""
    async with session_scope() as session:
        result = await session.execute(
            select(MailOutbox.next_attempt_at)
            .where(
                MailOutbox.sent_at.is_(None),
                MailOutbox.failed_at.is_(None),
            )
            .order_by(MailOutbox.next_attempt_at)
            .limit(1)
        )
        next_at = result.scalar_one_or_none()
    if next_at is None:
        return POLL_CEILING_S
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return max(0.0, min(POLL_CEILING_S, (next_at - now).total_seconds()))
```

- [ ] **Step 4: Run worker test**

```
uv run pytest tests/test_mail_outbox.py::test_run_outbox_worker_processes_pending_then_sleeps -v
```
Expected: 1 passed.

- [ ] **Step 5: Hook the worker into app.startup**

Edit `not_dot_net/app.py:78-99`. Add the worker task before the existing LDAP reaper hook:

```python
    _worker_tasks: set = set()

    async def startup():
        logger.info("Running async startup...")
        if dev_mode:
            await create_db_and_tables()
            logger.info("Dev tables created")
        if dev_mode:
            await ensure_default_admin(DEV_ADMIN_EMAIL, DEV_ADMIN_PASSWORD)
        if _seed_fake_users:
            from not_dot_net.backend.seeding import seed_fake_users
            await seed_fake_users()

        from not_dot_net.backend.mail_outbox import run_outbox_worker
        outbox_task = asyncio.create_task(run_outbox_worker())
        _worker_tasks.add(outbox_task)
        outbox_task.add_done_callback(_worker_tasks.discard)
        logger.info("Mail outbox worker started")

        from not_dot_net.backend.auth.ldap import start_connection_reaper
        start_connection_reaper()
        logger.info("Startup complete")

    async def shutdown():
        logger.info("Shutting down...")
        for task in list(_worker_tasks):
            task.cancel()
        for task in list(_worker_tasks):
            try:
                await task
            except (Exception, asyncio.CancelledError):
                pass
        from not_dot_net.backend.auth.ldap import drop_all_connections
        drop_all_connections()
        logger.info("Shutdown complete")
```

(Add `import asyncio` at the top of `app.py` if it's not already there.)

- [ ] **Step 6: Run full suite**

```
uv run pytest --tb=short -q
```
Expected: 706 passed (705 after Task 3 + 1 new worker test).

- [ ] **Step 7: Commit**

```bash
git add not_dot_net/backend/mail_outbox.py not_dot_net/app.py tests/test_mail_outbox.py
git commit -m "$(cat <<'EOF'
feat(mail): outbox worker started in app.startup, cancelled on shutdown

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Admin UI panel + i18n

**Files:**
- Modify: `not_dot_net/frontend/admin_settings.py`
- Modify: `not_dot_net/frontend/i18n.py`

A read-only Quasar table below the auto-rendered Mail config form, mirroring the `prefix == "ldap"` hook pattern.

- [ ] **Step 1: Add i18n keys**

Edit `not_dot_net/frontend/i18n.py`. In the EN block (near the existing `mail`-related strings or alongside the `field_more` group), add:

```python
        "mail_outbox": "Outbox",
        "mail_outbox_pending": "Pending",
        "mail_outbox_failed": "Failed",
        "mail_outbox_empty": "No mail in this state.",
        "mail_outbox_recipient": "Recipient",
        "mail_outbox_subject": "Subject",
        "mail_outbox_attempts": "Attempts",
        "mail_outbox_next_attempt": "Next attempt",
        "mail_outbox_failed_at": "Failed at",
        "mail_outbox_last_error": "Last error",
```

In the FR block, mirror them:

```python
        "mail_outbox": "File d'attente",
        "mail_outbox_pending": "En attente",
        "mail_outbox_failed": "Échoués",
        "mail_outbox_empty": "Aucun courriel dans cet état.",
        "mail_outbox_recipient": "Destinataire",
        "mail_outbox_subject": "Sujet",
        "mail_outbox_attempts": "Tentatives",
        "mail_outbox_next_attempt": "Prochaine tentative",
        "mail_outbox_failed_at": "Échoué à",
        "mail_outbox_last_error": "Dernière erreur",
```

- [ ] **Step 2: Add the panel render hook**

Edit `not_dot_net/frontend/admin_settings.py`. The `render(user)` function has a hook for `prefix == "ldap"` at the end of the loop body that renders extra UI. Add a parallel hook for `prefix == "mail"` right after it:

```python
            if prefix == "ldap":
                _render_ldap_sync(user)
            if prefix == "mail":
                await _render_mail_outbox(user)
```

Then append a new function at the bottom of `admin_settings.py`:

```python
async def _render_mail_outbox(user):
    """Read-only outbox table: pending + failed mails."""
    from sqlalchemy import select
    from not_dot_net.backend.db import session_scope
    from not_dot_net.backend.mail_outbox import MailOutbox

    ui.label(t("mail_outbox")).classes("text-subtitle1 mt-4")

    async def _load_rows(state: str) -> list[dict]:
        async with session_scope() as session:
            stmt = select(MailOutbox).limit(100)
            if state == "pending":
                stmt = stmt.where(
                    MailOutbox.sent_at.is_(None),
                    MailOutbox.failed_at.is_(None),
                ).order_by(MailOutbox.created_at)
            else:
                stmt = stmt.where(MailOutbox.failed_at.is_not(None)).order_by(
                    MailOutbox.failed_at.desc()
                )
            rows = (await session.execute(stmt)).scalars().all()
        return [
            {
                "to": r.to_address,
                "subject": r.subject[:100],
                "attempts": r.attempts,
                "next_attempt_at": r.next_attempt_at.strftime("%Y-%m-%d %H:%M"),
                "failed_at": r.failed_at.strftime("%Y-%m-%d %H:%M") if r.failed_at else "",
                "last_error": (r.last_error or "")[:200],
            }
            for r in rows
        ]

    table_container = ui.column().classes("w-full")
    pending_columns = [
        {"name": "to", "label": t("mail_outbox_recipient"), "field": "to", "align": "left"},
        {"name": "subject", "label": t("mail_outbox_subject"), "field": "subject", "align": "left"},
        {"name": "attempts", "label": t("mail_outbox_attempts"), "field": "attempts", "align": "center"},
        {"name": "next_attempt_at", "label": t("mail_outbox_next_attempt"), "field": "next_attempt_at", "align": "left"},
        {"name": "last_error", "label": t("mail_outbox_last_error"), "field": "last_error", "align": "left"},
    ]
    failed_columns = [
        {"name": "to", "label": t("mail_outbox_recipient"), "field": "to", "align": "left"},
        {"name": "subject", "label": t("mail_outbox_subject"), "field": "subject", "align": "left"},
        {"name": "attempts", "label": t("mail_outbox_attempts"), "field": "attempts", "align": "center"},
        {"name": "failed_at", "label": t("mail_outbox_failed_at"), "field": "failed_at", "align": "left"},
        {"name": "last_error", "label": t("mail_outbox_last_error"), "field": "last_error", "align": "left"},
    ]

    async def render_tab(state: str):
        table_container.clear()
        rows = await _load_rows(state)
        with table_container:
            if not rows:
                ui.label(t("mail_outbox_empty")).classes("text-grey")
                return
            cols = pending_columns if state == "pending" else failed_columns
            ui.table(columns=cols, rows=rows, row_key="to").props("flat bordered dense").classes("w-full")

    with ui.tabs() as tabs:
        pending_tab = ui.tab(t("mail_outbox_pending"))
        failed_tab = ui.tab(t("mail_outbox_failed"))
    tabs.on_value_change(lambda e: render_tab("pending" if e.value == t("mail_outbox_pending") else "failed"))
    tabs.value = t("mail_outbox_pending")
    await render_tab("pending")
```

- [ ] **Step 3: Run i18n + admin tests**

```
uv run pytest tests/test_i18n.py tests/test_workflow_editor.py -q
```
Expected: all green (the new i18n keys are symmetric, and the admin hook doesn't break editor tests).

- [ ] **Step 4: Full regression**

```
uv run pytest --tb=short -q
```
Expected: 706 passed.

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/frontend/admin_settings.py not_dot_net/frontend/i18n.py
git commit -m "$(cat <<'EOF'
feat(admin): read-only mail outbox panel in Settings → Mail

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Final regression and push

- [ ] **Step 1: Run the full suite**

```
uv run pytest --tb=short -q
```
Expected: 706 passed.

- [ ] **Step 2: Manual smoke check (recommended)**

```
uv run python -m not_dot_net.cli serve --host localhost --port 8088
```

Open `/`, log in, trigger a workflow (e.g. an onboarding initiation). In the dev DB, verify a row appears in `mail_outbox` and quickly transitions to `sent_at IS NOT NULL`. Check Settings → Mail → Outbox: the table should be empty (Pending and Failed both empty after a successful drain).

- [ ] **Step 3: Push**

Ask the user before pushing — per session memory, every push needs explicit consent.

---

## Notes for the implementer

- **Why per-row commits**: with single-transaction batch processing, one row's SMTP exception would roll back ALL the row updates in the batch (including successful sends). Per-row commit isolates failures.
- **Why `dev_mode` doesn't increment `attempts`**: dev-mode short-circuits to `sent_at` immediately; the row is "delivered" by definition. No retry needed.
- **Why a single in-process worker (not multiple)**: this matches the current k8s deployment (single replica). Multi-replica would race on the same rows; the v1 design accepts that limitation. A `SELECT … FOR UPDATE SKIP LOCKED` upgrade is a natural follow-up if the deployment grows.
- **Why `aiosmtplib.send` is patched in `mail_outbox`'s namespace**: the module imports `aiosmtplib` lazily inside `_send_one`; tests patch `not_dot_net.backend.mail_outbox.aiosmtplib.send`. If you move the import to module level, update the patch path.
- **Why the worker is created in `startup` and not at module import**: the asyncio event loop must exist. NiceGUI's `app.on_startup` runs inside the loop.
- **Why `MailConfig` is loaded once per drain iteration**: if SMTP settings change while the worker is running, the next iteration picks them up. Per-row reload would be wasteful.
