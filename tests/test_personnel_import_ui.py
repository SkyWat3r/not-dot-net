"""Test the super-user-only personnel-CSV import upload handler in Settings."""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from not_dot_net.backend.db import User, get_async_session
from not_dot_net.frontend.admin_settings import _handle_personnel_import_upload

CLEAN_CSV = (
    "first_name,last_name,email,employer,team,status,start_date,end_date,notes\n"
    "Marie,Dupont,marie.dupont@lpp.fr,CNRS,Spatiaux,Intern,2015-03-01,2015-08-31,\n"
)

SUPERUSER = SimpleNamespace(
    id="00000000-0000-0000-0000-000000000000", email="root@test.local", is_superuser=True
)
PLAIN_ADMIN = SimpleNamespace(
    id="00000000-0000-0000-0000-000000000001", email="admin@test.local", is_superuser=False
)


@dataclass
class FakeCsvUpload:
    _data: bytes
    name: str = "personnel.csv"

    async def read(self) -> bytes:
        return self._data


@dataclass
class FakeUploadEvent:
    file: FakeCsvUpload


def _event(text: str) -> FakeUploadEvent:
    return FakeUploadEvent(file=FakeCsvUpload(text.encode("utf-8")))


@pytest.fixture
def ui_mocks():
    with (
        patch("not_dot_net.frontend.admin_settings.ui") as mock_ui,
        patch("not_dot_net.frontend.admin_settings.log_audit", new_callable=AsyncMock),
        patch("not_dot_net.frontend.admin_settings.t", side_effect=lambda k, **kw: k),
    ):
        yield mock_ui


async def _all_users():
    get_session = asynccontextmanager(get_async_session)
    async with get_session() as session:
        return (await session.execute(select(User))).scalars().all()


async def test_superuser_import_creates_inactive_users(ui_mocks):
    await _handle_personnel_import_upload(_event(CLEAN_CSV), user=SUPERUSER)

    users = await _all_users()
    assert len(users) == 1
    assert users[0].email == "marie.dupont@lpp.fr"
    assert users[0].is_active is False
    assert "positive" in str(ui_mocks.notify.call_args)


async def test_import_rejects_invalid_csv(ui_mocks):
    bad = "first_name,last_name,status,start_date\nMarie,Dupont,Intern,not-a-date\n"
    await _handle_personnel_import_upload(_event(bad), user=SUPERUSER)

    assert await _all_users() == []
    assert "negative" in str(ui_mocks.notify.call_args)


async def test_import_forbidden_for_non_superuser(ui_mocks):
    with patch(
        "not_dot_net.frontend.admin_settings.import_personnel", new_callable=AsyncMock
    ) as imp:
        await _handle_personnel_import_upload(_event(CLEAN_CSV), user=PLAIN_ADMIN)

    imp.assert_not_awaited()
    assert await _all_users() == []
    assert "negative" in str(ui_mocks.notify.call_args)
