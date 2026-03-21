from typing import Optional

from fastapi import Request, Depends
from fastapi.responses import RedirectResponse

from not_dot_net.backend.app import NotDotNetApp
from not_dot_net.backend.db import User
from .register import register_frontend_loader
from nicegui import ui


@register_frontend_loader
def load(ndtapp: NotDotNetApp):
    @ui.page("/user/profile")
    def user_page(
        user: Optional[User] = Depends(ndtapp.auth_backends.current_active_user_optional),
    ) -> Optional[RedirectResponse]:
        if not user:
            ui.notify("Please log in to access your user profile", color="warning")
            return RedirectResponse("/login")
        with ui.card().classes("absolute-center"):
            ui.label(f"User Page for User ID: {user.id}")
            ui.label(f"Email: {user.email}")
            ui.button("Go to Main Page", on_click=lambda: ui.navigate.to("/"))
        return None
