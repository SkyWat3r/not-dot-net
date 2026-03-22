from typing import Optional

from fastapi import Depends
from fastapi.responses import RedirectResponse
from nicegui import ui

from not_dot_net.backend.db import User
from not_dot_net.backend.roles import Role, has_role
from not_dot_net.backend.users import current_active_user_optional
from not_dot_net.frontend.directory import render as render_directory
from not_dot_net.frontend.dashboard import render as render_dashboard
from not_dot_net.frontend.new_request import render as render_new_request
from not_dot_net.frontend.i18n import SUPPORTED_LOCALES, get_locale, set_locale, t


def setup():
    @ui.page("/")
    def main_page(
        user: Optional[User] = Depends(current_active_user_optional),
    ) -> Optional[RedirectResponse]:
        if not user:
            return RedirectResponse("/login")

        locale = get_locale()
        people_label = t("people")
        dashboard_label = t("dashboard")
        new_request_label = t("new_request")

        can_create = has_role(user, Role.STAFF)

        with ui.header().classes("row items-center justify-between px-4"):
            ui.label(t("app_name")).classes("text-h6 text-white")
            with ui.tabs().classes("ml-4") as tabs:
                ui.tab(dashboard_label, icon="dashboard")
                ui.tab(people_label, icon="people")
                if can_create:
                    ui.tab(new_request_label, icon="add_circle")
            with ui.row().classes("items-center"):
                def on_lang_change(e):
                    set_locale(e.value)
                    ui.run_javascript("window.location.reload()")

                ui.toggle(
                    list(SUPPORTED_LOCALES), value=locale, on_change=on_lang_change
                ).props("flat dense color=white text-color=white toggle-color=white")

                with ui.button(icon="person").props("flat color=white"):
                    with ui.menu():
                        ui.menu_item(t("my_profile"), on_click=lambda: tabs.set_value(people_label))
                        ui.menu_item(t("logout"), on_click=lambda: _logout())

        with ui.tab_panels(tabs, value=dashboard_label).classes("w-full"):
            with ui.tab_panel(dashboard_label):
                render_dashboard(user)
            with ui.tab_panel(people_label):
                render_directory(user)
            if can_create:
                with ui.tab_panel(new_request_label):
                    render_new_request(user)

        return None


def _logout():
    ui.run_javascript(
        'document.cookie = "fastapiusersauth=; path=/; max-age=0";'
        'window.location.href = "/login";'
    )
