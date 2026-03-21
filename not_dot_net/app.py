#!/usr/bin/env python3
"""This is just a simple authentication example.

Please see the `OAuth2 example at FastAPI <https://fastapi.tiangolo.com/tutorial/security/simple-oauth2/>`_  or
use the great `Authlib package <https://docs.authlib.org/en/v0.13/client/starlette.html#using-fastapi>`_ to implement a classing real authentication system.
Here we just demonstrate the NiceGUI integration.
"""

from typing import Optional
from nicegui import app, ui
from not_dot_net.config import load_settings
from not_dot_net.frontend import load as load_frontend
from not_dot_net.backend.app import NotDotNetApp
from anyio import run
from contextlib import asynccontextmanager


@asynccontextmanager
async def get_user_manager(db_path: str, create_db_and_tables: bool = False):
    from not_dot_net.backend.db import get_db
    from not_dot_net.backend.users import get_authentication_backend

    db = get_db(db_path)
    if create_db_and_tables:
        await db.create_db_and_tables()
    backend = get_authentication_backend(db)
    get_async_session_context = asynccontextmanager(db.get_async_session)
    get_user_db_context = asynccontextmanager(db.get_user_db)
    get_user_manager_context = asynccontextmanager(backend.get_user_manager)

    async with get_async_session_context() as session:
        async with get_user_db_context(session) as user_db:
            async with get_user_manager_context(user_db) as user_manager:
                yield user_manager


async def create_user(
    username: str, password: str, config_file: Optional[str] = None
) -> None:
    settings = load_settings(config_file=config_file)
    async with get_user_manager(
        settings.backend.database_url, create_db_and_tables=True
    ) as user_manager:
        from not_dot_net.backend.schemas import UserCreate

        user_create = UserCreate(
            email=username,
            password=password,
            is_active=True,
            is_superuser=False,
        )

        user = await user_manager.create(user_create)
        print(f"User '{user.email}' created successfully.")


class App:
    def __init__(self, host: str, port: int, config_file: Optional[str]) -> None:
        self.host = host
        self.port = port
        self.settings = settings = load_settings(config_file=config_file)
        self.ndtapp = ndtapp = NotDotNetApp(app, settings.backend.database_url)
        load_frontend(ndtapp)
        self.ndtapp.register_routes(app)


@ui.page("/")
def main_page() -> None:
    with ui.header().classes(replace="row items-center") as header:
        ui.button(on_click=lambda: left_drawer.toggle(), icon="menu").props(
            "flat color=white"
        )
        with ui.tabs() as tabs:
            ui.tab("A")
            ui.tab("B")
            ui.tab("C")

    with ui.footer(value=False) as footer:
        ui.label("Footer")

    with ui.left_drawer().classes("bg-blue-100") as left_drawer:
        ui.label("Side menu")

    with ui.page_sticky(position="bottom-right", x_offset=20, y_offset=20):
        ui.button(on_click=footer.toggle, icon="contact_support").props("fab")

    with ui.tab_panels(tabs, value="A").classes("w-full"):
        with ui.tab_panel("A"):
            ui.label("Content of A")
        with ui.tab_panel("B"):
            ui.label("Content of B")
        with ui.tab_panel("C"):
            ui.label("Content of C")


def main(
    host: str = "localhost",
    port: int = 8000,
    env_file: Optional[str] = None,
    reload=False,
) -> None:
    _app = App(host, port, env_file)
    ui.run(
        storage_secret="test", host=host, port=port, reload=reload, title="NotDotNet"
    )


if __name__ in {"__main__", "__mp_main__"}:
    main("localhost", 8000, None)
