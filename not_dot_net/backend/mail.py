"""Async mail sending with dev-mode logging."""

from email.message import EmailMessage

import aiosmtplib

from not_dot_net.config import MailSettings


async def send_mail(
    to: str,
    subject: str,
    body_html: str,
    mail_settings: MailSettings,
) -> None:
    effective_to = to
    if mail_settings.dev_catch_all:
        effective_to = mail_settings.dev_catch_all

    if mail_settings.dev_mode:
        print(f"[MAIL dev] To: {effective_to} (original: {to})")
        print(f"[MAIL dev] Subject: {subject}")
        print(f"[MAIL dev] Body: {body_html[:200]}")
        return

    msg = EmailMessage()
    msg["From"] = mail_settings.from_address
    msg["To"] = effective_to
    msg["Subject"] = subject
    msg.set_content(body_html, subtype="html")

    await aiosmtplib.send(
        msg,
        hostname=mail_settings.smtp_host,
        port=mail_settings.smtp_port,
        start_tls=mail_settings.smtp_tls,
        username=mail_settings.smtp_user or None,
        password=mail_settings.smtp_password or None,
    )
