"""Tests for the mail outbox model and worker drain."""
import uuid
from datetime import datetime, timezone

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
