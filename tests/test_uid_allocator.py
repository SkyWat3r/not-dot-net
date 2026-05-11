import uuid
import pytest
from sqlalchemy import select

from not_dot_net.backend.db import session_scope, User, AuthMethod
from not_dot_net.backend.uid_allocator import (
    allocate_uid, UidAllocation, UidRangeExhausted,
)
from not_dot_net.backend.ad_account_config import ad_account_config


async def _make_user(email: str = "u@example.com") -> uuid.UUID:
    async with session_scope() as session:
        u = User(
            email=email,
            full_name="U",
            hashed_password="x",
            auth_method=AuthMethod.LOCAL,
            role="",
            is_active=True,
        )
        session.add(u)
        await session.commit()
        await session.refresh(u)
        return u.id


@pytest.mark.asyncio
async def test_allocate_uid_empty_returns_uid_min():
    uid_user = await _make_user()
    uid = await allocate_uid(uid_user, "alpha")
    assert uid == 10000


@pytest.mark.asyncio
async def test_allocate_uid_fills_smallest_gap():
    uid_user = await _make_user()
    async with session_scope() as session:
        session.add(UidAllocation(uid=10000, source="allocated", sam_account="a"))
        session.add(UidAllocation(uid=10002, source="allocated", sam_account="b"))
        await session.commit()
    uid = await allocate_uid(uid_user, "c")
    assert uid == 10001


@pytest.mark.asyncio
async def test_allocate_uid_contiguous_returns_max_plus_one():
    uid_user = await _make_user()
    async with session_scope() as session:
        for n in (10000, 10001, 10002):
            session.add(UidAllocation(uid=n, source="allocated", sam_account=f"u{n}"))
        await session.commit()
    uid = await allocate_uid(uid_user, "z")
    assert uid == 10003


@pytest.mark.asyncio
async def test_allocate_uid_range_exhausted_raises():
    uid_user = await _make_user()
    cfg = await ad_account_config.get()
    await ad_account_config.set(cfg.model_copy(update={"uid_min": 10, "uid_max": 11}))
    async with session_scope() as session:
        session.add(UidAllocation(uid=10, source="allocated", sam_account="a"))
        session.add(UidAllocation(uid=11, source="allocated", sam_account="b"))
        await session.commit()
    with pytest.raises(UidRangeExhausted):
        await allocate_uid(uid_user, "c")


@pytest.mark.asyncio
async def test_allocate_uid_writes_row_with_metadata():
    uid_user = await _make_user("metadata@example.com")
    uid = await allocate_uid(uid_user, "metaman")
    async with session_scope() as session:
        row = (await session.execute(select(UidAllocation).where(UidAllocation.uid == uid))).scalar_one()
    assert row.source == "allocated"
    assert row.user_id == uid_user
    assert row.sam_account == "metaman"


@pytest.mark.asyncio
async def test_allocate_uid_writes_audit_event():
    from not_dot_net.backend.audit import list_audit_events
    uid_user = await _make_user("audit@example.com")
    uid = await allocate_uid(uid_user, "audited")
    events = await list_audit_events(category="ad", action="allocate_uid")
    assert any(ev.target_id == str(uid_user) and str(uid) in str(ev.detail) for ev in events)
