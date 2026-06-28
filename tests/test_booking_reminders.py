from datetime import date, timedelta
from unittest.mock import AsyncMock, patch
import uuid

from sqlalchemy import select

from not_dot_net.backend.booking_models import Booking, Resource
from not_dot_net.backend.booking_service import (
    _booking_reminder_context,
    _booking_reminder_delay_label,
    _resource_status_context,
    send_booking_end_reminders,
)
from not_dot_net.backend.db import User, session_scope
from not_dot_net.config import BookingsConfig, bookings_config


async def _create_user(email="user@test.com", full_name="Test User", active=True) -> User:
    async with session_scope() as session:
        user = User(
            id=uuid.uuid4(),
            email=email,
            hashed_password="x",
            full_name=full_name,
            is_active=active,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _create_resource(name="Reminder PC") -> Resource:
    async with session_scope() as session:
        resource = Resource(name=name, resource_type="desktop", location="Palaiseau")
        session.add(resource)
        await session.commit()
        await session.refresh(resource)
        return resource


async def _create_booking(user, resource, *, end_offset_days: int) -> Booking:
    today = date(2026, 5, 26)
    async with session_scope() as session:
        booking = Booking(
            user_id=user.id,
            resource_id=resource.id,
            start_date=today - timedelta(days=3),
            end_date=today + timedelta(days=end_offset_days),
            os_choice="Ubuntu",
            software_tags=["Python", "GCC"],
        )
        session.add(booking)
        await session.commit()
        await session.refresh(booking)
        return booking


def test_booking_reminder_context_raw_values_no_pre_escape():
    """Context builder stores raw strings; Jinja autoescape handles HTML safety at render."""
    class U: full_name = "<script>"; email = "x@test.com"
    class B: start_date = date(2026, 5, 20); end_date = date(2026, 5, 27); \
             os_choice = "<Ubuntu>"; software_tags = ["A&B"]
    class R: name = "<PC>"
    ctx = _booking_reminder_context(user=U(), booking=B(), resource=R(),
                                    delay_label="today",
                                    bookings_url="http://x/?tab=bookings")
    assert ctx["recipient_name"] == "<script>"
    assert ctx["resource_name"] == "<PC>"
    assert ctx["os"] == "<Ubuntu>"
    assert ctx["software"] == "A&B"


def test_booking_reminder_delay_label():
    assert _booking_reminder_delay_label(0) == "today"
    assert _booking_reminder_delay_label(1) == "in 1 day"
    assert _booking_reminder_delay_label(7) == "in 7 days"


async def test_send_booking_end_reminders_queues_mail_and_marks_booking():
    user = await _create_user()
    resource = await _create_resource()
    booking = await _create_booking(user, resource, end_offset_days=1)

    with patch("not_dot_net.backend.booking_service.send_mail", new_callable=AsyncMock) as send:
        queued = await send_booking_end_reminders(today=date(2026, 5, 26))

    assert queued == 1
    send.assert_awaited_once()
    assert send.await_args.args[0] == user.email
    # end_date is the exclusive hand-back day; the user's last day is today
    assert "booking ends today" in send.await_args.args[1]

    async with session_scope() as session:
        stored = await session.get(Booking, booking.id)
        assert stored.reminder_sent_lead_days == [1]


async def test_send_booking_end_reminders_does_not_send_twice():
    user = await _create_user()
    resource = await _create_resource()
    await _create_booking(user, resource, end_offset_days=1)

    with patch("not_dot_net.backend.booking_service.send_mail", new_callable=AsyncMock):
        assert await send_booking_end_reminders(today=date(2026, 5, 26)) == 1

    with patch("not_dot_net.backend.booking_service.send_mail", new_callable=AsyncMock) as send:
        assert await send_booking_end_reminders(today=date(2026, 5, 26)) == 0

    send.assert_not_awaited()


async def test_send_booking_end_reminders_ignores_later_bookings():
    user = await _create_user()
    resource = await _create_resource()
    await _create_booking(user, resource, end_offset_days=3)

    with patch("not_dot_net.backend.booking_service.send_mail", new_callable=AsyncMock) as send:
        queued = await send_booking_end_reminders(today=date(2026, 5, 26))

    assert queued == 0
    send.assert_not_awaited()

    async with session_scope() as session:
        stored = (await session.execute(select(Booking))).scalar_one()
        assert stored.reminder_sent_lead_days is None


async def test_send_booking_end_reminders_uses_configured_lead_days():
    user = await _create_user()
    resource = await _create_resource()
    await _create_booking(user, resource, end_offset_days=8)
    await bookings_config.set(BookingsConfig(reminder_lead_days=[7]))
    try:
        with patch("not_dot_net.backend.booking_service.send_mail", new_callable=AsyncMock) as send:
            queued = await send_booking_end_reminders(today=date(2026, 5, 26))
    finally:
        await bookings_config.reset()

    assert queued == 1
    send.assert_awaited_once()
    assert "booking ends in 7 days" in send.await_args.args[1]


async def test_send_booking_end_reminders_can_be_disabled_with_empty_lead_days():
    user = await _create_user()
    resource = await _create_resource()
    await _create_booking(user, resource, end_offset_days=1)
    await bookings_config.set(BookingsConfig(reminder_lead_days=[]))
    try:
        with patch("not_dot_net.backend.booking_service.send_mail", new_callable=AsyncMock) as send:
            queued = await send_booking_end_reminders(today=date(2026, 5, 26))
    finally:
        await bookings_config.reset()

    assert queued == 0
    send.assert_not_awaited()


async def test_send_booking_end_reminders_zero_lead_days_sends_on_last_day():
    user = await _create_user()
    resource = await _create_resource()
    await _create_booking(user, resource, end_offset_days=1)
    await bookings_config.set(BookingsConfig(reminder_lead_days=[0]))
    try:
        with patch("not_dot_net.backend.booking_service.send_mail", new_callable=AsyncMock) as send:
            queued = await send_booking_end_reminders(today=date(2026, 5, 26))
    finally:
        await bookings_config.reset()

    assert queued == 1
    send.assert_awaited_once()
    assert "booking ends today" in send.await_args.args[1]


async def test_send_booking_end_reminders_supports_multiple_lead_days():
    user = await _create_user()
    resource = await _create_resource()
    booking = await _create_booking(user, resource, end_offset_days=7)
    await bookings_config.set(BookingsConfig(reminder_lead_days=[7, 1]))
    try:
        with patch("not_dot_net.backend.booking_service.send_mail", new_callable=AsyncMock) as send:
            assert await send_booking_end_reminders(today=date(2026, 5, 26)) == 1
            assert await send_booking_end_reminders(today=date(2026, 6, 1)) == 1
    finally:
        await bookings_config.reset()

    assert send.await_count == 2
    async with session_scope() as session:
        stored = await session.get(Booking, booking.id)
        assert stored.reminder_sent_lead_days == [1, 7]


async def test_send_booking_end_reminders_ignores_inactive_users():
    user = await _create_user(active=False)
    resource = await _create_resource()
    await _create_booking(user, resource, end_offset_days=1)

    with patch("not_dot_net.backend.booking_service.send_mail", new_callable=AsyncMock) as send:
        queued = await send_booking_end_reminders(today=date(2026, 5, 26))

    assert queued == 0
    send.assert_not_awaited()


async def test_send_booking_end_reminders_catches_up_missed_lead_day():
    """R-04: if the job was not running on the exact lead day, the reminder
    must still go out on the next run instead of being skipped forever."""
    user = await _create_user()
    resource = await _create_resource()
    booking = await _create_booking(user, resource, end_offset_days=2)
    await bookings_config.set(BookingsConfig(reminder_lead_days=[3]))

    with patch("not_dot_net.backend.booking_service.send_mail", new_callable=AsyncMock) as send:
        queued = await send_booking_end_reminders(today=date(2026, 5, 26))

    assert queued == 1
    send.assert_awaited_once()
    assert "in 1 day" in send.await_args.args[1]

    async with session_scope() as session:
        stored = await session.get(Booking, booking.id)
        assert stored.reminder_sent_lead_days == [3]


async def test_send_booking_end_reminders_sends_one_mail_for_multiple_missed_leads():
    """Catching up on several missed lead days must send a single email."""
    user = await _create_user()
    resource = await _create_resource()
    booking = await _create_booking(user, resource, end_offset_days=1)
    await bookings_config.set(BookingsConfig(reminder_lead_days=[1, 7]))

    with patch("not_dot_net.backend.booking_service.send_mail", new_callable=AsyncMock) as send:
        queued = await send_booking_end_reminders(today=date(2026, 5, 26))

    assert queued == 1
    send.assert_awaited_once()

    async with session_scope() as session:
        stored = await session.get(Booking, booking.id)
        assert stored.reminder_sent_lead_days == [1, 7]


async def test_no_reminder_once_last_usage_day_has_passed():
    """A booking freeing up today (last usage day yesterday) must not remind."""
    user = await _create_user()
    resource = await _create_resource()
    await _create_booking(user, resource, end_offset_days=0)
    await bookings_config.set(BookingsConfig(reminder_lead_days=[0, 1, 7]))

    with patch("not_dot_net.backend.booking_service.send_mail", new_callable=AsyncMock) as send:
        queued = await send_booking_end_reminders(today=date(2026, 5, 26))

    assert queued == 0
    send.assert_not_awaited()


def test_booking_reminder_context_shows_inclusive_last_day():
    class U: full_name = "X"; email = "x@test.com"
    class R: name = "PC"
    class B: start_date = date(2026, 5, 20); end_date = date(2026, 5, 28); \
             os_choice = None; software_tags = None
    ctx = _booking_reminder_context(user=U(), booking=B(), resource=R(),
                                    delay_label="today",
                                    bookings_url="http://x/?tab=bookings")
    assert ctx["end_date"] == "2026-05-27"   # last usage day (exclusive end - 1)
    assert ctx["start_date"] == "2026-05-20"


def test_booking_reminder_context_shape():
    class U: full_name = "Marie Curie"; email = "m@lpp.fr"
    class B: start_date = "2026-07-01"; end_date = date(2026, 7, 10); os_choice = "Ubuntu"; \
             software_tags = ["Python", "MATLAB"]
    class R: name = "Laptop-07"
    ctx = _booking_reminder_context(user=U(), booking=B(), resource=R(),
                                    delay_label="in 3 days",
                                    bookings_url="http://x/?tab=bookings")
    assert ctx["recipient_name"] == "Marie Curie"
    assert ctx["resource_name"] == "Laptop-07"
    assert ctx["software"] == "Python, MATLAB"
    assert ctx["delay_label"] == "in 3 days"
    assert ctx["bookings_url"] == "http://x/?tab=bookings"


def test_resource_status_context_defaults_blank_location():
    class U: full_name = ""; email = "m@lpp.fr"
    class R: name = "Laptop-07"; location = None
    ctx = _resource_status_context(user=U(), resource=R(),
                                   subject_line="Ready", headline="It is ready.",
                                   bookings_url="http://x/?tab=bookings")
    assert ctx["recipient_name"] == "m@lpp.fr"   # falls back to email
    assert ctx["location"] == "-"
    assert ctx["subject_line"] == "Ready"
