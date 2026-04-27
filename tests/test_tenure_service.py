import pytest
import uuid
from datetime import date
from contextlib import asynccontextmanager

from not_dot_net.backend.db import User, get_async_session
from not_dot_net.backend.tenure_service import (
    UserTenure,
    add_tenure,
    close_tenure,
    list_tenures,
    current_tenure,
    avg_duration_by_status,
    headcount_at_date,
    update_tenure,
    delete_tenure,
)


async def _create_user(email="test@lpp.fr") -> User:
    get_session = asynccontextmanager(get_async_session)
    async with get_session() as session:
        user = User(id=uuid.uuid4(), email=email, hashed_password="x", role="staff")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def test_add_tenure():
    user = await _create_user()
    tenure = await add_tenure(
        user_id=user.id,
        status="Intern",
        employer="CNRS",
        start_date=date(2026, 3, 1),
        end_date=date(2026, 8, 31),
    )
    assert tenure.status == "Intern"
    assert tenure.employer == "CNRS"
    assert tenure.start_date == date(2026, 3, 1)
    assert tenure.end_date == date(2026, 8, 31)


async def test_add_open_tenure():
    user = await _create_user()
    tenure = await add_tenure(
        user_id=user.id,
        status="PhD",
        employer="Sorbonne Université",
        start_date=date(2026, 9, 1),
    )
    assert tenure.end_date is None


async def test_current_tenure_returns_latest_open():
    user = await _create_user()
    await add_tenure(
        user_id=user.id, status="Intern", employer="CNRS",
        start_date=date(2025, 3, 1), end_date=date(2025, 8, 31),
    )
    await add_tenure(
        user_id=user.id, status="PhD", employer="Polytechnique",
        start_date=date(2025, 9, 1),
    )
    cur = await current_tenure(user.id)
    assert cur is not None
    assert cur.status == "PhD"
    assert cur.employer == "Polytechnique"


async def test_current_tenure_none_when_all_closed():
    user = await _create_user()
    await add_tenure(
        user_id=user.id, status="Intern", employer="CNRS",
        start_date=date(2025, 1, 1), end_date=date(2025, 6, 30),
    )
    assert await current_tenure(user.id) is None


async def test_close_tenure():
    user = await _create_user()
    tenure = await add_tenure(
        user_id=user.id, status="PhD", employer="CNRS",
        start_date=date(2025, 9, 1),
    )
    closed = await close_tenure(tenure.id, end_date=date(2026, 8, 31))
    assert closed.end_date == date(2026, 8, 31)


async def test_list_tenures_ordered():
    user = await _create_user()
    await add_tenure(
        user_id=user.id, status="Intern", employer="CNRS",
        start_date=date(2024, 3, 1), end_date=date(2024, 8, 31),
    )
    await add_tenure(
        user_id=user.id, status="PhD", employer="Polytechnique",
        start_date=date(2024, 9, 1),
    )
    tenures = await list_tenures(user.id)
    assert len(tenures) == 2
    assert tenures[0].start_date < tenures[1].start_date


async def test_avg_duration_by_status():
    u1 = await _create_user("a@lpp.fr")
    u2 = await _create_user("b@lpp.fr")
    await add_tenure(user_id=u1.id, status="PhD", employer="CNRS",
                     start_date=date(2022, 9, 1), end_date=date(2025, 8, 31))
    await add_tenure(user_id=u2.id, status="PhD", employer="Polytechnique",
                     start_date=date(2023, 9, 1), end_date=date(2026, 8, 31))
    stats = await avg_duration_by_status()
    assert "PhD" in stats
    assert stats["PhD"]["count"] == 2
    assert stats["PhD"]["avg_days"] > 0


async def test_headcount_at_date():
    u1 = await _create_user("c@lpp.fr")
    u2 = await _create_user("d@lpp.fr")
    await add_tenure(user_id=u1.id, status="Intern", employer="CNRS",
                     start_date=date(2025, 3, 1), end_date=date(2025, 8, 31))
    await add_tenure(user_id=u2.id, status="PhD", employer="CNRS",
                     start_date=date(2025, 1, 1))
    count = await headcount_at_date(date(2025, 6, 1))
    assert count == 2
    count_after = await headcount_at_date(date(2025, 10, 1))
    assert count_after == 1


async def test_update_tenure():
    user = await _create_user("e@lpp.fr")
    tenure = await add_tenure(
        user_id=user.id, status="Intern", employer="CNRS",
        start_date=date(2025, 3, 1),
    )
    updated = await update_tenure(tenure.id, status="PhD", employer="Polytechnique")
    assert updated.status == "PhD"
    assert updated.employer == "Polytechnique"
    assert updated.start_date == date(2025, 3, 1)


async def test_delete_tenure():
    user = await _create_user("f@lpp.fr")
    tenure = await add_tenure(
        user_id=user.id, status="Intern", employer="CNRS",
        start_date=date(2025, 3, 1),
    )
    await delete_tenure(tenure.id)
    tenures = await list_tenures(user.id)
    assert len(tenures) == 0
