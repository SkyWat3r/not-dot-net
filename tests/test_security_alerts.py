import asyncio
import uuid
from unittest.mock import AsyncMock, patch

from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.mail import MailConfig
from not_dot_net.backend.security_alerts import (
    _BACKGROUND_ALERT_TASKS,
    get_security_alert_recipients,
    queue_security_alert,
    render_security_alert_body,
    send_security_alert,
)


async def _create_user(
    email: str,
    *,
    is_superuser: bool = False,
    is_active: bool = True,
) -> User:
    async with session_scope() as session:
        user = User(
            id=uuid.uuid4(),
            email=email,
            hashed_password="x",
            is_superuser=is_superuser,
            is_active=is_active,
        )
        session.add(user)
        await session.commit()
        return user


async def test_security_alert_recipients_include_only_active_superusers():
    await _create_user("root@test.com", is_superuser=True)
    await _create_user("disabled@test.com", is_superuser=True, is_active=False)
    await _create_user("regular@test.com", is_superuser=False)

    recipients = await get_security_alert_recipients()

    assert recipients == ["root@test.com"]


async def test_send_security_alert_uses_existing_mail_sender_for_each_recipient():
    await _create_user("root@test.com", is_superuser=True)
    mail_settings = MailConfig(dev_mode=True)

    with patch(
        "not_dot_net.backend.security_alerts.send_mail",
        new=AsyncMock(),
    ) as send_mail_mock:
        recipients = await send_security_alert(
            "[not-dot-net] Security alert",
            "<p>body</p>",
            mail_settings=mail_settings,
        )

    assert recipients == ["root@test.com"]
    assert send_mail_mock.await_count == 1
    send_mail_mock.assert_any_await(
        "root@test.com",
        "[not-dot-net] Security alert",
        "<p>body</p>",
        mail_settings,
    )


async def test_subject_prefix_uses_org_app_name():
    from not_dot_net.backend.security_alerts import _subject
    from not_dot_net.config import org_config

    cfg = await org_config.get()
    cfg.app_name = "LPP Intranet"
    await org_config.set(cfg)

    assert await _subject("Security alert: x") == "[LPP Intranet] Security alert: x"


async def test_subject_prefix_falls_back_when_app_name_blank():
    from not_dot_net.backend.security_alerts import _subject
    from not_dot_net.config import org_config

    cfg = await org_config.get()
    cfg.app_name = "   "
    await org_config.set(cfg)

    assert await _subject("hello") == "[not-dot-net] hello"


def test_render_security_alert_body_escapes_values():
    body = render_security_alert_body(
        "A superuser account has logged in.",
        [("Account", "<admin@test.com>"), ("IP address", "10.0.0.42")],
        "Review the audit log if this was not expected.",
    )

    assert "&lt;admin@test.com&gt;" in body
    assert "10.0.0.42" in body
    assert "Review the audit log" in body


async def test_queue_security_alert_keeps_task_until_completion():
    started = asyncio.Event()
    release = asyncio.Event()

    async def pending_alert():
        started.set()
        await release.wait()

    queue_security_alert(pending_alert())
    await started.wait()

    assert len(_BACKGROUND_ALERT_TASKS) == 1
    release.set()
    for _ in range(5):
        await asyncio.sleep(0)
        if not _BACKGROUND_ALERT_TASKS:
            break
    assert _BACKGROUND_ALERT_TASKS == set()


def test_queue_security_alert_logs_schedule_failure():
    async def alert():
        return None

    with (
        patch(
            "not_dot_net.backend.security_alerts.asyncio.create_task",
            side_effect=RuntimeError("no running event loop"),
        ),
        patch("not_dot_net.backend.security_alerts.logger.exception") as log_mock,
    ):
        queue_security_alert(alert())

    log_mock.assert_called_once_with("Failed to schedule security alert background task")

async def test_cli_promote_emits_superuser_grant_alert():
    from not_dot_net.cli import _set_superuser

    user = await _create_user("cli-promote@test.com")

    with (
        patch("not_dot_net.backend.db.init_db"),
        patch(
            "not_dot_net.backend.security_alerts.notify_superuser_granted",
            new=AsyncMock(return_value=["root@test.com"]),
        ) as notify_mock,
    ):
        await _set_superuser(user.email, True)

    notify_mock.assert_awaited_once()
    args, kwargs = notify_mock.await_args
    assert args[0].email == user.email
    assert kwargs == {"actor_email": "cli"}


async def test_cli_revoke_does_not_emit_superuser_grant_alert():
    from not_dot_net.cli import _set_superuser

    user = await _create_user("cli-revoke@test.com", is_superuser=True)

    with (
        patch("not_dot_net.backend.db.init_db"),
        patch(
            "not_dot_net.backend.security_alerts.notify_superuser_granted",
            new=AsyncMock(return_value=["root@test.com"]),
        ) as notify_mock,
    ):
        await _set_superuser(user.email, False)

    notify_mock.assert_not_awaited()
