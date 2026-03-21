from typing import Optional

from nicegui import app, ui

from not_dot_net.config import init_settings
from not_dot_net.backend.db import init_db, create_db_and_tables
from not_dot_net.backend.users import fastapi_users, jwt_backend, cookie_backend, ensure_default_admin
from not_dot_net.backend.schemas import UserRead, UserUpdate
from not_dot_net.backend.auth import router as auth_router
from not_dot_net.backend.onboarding_router import router as onboarding_router
from not_dot_net.frontend.login import setup as setup_login
from not_dot_net.frontend.shell import setup as setup_shell


def create_app(config_file: str | None = None):
    settings = init_settings(config_file)
    init_db(settings.backend.database_url)

    async def startup():
        await create_db_and_tables()
        await ensure_default_admin()

    app.on_startup(startup)

    app.include_router(
        fastapi_users.get_auth_router(jwt_backend),
        prefix="/auth/jwt",
        tags=["auth"],
    )
    app.include_router(
        fastapi_users.get_auth_router(cookie_backend),
        prefix="/auth/cookie",
        tags=["auth"],
    )
    app.include_router(
        fastapi_users.get_users_router(UserRead, UserUpdate),
        prefix="/users",
        tags=["users"],
    )
    app.include_router(auth_router)
    app.include_router(onboarding_router)

    setup_login()
    setup_shell()


def main(
    host: str = "localhost",
    port: int = 8088,
    env_file: Optional[str] = None,
    reload=False,
) -> None:
    create_app(env_file)
    from not_dot_net.config import get_settings
    ui.run(
        storage_secret=get_settings().storage_secret,
        host=host, port=port, reload=reload, title="NotDotNet",
    )


if __name__ in {"__main__", "__mp_main__"}:
    main("localhost", 8088, None)
