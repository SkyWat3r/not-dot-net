"""Bookings tab — resource list, booking calendar, admin management."""

import uuid
from datetime import date, timedelta

from nicegui import ui

from not_dot_net.backend.booking_service import (
    BookingConflictError,
    BookingValidationError,
    cancel_booking,
    create_booking,
    create_resource,
    delete_resource,
    list_bookings_for_resource,
    list_bookings_for_user,
    list_resources,
    update_resource,
)
from not_dot_net.backend.db import User, get_async_session
from not_dot_net.backend.roles import Role, has_role
from not_dot_net.config import get_settings
from not_dot_net.frontend.i18n import t

from contextlib import asynccontextmanager
from sqlalchemy import select

RESOURCE_TYPES = ["desktop", "laptop"]


def render(user: User):
    container = ui.column().classes("w-full")

    async def refresh():
        await _render_bookings(container, user)

    ui.timer(0, refresh, once=True)


async def _get_user_name(user_id: uuid.UUID) -> str:
    get_session = asynccontextmanager(get_async_session)
    async with get_session() as session:
        u = await session.get(User, user_id)
        return u.full_name or u.email if u else "?"


async def _render_bookings(container, user: User, filter_range=None):
    container.clear()
    is_admin = has_role(user, Role.ADMIN)
    resources = await list_resources(active_only=not is_admin)
    my_bookings = await list_bookings_for_user(user.id)

    with container:
        # --- My Bookings ---
        if my_bookings:
            ui.label(t("my_bookings")).classes("text-h6 mb-2")
            with ui.element("div").classes(
                "w-full grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 mb-4"
            ):
                for bk in my_bookings:
                    res = _get_resource_for_booking(bk.resource_id, resources)
                    res_name = res.name if res else "?"
                    with ui.card().classes("q-py-sm q-px-md"):
                        with ui.row().classes("items-center justify-between w-full"):
                            with ui.column().classes("gap-0"):
                                ui.label(res_name).classes("font-bold")
                                ui.label(
                                    f"{bk.start_date} → {bk.end_date}"
                                ).classes("text-sm text-grey-8")
                                if bk.note:
                                    ui.label(bk.note).classes("text-xs text-grey")

                            async def do_cancel(b=bk):
                                try:
                                    await cancel_booking(b.id, user.id)
                                except Exception as e:
                                    ui.notify(str(e), color="negative")
                                    return
                                ui.notify(t("booking_cancelled"), color="positive")
                                await _render_bookings(container, user)

                            ui.button(
                                icon="close", on_click=do_cancel,
                            ).props("flat dense round color=negative size=sm")

            ui.separator().classes("mb-4")

        # --- Global date range filter ---
        today = date.today()
        default_range = filter_range or {"from": str(today), "to": str(today + timedelta(days=7))}
        state = {"range": default_range}

        def _range_label(r):
            return f"{r['from']} → {r['to']}" if isinstance(r, dict) else ""

        sites = get_settings().sites

        with ui.row().classes("items-center gap-2 mb-3"):
            ui.icon("date_range", size="sm").classes("text-primary")
            range_display = ui.input(
                t("filter"), value=_range_label(default_range),
            ).props("outlined dense readonly").classes("min-w-[250px]")
            with range_display.add_slot("append"):
                ui.icon("event").classes("cursor-pointer")
            with ui.menu() as menu:
                date_picker = ui.date(default_range).props("range")

            site_select = ui.select(
                options={None: t("all_types")} | {s: s for s in sites},
                value=None, label=t("resource_location"),
            ).props("outlined dense").classes("min-w-[150px]")

            type_select = ui.select(
                options={None: t("all_types")} | {rt: t(rt) for rt in RESOURCE_TYPES},
                value=None, label=t("resource_type"),
            ).props("outlined dense").classes("min-w-[150px]")

        resource_area = ui.column().classes("w-full")

        async def apply_filter():
            val = date_picker.value
            if not val or not isinstance(val, dict):
                return
            state["range"] = val
            range_display.value = _range_label(val)
            menu.close()
            await _render_resource_list(
                container, resource_area, resources, user, is_admin, val,
                site_filter=site_select.value,
                type_filter=type_select.value,
            )

        date_picker.on_value_change(lambda _: apply_filter())
        site_select.on_value_change(lambda _: apply_filter())
        type_select.on_value_change(lambda _: apply_filter())

        # --- Resources header ---
        with ui.row().classes("items-center justify-between w-full mb-2"):
            ui.label(t("resources")).classes("text-h6")
            if is_admin:
                ui.button(
                    t("add_resource"), icon="add",
                    on_click=lambda: _show_resource_dialog(container, user),
                ).props("flat color=primary")

        if not resources:
            ui.label(t("no_bookings")).classes("text-grey")
            return

        # Initial render with default range
        await _render_resource_list(
            container, resource_area, resources, user, is_admin, default_range,
        )


async def _render_resource_list(outer_container, area, resources, user, is_admin, date_range,
                                site_filter=None, type_filter=None):
    """Render resource cards filtered by availability, site, and type."""
    area.clear()
    try:
        range_start = date.fromisoformat(date_range["from"])
        range_end = date.fromisoformat(date_range["to"]) + timedelta(days=1)
    except (ValueError, KeyError):
        return

    # Apply site and type filters
    filtered = resources
    if site_filter:
        filtered = [r for r in filtered if r.location == site_filter]
    if type_filter:
        filtered = [r for r in filtered if r.resource_type == type_filter]

    # Build availability map
    availability: dict[uuid.UUID, bool] = {}
    for res in filtered:
        bookings = await list_bookings_for_resource(
            res.id, from_date=range_start, to_date=range_end,
        )
        has_conflict = any(
            b.start_date < range_end and b.end_date > range_start for b in bookings
        )
        availability[res.id] = not has_conflict

    sites = get_settings().sites
    state = {"expanded_id": None}

    with area:
        if not filtered:
            ui.label(t("no_bookings")).classes("text-grey")
            return

        # Group by site
        by_site: dict[str, list] = {s: [] for s in sites}
        by_site[""] = []
        for res in filtered:
            key = res.location if res.location in by_site else ""
            by_site[key].append(res)

        for site, site_resources in by_site.items():
            if not site_resources:
                continue
            if site:
                ui.label(site).classes("text-subtitle1 font-bold mt-3 mb-1")

            # Available first, then booked
            site_resources.sort(key=lambda r: (not availability.get(r.id, True)))

            with ui.element("div").classes(
                "w-full grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3"
            ):
                for res in site_resources:
                    await _resource_card(
                        outer_container, res, user, is_admin, state,
                        is_available=availability.get(res.id, True),
                        book_range=date_range,
                    )


def _get_resource_for_booking(resource_id, resources):
    for r in resources:
        if r.id == resource_id:
            return r
    return None


async def _resource_card(outer_container, res, user, is_admin, state,
                         is_available=True, book_range=None):
    with ui.card().classes("cursor-pointer q-py-sm q-px-md") as card:
        with ui.row().classes("items-center justify-between w-full"):
            with ui.column().classes("gap-0"):
                with ui.row().classes("items-center gap-2"):
                    icon = "computer" if res.resource_type == "desktop" else "laptop"
                    ui.icon(icon, size="sm").classes("text-grey-7")
                    ui.label(res.name).classes("font-bold")
                ui.label(t(res.resource_type)).classes("text-xs text-grey")
            ui.badge(
                t("available") if is_available else t("booked_by"),
                color="positive" if is_available else "orange",
            )

        if not res.active:
            ui.badge("inactive", color="grey").classes("mt-1")

        detail = ui.column().classes("w-full mt-2")
        detail.set_visibility(False)
        detail.on("click.stop", js_handler="() => {}")

        async def toggle(dc=detail, r=res, st=state):
            if st["expanded_id"] == r.id:
                dc.set_visibility(False)
                st["expanded_id"] = None
                return
            st["expanded_id"] = r.id
            dc.set_visibility(True)
            dc.clear()
            with dc:
                ui.separator()
                await _render_resource_detail(
                    outer_container, r, user, is_admin, book_range=book_range,
                )

        card.on("click", toggle)


async def _render_resource_detail(outer_container, res, user, is_admin, book_range=None):
    if res.description:
        ui.label(res.description).classes("text-sm text-grey-8 mb-2")

    # Upcoming bookings
    today = date.today()
    bookings = await list_bookings_for_resource(
        res.id, from_date=today, to_date=today + timedelta(days=90),
    )

    if bookings:
        ui.label(t("bookings")).classes("text-subtitle2 mt-2 mb-1")
        for bk in bookings:
            owner_name = await _get_user_name(bk.user_id)
            is_own = bk.user_id == user.id
            with ui.row().classes("items-center gap-2 w-full"):
                ui.label(
                    f"{bk.start_date} → {bk.end_date}"
                ).classes("text-sm")
                ui.label(owner_name).classes("text-sm text-grey")
                if is_own or is_admin:
                    async def do_cancel(b=bk):
                        try:
                            await cancel_booking(b.id, user.id, is_admin=is_admin)
                        except Exception as e:
                            ui.notify(str(e), color="negative")
                            return
                        ui.notify(t("booking_cancelled"), color="positive")
                        await _render_bookings(outer_container, user)

                    ui.button(icon="close", on_click=do_cancel).props(
                        "flat dense round size=xs color=negative"
                    )

    # Book form — pre-filled from global range picker
    ui.label(t("book")).classes("text-subtitle2 mt-3 mb-1")
    default_range = book_range or {"from": str(today), "to": str(today + timedelta(days=1))}
    range_label = f"{default_range['from']} → {default_range['to']}"
    with ui.row().classes("items-center gap-2"):
        ui.label(range_label).classes("text-sm text-grey-8")
        note_input = ui.input(t("note")).props("outlined dense")

        async def do_book():
            try:
                s = date.fromisoformat(default_range["from"])
                e = date.fromisoformat(default_range["to"]) + timedelta(days=1)
            except (ValueError, KeyError):
                ui.notify("Invalid date range", color="negative")
                return
            try:
                await create_booking(res.id, user.id, s, e, note=note_input.value)
            except (BookingConflictError, BookingValidationError) as err:
                ui.notify(str(err), color="negative")
                return
            ui.notify(t("booking_created"), color="positive")
            await _render_bookings(outer_container, user)

        ui.button(t("book"), on_click=do_book).props("color=primary")

    # Admin controls
    if is_admin:
        ui.separator().classes("mt-3")
        with ui.row().classes("gap-2 mt-2"):
            ui.button(
                t("edit_resource"), icon="edit",
                on_click=lambda: _show_resource_dialog(
                    outer_container, user, resource=res,
                ),
            ).props("flat dense color=primary")

            async def do_delete():
                try:
                    await delete_resource(res.id)
                except Exception as e:
                    ui.notify(str(e), color="negative")
                    return
                ui.notify(t("resource_deleted"), color="positive")
                await _render_bookings(outer_container, user)

            ui.button(
                t("delete"), icon="delete", on_click=do_delete,
            ).props("flat dense color=negative")


def _show_resource_dialog(outer_container, user, resource=None):
    editing = resource is not None

    with ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label(t("edit_resource") if editing else t("add_resource")).classes("text-h6")

        name_input = ui.input(
            t("resource_name"), value=resource.name if editing else "",
        ).props("outlined dense").classes("w-full")

        type_select = ui.select(
            options=RESOURCE_TYPES,
            value=resource.resource_type if editing else RESOURCE_TYPES[0],
            label=t("resource_type"),
        ).props("outlined dense").classes("w-full")

        sites = get_settings().sites
        location_select = ui.select(
            options=sites,
            value=resource.location if editing and resource.location in sites else sites[0],
            label=t("resource_location"),
        ).props("outlined dense").classes("w-full")

        desc_input = ui.textarea(
            t("description"),
            value=resource.description or "" if editing else "",
        ).props("outlined dense").classes("w-full")

        with ui.row().classes("justify-end gap-2 mt-2"):
            ui.button(t("cancel"), on_click=dialog.close).props("flat")

            async def do_save():
                if not name_input.value.strip():
                    ui.notify(t("required_field"), color="negative")
                    return
                try:
                    if editing:
                        await update_resource(
                            resource.id,
                            name=name_input.value.strip(),
                            resource_type=type_select.value,
                            location=location_select.value,
                            description=desc_input.value.strip() or None,
                        )
                        ui.notify(t("resource_updated"), color="positive")
                    else:
                        await create_resource(
                            name=name_input.value.strip(),
                            resource_type=type_select.value,
                            description=desc_input.value.strip(),
                            location=location_select.value,
                        )
                        ui.notify(t("resource_created"), color="positive")
                except Exception as e:
                    ui.notify(str(e), color="negative")
                    return
                dialog.close()
                await _render_bookings(outer_container, user)

            ui.button(t("save"), on_click=do_save).props("color=primary")

    dialog.open()
