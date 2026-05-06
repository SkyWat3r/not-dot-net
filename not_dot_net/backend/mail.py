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
