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
