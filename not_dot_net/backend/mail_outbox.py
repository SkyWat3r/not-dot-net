"""Durable mail outbox: enqueue + background worker that drains it.

`backend.mail.send_mail` writes rows here; `run_outbox_worker` (started
in `app.startup`) sends them with capped exponential backoff. Single
in-process worker; not safe across multiple replicas.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import aiosmtplib
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
    __table_args__ = (
        Index("ix_mail_outbox_pending", "sent_at", "failed_at", "next_attempt_at"),
        Index("ix_mail_outbox_failed_at", "failed_at"),
    )

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
