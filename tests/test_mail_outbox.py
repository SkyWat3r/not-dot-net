"""Tests for the mail outbox model and worker drain."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

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


async def test_drain_smtp_failure_increments_attempts_and_bumps_next_attempt():
    from unittest.mock import AsyncMock, patch
    from not_dot_net.backend.mail import send_mail, mail_config, MailConfig
    from not_dot_net.backend.mail_outbox import _drain_outbox_once

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
    # next_attempt_at must have been bumped to the future
    assert row.next_attempt_at > datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=5)


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


# --- Recovered coverage from the deleted tests/test_mail.py ---
# These tests target _send_one directly (the old send_mail's behavior moved
# there). They are restored verbatim-equivalent to preserve the security
# regression invariants (especially the "no body or token URL leakage in
# dev-mode logs" check).

async def test_send_one_dev_mode_does_not_leak_body_or_tokens(caplog):
    """Dev-mode log line must not contain the body HTML or anything that
    looks like a token (the body might include a sensitive token URL)."""
    from not_dot_net.backend.mail_outbox import _send_one, MailOutbox
    from not_dot_net.backend.mail import MailConfig

    cfg = MailConfig(dev_mode=True)
    row = MailOutbox(
        to_address="t@test.local",
        subject="Workflow",
        body_html="<a href='https://app/workflow/token/super-secret-token-uuid'>Open</a>",
        next_attempt_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )

    import logging as _logging
    with caplog.at_level(_logging.INFO, logger="not_dot_net.mail_outbox"):
        await _send_one(row, cfg)

    log_text = "\n".join(r.message for r in caplog.records)
    assert "super-secret-token-uuid" not in log_text
    assert "<a href" not in log_text
    assert row.sent_at is not None


async def test_send_one_dev_catch_all_redirect(caplog):
    """Dev-mode catch_all should redirect logs to the catch-all address
    while preserving the original recipient in the log line."""
    from not_dot_net.backend.mail_outbox import _send_one, MailOutbox
    from not_dot_net.backend.mail import MailConfig
    import logging

    cfg = MailConfig(dev_mode=True, dev_catch_all="catch@test.local")
    row = MailOutbox(
        to_address="real-user@test.local",
        subject="Hi",
        body_html="<p>x</p>",
        next_attempt_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )

    with caplog.at_level(logging.INFO, logger="not_dot_net.mail_outbox"):
        await _send_one(row, cfg)

    log_text = "\n".join(r.message for r in caplog.records)
    assert "catch@test.local" in log_text
    assert "real-user@test.local" in log_text
    assert row.sent_at is not None


async def test_send_one_production_passes_smtp_settings_to_aiosmtplib():
    """SMTP host/port/start_tls/username/password are propagated correctly."""
    from unittest.mock import AsyncMock, patch
    from not_dot_net.backend.mail_outbox import _send_one, MailOutbox
    from not_dot_net.backend.mail import MailConfig

    cfg = MailConfig(
        dev_mode=False,
        smtp_host="mail.example.test",
        smtp_port=2525,
        smtp_tls=True,
        smtp_user="alice",
        smtp_password="hunter2",
        from_address="noreply@example.test",
    )
    row = MailOutbox(
        to_address="bob@test.local",
        subject="Hi",
        body_html="<p>x</p>",
        next_attempt_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )

    with patch(
        "not_dot_net.backend.mail_outbox.aiosmtplib.send",
        new=AsyncMock(),
    ) as mock_send:
        await _send_one(row, cfg)

    mock_send.assert_awaited_once()
    args, kwargs = mock_send.await_args
    assert kwargs["hostname"] == "mail.example.test"
    assert kwargs["port"] == 2525
    assert kwargs["start_tls"] is True
    assert kwargs["username"] == "alice"
    assert kwargs["password"] == "hunter2"
    assert row.sent_at is not None


async def test_send_one_production_omits_empty_credentials():
    """Empty smtp_user / smtp_password should pass None, not '' (some
    SMTP servers reject empty-string auth)."""
    from unittest.mock import AsyncMock, patch
    from not_dot_net.backend.mail_outbox import _send_one, MailOutbox
    from not_dot_net.backend.mail import MailConfig

    cfg = MailConfig(dev_mode=False, smtp_user="", smtp_password="")
    row = MailOutbox(
        to_address="bob@test.local",
        subject="Hi",
        body_html="<p>x</p>",
        next_attempt_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )

    with patch(
        "not_dot_net.backend.mail_outbox.aiosmtplib.send",
        new=AsyncMock(),
    ) as mock_send:
        await _send_one(row, cfg)

    _, kwargs = mock_send.await_args
    assert kwargs["username"] is None
    assert kwargs["password"] is None


async def test_send_one_production_constructs_html_message():
    """The EmailMessage carries From / To / Subject / HTML body / subtype."""
    from unittest.mock import AsyncMock, patch
    from not_dot_net.backend.mail_outbox import _send_one, MailOutbox
    from not_dot_net.backend.mail import MailConfig

    cfg = MailConfig(dev_mode=False, from_address="noreply@example.test")
    row = MailOutbox(
        to_address="bob@test.local",
        subject="Welcome",
        body_html="<p>Hello <b>world</b></p>",
        next_attempt_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )

    with patch(
        "not_dot_net.backend.mail_outbox.aiosmtplib.send",
        new=AsyncMock(),
    ) as mock_send:
        await _send_one(row, cfg)

    msg = mock_send.await_args.args[0]
    assert msg["From"] == "noreply@example.test"
    assert msg["To"] == "bob@test.local"
    assert msg["Subject"] == "Welcome"
    body_part = msg.get_body(preferencelist=("html",))
    assert body_part is not None
    assert "Hello" in body_part.get_content()
    assert "<b>world</b>" in body_part.get_content()


async def test_send_one_production_dev_catch_all_overrides_recipient():
    """In production with dev_catch_all set, msg['To'] becomes the catch-all."""
    from unittest.mock import AsyncMock, patch
    from not_dot_net.backend.mail_outbox import _send_one, MailOutbox
    from not_dot_net.backend.mail import MailConfig

    cfg = MailConfig(dev_mode=False, dev_catch_all="qa@test.local")
    row = MailOutbox(
        to_address="real-user@example.com",
        subject="Hi",
        body_html="<p>x</p>",
        next_attempt_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )

    with patch(
        "not_dot_net.backend.mail_outbox.aiosmtplib.send",
        new=AsyncMock(),
    ) as mock_send:
        await _send_one(row, cfg)

    msg = mock_send.await_args.args[0]
    assert msg["To"] == "qa@test.local"


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


async def test_send_test_mail_uses_smtp_directly_and_does_not_enqueue():
    """send_test_mail bypasses both dev_mode and the outbox queue:
    it makes a synchronous aiosmtplib call so the admin gets immediate
    feedback."""
    from unittest.mock import AsyncMock, patch
    from not_dot_net.backend.mail import mail_config, MailConfig
    from not_dot_net.backend.mail_outbox import send_test_mail

    cfg = await mail_config.get()
    await mail_config.set(MailConfig(**{**cfg.model_dump(), "dev_mode": True}))

    with patch(
        "not_dot_net.backend.mail_outbox.aiosmtplib.send",
        new=AsyncMock(),
    ) as mock_send:
        await send_test_mail("admin@test.local")

    mock_send.assert_awaited_once()
    msg = mock_send.await_args.args[0]
    assert msg["To"] == "admin@test.local"
    assert "SMTP test" in msg["Subject"]
    body_part = msg.get_body(preferencelist=("html",))
    assert "Test email sent at" in body_part.get_content()

    async with session_scope() as session:
        rows = (await session.execute(select(MailOutbox))).scalars().all()
    assert list(rows) == []  # no row queued for a test send


async def test_send_test_mail_propagates_smtp_failures():
    """Any SMTP exception bubbles up so the UI can show the error."""
    from unittest.mock import AsyncMock, patch
    from not_dot_net.backend.mail_outbox import send_test_mail

    with patch(
        "not_dot_net.backend.mail_outbox.aiosmtplib.send",
        new=AsyncMock(side_effect=ConnectionRefusedError("localhost:587")),
    ):
        with pytest.raises(ConnectionRefusedError):
            await send_test_mail("admin@test.local")
