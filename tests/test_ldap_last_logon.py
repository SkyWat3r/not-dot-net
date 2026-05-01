"""AD lastLogonTimestamp → User.last_ad_logon."""
from datetime import datetime, timedelta, timezone

import pytest

from not_dot_net.backend.auth.ldap import (
    LdapUserInfo,
    _attr_filetime,
    _entry_to_user_info,
    sync_user_from_ldap,
)
from not_dot_net.backend.db import AuthMethod, User, session_scope


_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


def _filetime_for(dt: datetime) -> int:
    """Convert a UTC datetime to an AD FILETIME (100-ns intervals since 1601)."""
    delta = dt - _FILETIME_EPOCH
    return int(delta.total_seconds() * 10_000_000)


class _Attr:
    def __init__(self, value):
        self.value = value


class _Entry:
    """Minimal stand-in for an ldap3 Entry."""
    def __init__(self, **attrs):
        self.entry_dn = attrs.pop("entry_dn", "cn=x,dc=example,dc=com")
        for k, v in attrs.items():
            setattr(self, k, _Attr(v))


def test_attr_filetime_parses_int():
    expected = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    entry = _Entry(lastLogonTimestamp=_filetime_for(expected))
    parsed = _attr_filetime(entry, "lastLogonTimestamp")
    # Allow ~1ms tolerance from microsecond truncation
    assert parsed is not None
    assert abs((parsed - expected).total_seconds()) < 1


def test_attr_filetime_parses_datetime_passthrough():
    """ldap3 sometimes auto-converts FILETIME → datetime; should pass through (UTC-tagged)."""
    naive = datetime(2026, 4, 25, 12, 0)
    entry = _Entry(lastLogonTimestamp=naive)
    parsed = _attr_filetime(entry, "lastLogonTimestamp")
    assert parsed == naive.replace(tzinfo=timezone.utc)


def test_attr_filetime_returns_none_for_never():
    entry = _Entry(lastLogonTimestamp=0)
    assert _attr_filetime(entry, "lastLogonTimestamp") is None
    entry2 = _Entry(lastLogonTimestamp=9223372036854775807)
    assert _attr_filetime(entry2, "lastLogonTimestamp") is None


def test_attr_filetime_returns_none_when_missing():
    entry = _Entry()  # no attribute at all
    assert _attr_filetime(entry, "lastLogonTimestamp") is None


def test_entry_to_user_info_includes_last_logon():
    expected = datetime(2026, 3, 1, 9, 30, tzinfo=timezone.utc)
    entry = _Entry(
        mail="u@example.com",
        displayName="U",
        lastLogonTimestamp=_filetime_for(expected),
    )
    info = _entry_to_user_info(entry)
    assert info is not None
    assert info.last_logon_timestamp is not None
    assert abs((info.last_logon_timestamp - expected).total_seconds()) < 1


async def test_sync_persists_last_ad_logon():
    async with session_scope() as session:
        user = User(
            email="ll@example.com", hashed_password="x", is_active=True,
            auth_method=AuthMethod.LDAP,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        user_id = user.id

    when = datetime(2026, 4, 1, 8, 0, tzinfo=timezone.utc)
    info = LdapUserInfo(
        email="ll@example.com", dn="cn=ll,dc=example,dc=com",
        last_logon_timestamp=when,
    )
    await sync_user_from_ldap(user_id, info)

    async with session_scope() as session:
        refreshed = await session.get(User, user_id)
        assert refreshed.last_ad_logon is not None
        # SQLite may strip tzinfo; compare as naive UTC if needed.
        stored = refreshed.last_ad_logon
        if stored.tzinfo is None:
            stored = stored.replace(tzinfo=timezone.utc)
        assert stored == when


async def test_sync_clears_last_ad_logon_when_absent():
    """When AD returns no lastLogonTimestamp (never logged in), the column resets to NULL."""
    async with session_scope() as session:
        user = User(
            email="cl@example.com", hashed_password="x", is_active=True,
            auth_method=AuthMethod.LDAP,
            last_ad_logon=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        session.add(user)
        await session.commit()
        user_id = user.id

    info = LdapUserInfo(
        email="cl@example.com", dn="cn=cl,dc=example,dc=com",
        last_logon_timestamp=None,
    )
    await sync_user_from_ldap(user_id, info)

    async with session_scope() as session:
        refreshed = await session.get(User, user_id)
        assert refreshed.last_ad_logon is None
