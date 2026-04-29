"""Master-detail dialog for editing the workflows config section."""

from __future__ import annotations

import logging
import re

from nicegui import ui
from pydantic import ValidationError

from not_dot_net.backend.audit import log_audit
from not_dot_net.backend.workflow_service import workflows_config, WorkflowsConfig
from not_dot_net.config import WorkflowConfig, WorkflowStepConfig
from not_dot_net.frontend.i18n import t

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _validate_slug(key: str) -> None:
    if not _SLUG_RE.fullmatch(key):
        raise ValueError(
            f"Invalid key '{key}': must be lowercase letters, digits, underscore; start with a letter"
        )


class WorkflowEditorDialog:
    """Dialog state holder. Construct with `await WorkflowEditorDialog.create(user)`."""

    def __init__(self, user, original: WorkflowsConfig):
        self.user = user
        self.original = original
        self.working_copy = original.model_copy(deep=True)
        self.selected_workflow: str | None = next(iter(self.working_copy.workflows), None)
        self.selected_step: str | None = None
        self.dialog: ui.dialog | None = None
        self._tree_container: ui.column | None = None
        self._detail_container: ui.column | None = None

    @classmethod
    async def create(cls, user) -> "WorkflowEditorDialog":
        original = await workflows_config.get()
        instance = cls(user, original)
        instance._build()
        return instance

    def _build(self) -> None:
        self.dialog = ui.dialog().props("maximized")
        with self.dialog, ui.card().classes("w-full h-full"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label(t("workflows_editor")).classes("text-h6")
            with ui.row().classes("w-full grow no-wrap"):
                self._tree_container = ui.column().classes("w-72 q-pr-md").style("border-right: 1px solid #e0e0e0")
                self._detail_container = ui.column().classes("grow")
            with ui.row().classes("w-full justify-end"):
                ui.button(t("cancel"), on_click=self.close).props("flat")
                ui.button(t("reset_defaults"), on_click=self.reset).props("flat color=grey")
                ui.button(t("save"), on_click=self.save).props("color=primary")
        self._refresh_tree()
        self._refresh_detail()

    # --- workflow mutations ---

    def add_workflow(self, key: str) -> None:
        _validate_slug(key)
        if key in self.working_copy.workflows:
            raise ValueError(f"Workflow '{key}' already exists")
        self.working_copy.workflows[key] = WorkflowConfig(label=key, steps=[])
        self.selected_workflow = key
        self.selected_step = None
        self._refresh_tree()
        self._refresh_detail()

    def delete_workflow(self, key: str) -> None:
        if key not in self.working_copy.workflows:
            return
        del self.working_copy.workflows[key]
        if self.selected_workflow == key:
            self.selected_workflow = next(iter(self.working_copy.workflows), None)
            self.selected_step = None
        self._refresh_tree()
        self._refresh_detail()

    def duplicate_workflow(self, src_key: str, new_key: str) -> None:
        _validate_slug(new_key)
        if new_key in self.working_copy.workflows:
            raise ValueError(f"Workflow '{new_key}' already exists")
        if src_key not in self.working_copy.workflows:
            raise ValueError(f"Workflow '{src_key}' does not exist")
        self.working_copy.workflows[new_key] = self.working_copy.workflows[src_key].model_copy(deep=True)
        self.selected_workflow = new_key
        self.selected_step = None
        self._refresh_tree()
        self._refresh_detail()

    # --- step mutations ---

    def add_step(self, wf_key: str, step_key: str) -> None:
        _validate_slug(step_key)
        wf = self.working_copy.workflows[wf_key]
        if any(s.key == step_key for s in wf.steps):
            raise ValueError(f"Step '{step_key}' already exists in workflow '{wf_key}'")
        wf.steps.append(WorkflowStepConfig(key=step_key, type="form"))
        self.selected_workflow = wf_key
        self.selected_step = step_key
        self._refresh_tree()
        self._refresh_detail()

    def delete_step(self, wf_key: str, step_key: str) -> None:
        wf = self.working_copy.workflows[wf_key]
        wf.steps = [s for s in wf.steps if s.key != step_key]
        if self.selected_step == step_key:
            self.selected_step = wf.steps[0].key if wf.steps else None
        self._refresh_tree()
        self._refresh_detail()

    def select(self, wf_key: str, step_key: str | None = None) -> None:
        self.selected_workflow = wf_key
        self.selected_step = step_key
        self._refresh_tree()
        self._refresh_detail()

    # --- rendering ---

    def _refresh_tree(self) -> None:
        if self._tree_container is None:
            return
        self._tree_container.clear()
        with self._tree_container:
            for wf_key, wf in self.working_copy.workflows.items():
                self._render_workflow_header(wf_key, wf)
                for step in wf.steps:
                    self._render_step_row(wf_key, step.key)
            ui.button("+ Add workflow", on_click=self._on_add_workflow_click).props("flat dense color=primary")

    def _render_workflow_header(self, wf_key: str, wf) -> None:
        is_selected = self.selected_workflow == wf_key and self.selected_step is None
        with ui.row().classes(f"w-full items-center {'bg-blue-1' if is_selected else ''}"):
            ui.button(wf.label or wf_key, on_click=lambda k=wf_key: self.select(k)).props("flat dense").classes("grow text-left")
            ui.button(icon="content_copy", on_click=lambda k=wf_key: self._on_duplicate_click(k)).props("flat dense round size=sm")
            ui.button(icon="delete", on_click=lambda k=wf_key: self.delete_workflow(k)).props("flat dense round size=sm color=negative")

    def _render_step_row(self, wf_key: str, step_key: str) -> None:
        is_selected = self.selected_workflow == wf_key and self.selected_step == step_key
        with ui.row().classes(f"w-full items-center q-pl-md {'bg-blue-1' if is_selected else ''}"):
            ui.button(f"• {step_key}", on_click=lambda w=wf_key, s=step_key: self.select(w, s)).props("flat dense").classes("grow text-left")
            ui.button(icon="delete", on_click=lambda w=wf_key, s=step_key: self.delete_step(w, s)).props("flat dense round size=sm color=negative")

    def _refresh_detail(self) -> None:
        if self._detail_container is None:
            return
        self._detail_container.clear()
        with self._detail_container:
            if self.selected_workflow is None:
                ui.label("No workflow selected. Add one to begin.").classes("text-grey")
                return
            if self.selected_step is None:
                ui.label(f"Workflow: {self.selected_workflow}").classes("text-h6")
                ui.label("(workflow-level editor will land in Task 6)").classes("text-grey")
            else:
                ui.label(f"Step: {self.selected_step}").classes("text-h6")
                ui.label("(step editor will land in Tasks 7-8)").classes("text-grey")
            ui.button(
                f"+ Add step to {self.selected_workflow}",
                on_click=lambda k=self.selected_workflow: self._on_add_step_click(k),
            ).props("flat dense color=primary")

    def _on_add_workflow_click(self) -> None:
        self._prompt_for_key("New workflow key", lambda k: self.add_workflow(k))

    def _on_duplicate_click(self, src_key: str) -> None:
        self._prompt_for_key(f"Duplicate '{src_key}' as", lambda k: self.duplicate_workflow(src_key, k))

    def _on_add_step_click(self, wf_key: str) -> None:
        self._prompt_for_key("New step key", lambda k: self.add_step(wf_key, k))

    def _prompt_for_key(self, prompt: str, callback) -> None:
        dlg = ui.dialog()
        with dlg, ui.card():
            ui.label(prompt)
            inp = ui.input(label="key").props("dense outlined stack-label autofocus")
            err = ui.label("").classes("text-negative text-sm")

            def confirm():
                try:
                    callback(inp.value.strip())
                    dlg.close()
                except ValueError as e:
                    err.set_text(str(e))

            with ui.row():
                ui.button("OK", on_click=confirm).props("color=primary")
                ui.button("Cancel", on_click=dlg.close).props("flat")
        dlg.open()

    # --- lifecycle ---

    def open(self) -> None:
        if self.dialog:
            self.dialog.open()

    def close(self) -> None:
        if self.dialog:
            self.dialog.close()

    async def save(self) -> None:
        try:
            validated = WorkflowsConfig.model_validate(self.working_copy.model_dump())
        except ValidationError as e:
            ui.notify(str(e), color="negative", multi_line=True)
            return
        await workflows_config.set(validated)
        await log_audit(
            "settings", "update",
            actor_id=self.user.id, actor_email=self.user.email,
            detail="section=workflows",
        )
        ui.notify(t("settings_saved"), color="positive")
        self.close()

    async def reset(self) -> None:
        await workflows_config.reset()
        self.original = await workflows_config.get()
        self.working_copy = self.original.model_copy(deep=True)
        ui.notify(t("settings_reset"), color="info")


async def open_workflow_editor(user) -> None:
    dlg = await WorkflowEditorDialog.create(user)
    dlg.open()
