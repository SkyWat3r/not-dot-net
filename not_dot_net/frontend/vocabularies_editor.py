"""Bespoke admin editor for stored vocabularies (Settings -> Vocabularies)."""

from nicegui import ui

from not_dot_net.backend.permissions import check_permission
from not_dot_net.backend.vocabularies import (
    VocabulariesConfig, StoredVocabulary, VocabularyTerm,
    vocabularies_config, list_vocabularies,
)
from not_dot_net.frontend.i18n import t, get_locale
from not_dot_net.frontend.workflow_editor_options import display_name_to_key


async def save_vocabulary(vocabulary: StoredVocabulary) -> None:
    """Validate (unique codes) and upsert one stored vocabulary."""
    codes = [term.code for term in vocabulary.terms]
    dupes = {c for c in codes if codes.count(c) > 1}
    if dupes:
        raise ValueError(f"duplicate code(s): {', '.join(sorted(dupes))}")
    cfg = await vocabularies_config.get()
    cfg.vocabularies[vocabulary.key] = vocabulary
    await vocabularies_config.set(cfg)


async def delete_vocabulary(key: str) -> None:
    cfg = await vocabularies_config.get()
    cfg.vocabularies.pop(key, None)
    await vocabularies_config.set(cfg)


async def render(user) -> None:
    await check_permission(user, "manage_settings")
    container = ui.column().classes("w-full")

    async def refresh():
        container.clear()
        views = await list_vocabularies()
        cfg = await vocabularies_config.get()
        with container:
            for view in sorted(views, key=lambda v: v.key):
                with ui.row().classes("w-full items-center gap-2"):
                    ui.label(view.label.get(get_locale()) or view.key).classes("font-medium")
                    if view.source == "builtin":
                        ui.badge(t("vocab_system")).props("color=grey")
                    else:
                        stored = cfg.vocabularies[view.key]
                        ui.button(t("edit"),
                                  on_click=lambda s=stored: _open_term_editor(s, refresh)
                                  ).props("flat dense")
                        ui.button(icon="delete",
                                  on_click=lambda k=view.key: _confirm_delete(k, refresh)
                                  ).props("flat dense color=negative")
            ui.button(t("vocab_new"), icon="add",
                      on_click=lambda: _prompt_new_vocabulary(cfg, refresh)).props("flat")

    await refresh()


def _prompt_new_vocabulary(cfg: VocabulariesConfig, on_done) -> None:
    dlg = ui.dialog()
    with dlg, ui.card():
        ui.label(t("vocab_new"))
        name = ui.input(t("vocab_name")).props("outlined dense")

        async def create():
            key = display_name_to_key(name.value or "", set(cfg.vocabularies), fallback_prefix="vocab")
            await save_vocabulary(StoredVocabulary(key=key, label={get_locale(): name.value or key}))
            dlg.close()
            await on_done()

        ui.button("OK", on_click=create).props("color=primary")
    dlg.open()


def _confirm_delete(key: str, on_done) -> None:
    dlg = ui.dialog()
    with dlg, ui.card():
        ui.label(t("vocab_confirm_delete", key=key))

        async def do():
            await delete_vocabulary(key)
            dlg.close()
            await on_done()

        with ui.row():
            ui.button(t("delete"), on_click=do).props("color=negative")
            ui.button(t("cancel"), on_click=dlg.close).props("flat")
    dlg.open()


def _open_term_editor(vocabulary: StoredVocabulary, on_done) -> None:
    """Edit the terms table (code | en | fr | active) + allow_custom, then save."""
    dlg = ui.dialog().props("maximized")
    working = vocabulary.model_copy(deep=True)
    with dlg, ui.card().classes("w-full h-full"):
        ui.label(working.key).classes("text-h6")
        allow = ui.switch(t("vocab_allow_custom"), value=working.allow_custom)
        rows = ui.column().classes("w-full")

        def render_rows():
            rows.clear()
            with rows:
                for i, term in enumerate(working.terms):
                    with ui.row().classes("items-center gap-2"):
                        ui.input(t("vocab_code"), value=term.code,
                                 on_change=lambda e, i=i: _set(working, i, "code", e.value)
                                 ).props("outlined dense")
                        ui.input("EN", value=term.labels.get("en", ""),
                                 on_change=lambda e, i=i: _set_label(working, i, "en", e.value)
                                 ).props("outlined dense")
                        ui.input("FR", value=term.labels.get("fr", ""),
                                 on_change=lambda e, i=i: _set_label(working, i, "fr", e.value)
                                 ).props("outlined dense")
                        ui.switch(t("active"), value=term.active,
                                  on_change=lambda e, i=i: _set(working, i, "active", e.value))
                        ui.button(icon="delete",
                                  on_click=lambda i=i: (_del(working, i), render_rows())
                                  ).props("flat dense color=negative")

        def add_row():
            working.terms.append(VocabularyTerm(code="", labels={}))
            render_rows()

        async def save():
            working.allow_custom = allow.value
            try:
                await save_vocabulary(working)
            except ValueError as exc:
                ui.notify(str(exc), color="negative")
                return
            dlg.close()
            await on_done()

        ui.button(t("vocab_add_term"), icon="add", on_click=add_row).props("flat")
        render_rows()
        with ui.row():
            ui.button(t("save"), on_click=save).props("color=primary")
            ui.button(t("cancel"), on_click=dlg.close).props("flat")
    dlg.open()


def _set(voc: StoredVocabulary, i: int, attr: str, value) -> None:
    setattr(voc.terms[i], attr, value)


def _set_label(voc: StoredVocabulary, i: int, locale: str, value: str) -> None:
    if value:
        voc.terms[i].labels[locale] = value
    else:
        voc.terms[i].labels.pop(locale, None)


def _del(voc: StoredVocabulary, i: int) -> None:
    del voc.terms[i]
