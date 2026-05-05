"""Security alert emails for critical audit/security events."""

import asyncio
import logging
from collections.abc import Sequence
from datetime import datetime, timezone
from html import escape
from typing import Any

from sqlalchemy import select

from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.mail import MailConfig, mail_config, send_mail

logger = logging.getLogger("not_dot_net.security_alerts")
_BACKGROUND_ALERT_TASKS: set[asyncio.Task[Any]] = set()


def _clean_email(email: str | None) -> str | None:
    if not email:
        return None
    cleaned = email.strip().lower()
    if "@" not in cleaned:
        return None
    return cleaned


async def get_security_alert_recipients() -> list[str]:
    """Return active superuser emails."""
    recipients = set()

    async with session_scope() as session:
        result = await session.execute(
            select(User.email).where(
                User.is_superuser.is_(True),
                User.is_active.is_(True),
            )
        )
        for email in result.scalars().all():
            cleaned = _clean_email(email)
            if cleaned:
                recipients.add(cleaned)

    return sorted(recipients)


def render_security_alert_body(
    title: str,
    fields: Sequence[tuple[str, object | None]],
    message: str,
) -> str:
    """Render a compact HTML body for security alert emails."""
    rows = "\n".join(
        "<tr>"
        f"<td><strong>{escape(label)}</strong></td>"
        f"<td>{escape(str(value)) if value is not None else '-'}</td>"
        "</tr>"
        for label, value in fields
    )
    return (
        f"<p>{escape(title)}</p>"
        "<table>"
        f"{rows}"
        "</table>"
        f"<p>{escape(message)}</p>"
    )


async def send_security_alert(
    subject: str,
    body_html: str,
    *,
    mail_settings: MailConfig | None = None,
) -> list[str]:
    """Send one security alert to every configured security recipient."""
    cfg = mail_settings or await mail_config.get()
    recipients = await get_security_alert_recipients()
    for email in recipients:
        await send_mail(email, subject, body_html, cfg)
    return recipients


def queue_security_alert(coro) -> None:
    """Schedule a best-effort alert without blocking the active request."""
    try:
        task = asyncio.create_task(coro)
    except Exception:
        close = getattr(coro, "close", None)
        if close is not None:
            close()
        logger.exception("Failed to schedule security alert background task")
        return

    _BACKGROUND_ALERT_TASKS.add(task)

    def _log_failure(done_task: asyncio.Task) -> None:
        _BACKGROUND_ALERT_TASKS.discard(done_task)
        try:
            done_task.result()
        except Exception:
            logger.exception("Unhandled security alert background task failure")

    task.add_done_callback(_log_failure)


async def notify_superuser_login_success(
    user: User,
    *,
    ip: str | None,
    user_agent: str | None,
) -> list[str]:
    """Notify security recipients that a superuser successfully logged in."""
    subject = "[not-dot-net] Security alert: superuser login"
    body = render_security_alert_body(
        "A superuser account has logged in.",
        [
            ("Account", user.email),
            ("Role", user.role or "(none)"),
            ("IP address", ip or "unknown"),
            ("User agent", user_agent or "-"),
            ("Time", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")),
        ],
        "If this login was expected, no action is required. "
        "If this login was not expected, review the audit log immediately.",
    )
    try:
        return await send_security_alert(subject, body)
    except Exception:
        logger.exception("Failed to send superuser login security alert for %s", user.email)
        return []


async def notify_superuser_login_failed(
    user: User,
    *,
    ip: str | None,
    user_agent: str | None,
) -> list[str]:
    """Notify security recipients that a superuser login failed."""
    subject = "[not-dot-net] Security alert: superuser login failed"
    body = render_security_alert_body(
        "A failed login attempt targeted a superuser account.",
        [
            ("Account", user.email),
            ("IP address", ip or "unknown"),
            ("User agent", user_agent or "-"),
            ("Time", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")),
        ],
        "This may be a mistyped password, but it can also indicate an attempt "
        "to access a privileged account. Review the audit log if this was not expected.",
    )
    try:
        return await send_security_alert(subject, body)
    except Exception:
        logger.exception(
            "Failed to send superuser failed-login security alert for %s", user.email
        )
        return []


async def notify_superuser_granted(
    user: User,
    *,
    actor_email: str | None = None,
) -> list[str]:
    """Notify security recipients that a user was granted superuser privileges."""
    subject = "[not-dot-net] Security alert: is_superuser tag granted"
    body = render_security_alert_body(
        "A user has been assigned the tag is_superuser.",
        [
            ("Updated user", user.email),
            ("Updated by", actor_email or "-"),
            ("Time", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")),
        ],
        "This action grants high privileges. Review the audit log if this change "
        "was not expected.",
    )
    try:
        return await send_security_alert(subject, body)
    except Exception:
        logger.exception(
            "Failed to send is_superuser granted security alert for %s", user.email
        )
        return []
