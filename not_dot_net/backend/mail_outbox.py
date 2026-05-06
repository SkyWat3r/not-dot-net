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
