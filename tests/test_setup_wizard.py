import pytest
from not_dot_net.backend.db import User, session_scope


async def test_has_superuser_returns_false_when_no_users():
    from not_dot_net.frontend.setup_wizard import has_superuser
    assert await has_superuser() is False


async def test_has_superuser_returns_false_when_no_one_is_superuser():
    from not_dot_net.frontend.setup_wizard import has_superuser

    async with session_scope() as session:
        session.add(User(email="member@test.dev", hashed_password="x", role="member"))
        session.add(User(email="staff@test.dev", hashed_password="x", role="staff"))
        await session.commit()

    assert await has_superuser() is False


async def test_has_superuser_returns_true_after_admin_created():
    from not_dot_net.frontend.setup_wizard import has_superuser
    from not_dot_net.backend.users import ensure_default_admin
    await ensure_default_admin("admin@test.dev", "password")
    assert await has_superuser() is True


async def test_has_superuser_returns_true_when_at_least_one_superuser_exists():
    from not_dot_net.frontend.setup_wizard import has_superuser

    async with session_scope() as session:
        session.add(User(email="member@test.dev", hashed_password="x", role="member"))
        session.add(User(
            email="su@test.dev", hashed_password="x",
            role="", is_superuser=True,
        ))
        await session.commit()

    assert await has_superuser() is True
