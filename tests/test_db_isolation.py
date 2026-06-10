"""The NiceGUI user fixture executes not_dot_net/app.py main(), whose
init_db() must not rebind the engine to ./dev.db — otherwise every
user-fixture test silently reads and writes the developer's database."""

from nicegui.testing import User


async def test_user_fixture_keeps_in_memory_db(user: User):
    import not_dot_net.backend.db as db_module

    assert "memory" in str(db_module._engine.url), (
        f"user-fixture tests must run on the in-memory test DB, "
        f"not {db_module._engine.url}"
    )


async def test_user_fixture_startup_does_not_touch_test_db(user: User):
    """The app's dev startup (create_all + default admin seeding) must be
    disabled under tests: it ran as a background task per user-fixture test,
    racing test teardown (refresh on a disposed engine → spurious ERROR logs)
    and writing into per-test databases mid-test."""
    import asyncio

    from sqlalchemy import select

    from not_dot_net.backend.db import User as DbUser, session_scope

    await user.open("/login")
    await asyncio.sleep(0.3)  # give any (unwanted) startup task time to run

    async with session_scope() as session:
        admins = (await session.execute(
            select(DbUser).where(DbUser.email == "admin@not-dot-net.dev")
        )).scalars().all()
    assert admins == []
