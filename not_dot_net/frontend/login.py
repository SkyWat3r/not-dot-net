# python
# File: `not_dot_net/frontend/login.py`
from typing import Optional
from fastapi.responses import RedirectResponse
from contextlib import asynccontextmanager

from nicegui import app, ui

from not_dot_net.backend.app import NotDotNetApp
from not_dot_net.backend.db import User
from not_dot_net.backend.users.users import UserManager
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi.security import OAuth2PasswordRequestForm
from .register import register_frontend_loader


@register_frontend_loader
def load(ndtapp: NotDotNetApp):
    @ui.page("/login")
    def login(redirect_to: str = "/user/profile") -> Optional[RedirectResponse]:
        async def try_login() -> None:
            try:
                get_session_context = asynccontextmanager(ndtapp.db.get_async_session)
                get_user_db_context = asynccontextmanager(ndtapp.db.get_user_db)
                get_user_manager_context = asynccontextmanager(ndtapp.auth_backends.get_user_manager)
                async with get_session_context() as session:
                    async with get_user_db_context(session) as user_db:
                        async with get_user_manager_context(user_db) as user_manager:
                            credentials = OAuth2PasswordRequestForm(
                                username=email.value,
                                password=password.value,
                                scope="",
                                grant_type="password"
                            )
                            user = await user_manager.authenticate(credentials)
                            
                            if user is None or not user.is_active:
                                ui.notify('Invalid email or password', color='negative')
                                return
                            
                            strategy = ndtapp.auth_backends.cookie_auth_backend.get_strategy()
                            token = await strategy.write_token(user)

                            js_code = f'document.cookie = "fastapiusersauth={token}; path=/; SameSite=Lax"; window.location.href = "{redirect_to}";'

                            ui.run_javascript(js_code)
                            
                            ui.notify("Logged in", color="positive")
                            ui.notify(f'Welcome, {user.email}', color="positive")
                            return

            except Exception:
                import traceback

                ui.notify("Auth server / DB error", color="negative")
                ui.notify(traceback.format_exc(), color="negative")
                return

        if app.storage.user.get("authenticated", False):
            return RedirectResponse(redirect_to)

        with ui.card().classes("absolute-center"):
            email = ui.input("Email").on("keydown.enter", try_login)
            password = ui.input(
                "Password", password=True, password_toggle_button=True
            ).on("keydown.enter", try_login)
            ui.button("Log in", on_click=try_login)
        return None

