# Custom Markdown Pages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let authorized users create/edit markdown pages visible to everyone (including unauthenticated visitors) at public URLs and in a Pages tab.

**Architecture:** New `Page` model + `page_service.py` CRUD + `pages.py` frontend tab + public `@ui.page("/pages/{slug}")` route. Follows existing patterns: `booking_models.py` / `booking_service.py` / `bookings.py`.

**Tech Stack:** SQLAlchemy async, NiceGUI `ui.markdown()`, existing permission registry.

---

### Task 1: Page Model

**Files:**
- Create: `not_dot_net/backend/page_models.py`
- Modify: `not_dot_net/backend/db.py:60-68` (add import to register model)
- Test: `tests/test_page_service.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_page_service.py
"""Tests for custom page CRUD."""

import pytest

from not_dot_net.backend.page_models import Page


async def test_page_model_exists():
    p = Page(
        title="Hello",
        slug="hello",
        content="# Hello\nWorld",
        author_id=None,
    )
    assert p.title == "Hello"
    assert p.slug == "hello"
    assert p.published is False
    assert p.sort_order == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_page_service.py::test_page_model_exists -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'not_dot_net.backend.page_models'`

- [ ] **Step 3: Write the Page model**

```python
# not_dot_net/backend/page_models.py
"""Custom markdown page model."""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, MappedAsDataclass, mapped_column

from not_dot_net.backend.db import Base


class Page(MappedAsDataclass, Base, kw_only=True):
    __tablename__ = "page"

    title: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(200), unique=True)
    content: Mapped[str] = mapped_column(Text, default="")
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default_factory=uuid.uuid4)
    sort_order: Mapped[int] = mapped_column(default=0)
    published: Mapped[bool] = mapped_column(default=False)
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True, default=None,
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=None)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), default=None,
    )
```

- [ ] **Step 4: Register model with Base in db.py**

Add this line in `not_dot_net/backend/db.py` inside `create_db_and_tables()`, after the existing model imports:

```python
    import not_dot_net.backend.page_models  # noqa: F401 — register Page with Base
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_page_service.py::test_page_model_exists -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add not_dot_net/backend/page_models.py not_dot_net/backend/db.py tests/test_page_service.py
git commit -m "feat: add Page model for custom markdown pages"
```

---

### Task 2: Page Service — CRUD + Permission

**Files:**
- Create: `not_dot_net/backend/page_service.py`
- Modify: `tests/test_page_service.py`

- [ ] **Step 1: Write failing tests for CRUD**

Append to `tests/test_page_service.py`:

```python
from not_dot_net.backend.page_service import (
    MANAGE_PAGES,
    create_page,
    delete_page,
    get_page,
    list_pages,
    update_page,
)


async def test_create_and_get_page():
    page = await create_page(
        title="Welcome", slug="welcome", content="# Welcome\nHello!", author_id=None,
    )
    assert page.id is not None
    assert page.slug == "welcome"
    assert page.published is False

    fetched = await get_page("welcome")
    assert fetched is not None
    assert fetched.title == "Welcome"


async def test_get_page_not_found():
    result = await get_page("nonexistent")
    assert result is None


async def test_list_pages_published_only():
    await create_page(title="Draft", slug="draft", content="x", author_id=None)
    await create_page(
        title="Public", slug="public", content="y", author_id=None, published=True,
    )
    published = await list_pages(published_only=True)
    assert all(p.published for p in published)
    assert any(p.slug == "public" for p in published)
    assert not any(p.slug == "draft" for p in published)

    all_pages = await list_pages(published_only=False)
    slugs = [p.slug for p in all_pages]
    assert "draft" in slugs
    assert "public" in slugs


async def test_list_pages_sort_order():
    await create_page(title="B", slug="b-page", content="", author_id=None, sort_order=2, published=True)
    await create_page(title="A", slug="a-page", content="", author_id=None, sort_order=1, published=True)
    pages = await list_pages(published_only=True)
    slugs = [p.slug for p in pages]
    assert slugs.index("a-page") < slugs.index("b-page")


async def test_update_page():
    page = await create_page(title="Old", slug="upd", content="old", author_id=None)
    updated = await update_page(page.id, title="New", content="new")
    assert updated.title == "New"
    assert updated.content == "new"
    assert updated.slug == "upd"


async def test_delete_page():
    page = await create_page(title="Bye", slug="bye", content="", author_id=None)
    await delete_page(page.id)
    assert await get_page("bye") is None


async def test_create_duplicate_slug_raises():
    await create_page(title="One", slug="dup", content="", author_id=None)
    with pytest.raises(ValueError, match="slug"):
        await create_page(title="Two", slug="dup", content="", author_id=None)


async def test_manage_pages_permission_registered():
    from not_dot_net.backend.permissions import get_permissions
    assert MANAGE_PAGES in get_permissions()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_page_service.py -v -k "not test_page_model"`
Expected: FAIL — `ModuleNotFoundError` or `ImportError`

- [ ] **Step 3: Write the page service**

```python
# not_dot_net/backend/page_service.py
"""Page service — CRUD for custom markdown pages."""

import uuid

from sqlalchemy import select

from not_dot_net.backend.db import session_scope
from not_dot_net.backend.page_models import Page
from not_dot_net.backend.permissions import permission

MANAGE_PAGES = permission("manage_pages", "Manage pages", "Create/edit/delete custom pages")


async def list_pages(published_only: bool = True) -> list[Page]:
    async with session_scope() as session:
        query = select(Page).order_by(Page.sort_order, Page.title)
        if published_only:
            query = query.where(Page.published == True)  # noqa: E712
        result = await session.execute(query)
        return list(result.scalars().all())


async def get_page(slug: str) -> Page | None:
    async with session_scope() as session:
        result = await session.execute(
            select(Page).where(Page.slug == slug)
        )
        return result.scalars().first()


async def create_page(
    title: str,
    slug: str,
    content: str,
    author_id: uuid.UUID | None,
    sort_order: int = 0,
    published: bool = False,
) -> Page:
    existing = await get_page(slug)
    if existing is not None:
        raise ValueError(f"Page with slug '{slug}' already exists")

    async with session_scope() as session:
        page = Page(
            title=title,
            slug=slug,
            content=content,
            author_id=author_id,
            sort_order=sort_order,
            published=published,
        )
        session.add(page)
        await session.commit()
        await session.refresh(page)
        return page


async def update_page(page_id: uuid.UUID, **kwargs) -> Page:
    async with session_scope() as session:
        page = await session.get(Page, page_id)
        if page is None:
            raise ValueError(f"Page {page_id} not found")
        for key, value in kwargs.items():
            if hasattr(page, key):
                setattr(page, key, value)
        await session.commit()
        await session.refresh(page)
        return page


async def delete_page(page_id: uuid.UUID) -> None:
    async with session_scope() as session:
        page = await session.get(Page, page_id)
        if page is None:
            raise ValueError(f"Page {page_id} not found")
        await session.delete(page)
        await session.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_page_service.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/backend/page_service.py tests/test_page_service.py
git commit -m "feat: add page service with CRUD and manage_pages permission"
```

---

### Task 3: i18n Keys

**Files:**
- Modify: `not_dot_net/frontend/i18n.py`

- [ ] **Step 1: Add English keys**

Add to the `"en"` dict in `TRANSLATIONS`:

```python
        # Pages
        "pages": "Pages",
        "new_page": "New Page",
        "edit_page": "Edit Page",
        "page_title": "Title",
        "page_slug": "Slug",
        "page_content": "Content (Markdown)",
        "page_sort_order": "Sort Order",
        "page_published": "Published",
        "page_saved": "Page saved",
        "page_deleted": "Page deleted",
        "page_not_found": "Page not found",
        "page_draft": "Draft",
        "confirm_delete_page": "Delete this page?",
```

- [ ] **Step 2: Add French keys**

Add to the `"fr"` dict in `TRANSLATIONS`:

```python
        # Pages
        "pages": "Pages",
        "new_page": "Nouvelle page",
        "edit_page": "Modifier la page",
        "page_title": "Titre",
        "page_slug": "Identifiant URL",
        "page_content": "Contenu (Markdown)",
        "page_sort_order": "Ordre d'affichage",
        "page_published": "Publiée",
        "page_saved": "Page enregistrée",
        "page_deleted": "Page supprimée",
        "page_not_found": "Page introuvable",
        "page_draft": "Brouillon",
        "confirm_delete_page": "Supprimer cette page ?",
```

- [ ] **Step 3: Run translation validation**

Run: `uv run pytest -v -k "test_" --co -q | head -5` then `uv run pytest -v`
Expected: all tests pass (i18n `validate_translations()` runs at import time in tests)

- [ ] **Step 4: Commit**

```bash
git add not_dot_net/frontend/i18n.py
git commit -m "feat: add i18n keys for custom pages"
```

---

### Task 4: Pages Tab in Shell

**Files:**
- Create: `not_dot_net/frontend/pages.py`
- Modify: `not_dot_net/frontend/shell.py`

- [ ] **Step 1: Create the pages tab renderer**

```python
# not_dot_net/frontend/pages.py
"""Pages tab — list custom pages, inline editor for authorized users."""

import re

from nicegui import ui

from not_dot_net.backend.db import User
from not_dot_net.backend.page_service import (
    MANAGE_PAGES,
    create_page,
    delete_page,
    list_pages,
    get_page,
    update_page,
)
from not_dot_net.backend.permissions import has_permissions
from not_dot_net.frontend.i18n import t


def _slugify(text: str) -> str:
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    return re.sub(r"-+", "-", slug).strip("-")


def render(user: User):
    container = ui.column().classes("w-full")

    async def refresh():
        await _render_page_list(container, user)

    ui.timer(0, refresh, once=True)


async def _render_page_list(container, user: User):
    container.clear()
    can_manage = await has_permissions(user, MANAGE_PAGES)
    pages = await list_pages(published_only=not can_manage)

    with container:
        with ui.row().classes("items-center justify-between w-full mb-3"):
            ui.label(t("pages")).classes("text-h6")
            if can_manage:
                ui.button(
                    t("new_page"), icon="add",
                    on_click=lambda: _show_editor(container, user),
                ).props("flat color=primary")

        if not pages:
            ui.label(t("page_not_found")).classes("text-grey")
            return

        for page in pages:
            with ui.card().classes("w-full q-py-sm q-px-md mb-2"):
                with ui.row().classes("items-center justify-between w-full"):
                    with ui.row().classes("items-center gap-2"):
                        ui.link(page.title, f"/pages/{page.slug}").classes(
                            "text-subtitle1 font-bold"
                        )
                        if not page.published:
                            ui.badge(t("page_draft"), color="orange").props("dense")
                    if can_manage:
                        with ui.row().classes("gap-1"):
                            ui.button(
                                icon="edit",
                                on_click=lambda p=page: _show_editor(container, user, p),
                            ).props("flat dense round color=primary size=sm")

                            async def do_delete(p=page):
                                await delete_page(p.id)
                                ui.notify(t("page_deleted"), color="positive")
                                await _render_page_list(container, user)

                            ui.button(
                                icon="delete", on_click=do_delete,
                            ).props("flat dense round color=negative size=sm")


async def _show_editor(container, user: User, page=None):
    editing = page is not None

    with ui.dialog() as dialog, ui.card().classes("w-[700px]"):
        ui.label(t("edit_page") if editing else t("new_page")).classes("text-h6")

        title_input = ui.input(
            t("page_title"), value=page.title if editing else "",
        ).props("outlined dense").classes("w-full")

        slug_input = ui.input(
            t("page_slug"), value=page.slug if editing else "",
        ).props("outlined dense").classes("w-full")

        if not editing:
            title_input.on_value_change(
                lambda e: slug_input.set_value(_slugify(e.value))
            )

        content_input = ui.textarea(
            t("page_content"), value=page.content if editing else "",
        ).props("outlined").classes("w-full").style("min-height: 300px")

        with ui.row().classes("items-center gap-4"):
            order_input = ui.number(
                t("page_sort_order"), value=page.sort_order if editing else 0,
            ).props("outlined dense").classes("w-32")
            published_toggle = ui.switch(
                t("page_published"), value=page.published if editing else False,
            )

        with ui.row().classes("justify-end gap-2 mt-2"):
            ui.button(t("cancel"), on_click=dialog.close).props("flat")

            async def do_save():
                if not title_input.value.strip():
                    ui.notify(t("required_field"), color="negative")
                    return
                slug_val = slug_input.value.strip() or _slugify(title_input.value)
                try:
                    if editing:
                        await update_page(
                            page.id,
                            title=title_input.value.strip(),
                            slug=slug_val,
                            content=content_input.value,
                            sort_order=int(order_input.value or 0),
                            published=published_toggle.value,
                        )
                    else:
                        await create_page(
                            title=title_input.value.strip(),
                            slug=slug_val,
                            content=content_input.value,
                            author_id=user.id,
                            sort_order=int(order_input.value or 0),
                            published=published_toggle.value,
                        )
                except ValueError as e:
                    ui.notify(str(e), color="negative")
                    return
                ui.notify(t("page_saved"), color="positive")
                dialog.close()
                await _render_page_list(container, user)

            ui.button(t("save"), on_click=do_save).props("color=primary")

    dialog.open()
```

- [ ] **Step 2: Wire Pages tab into shell.py**

In `not_dot_net/frontend/shell.py`, add the import at the top:

```python
from not_dot_net.frontend.pages import render as render_pages
```

In `setup()`, add `pages_label` alongside the other labels:

```python
        pages_label = t("pages")
```

Add `pages_label` to `available_tabs` (for all users, after `bookings_label`):

```python
        available_tabs = [dashboard_label, people_label, bookings_label, pages_label]
```

Add the tab header (after the bookings tab):

```python
                ui.tab(pages_label, icon="article")
```

Add the tab panel (after the bookings panel):

```python
            with ui.tab_panel(pages_label):
                render_pages(user)
```

- [ ] **Step 3: Run all tests**

Run: `uv run pytest -v`
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add not_dot_net/frontend/pages.py not_dot_net/frontend/shell.py
git commit -m "feat: add Pages tab with editor for authorized users"
```

---

### Task 5: Public Page Route

**Files:**
- Modify: `not_dot_net/app.py`

- [ ] **Step 1: Add public `/pages/{slug}` route in app.py**

In `not_dot_net/app.py`, after `setup_login()` / `setup_shell()` / etc., add a public NiceGUI page. First add the import at the top:

```python
from not_dot_net.frontend.i18n import t as i18n_t
```

Then after `setup_wizard()` (or inside the `create_app` function, near the end), add:

```python
    @ui.page("/pages/{slug}")
    async def public_page(slug: str):
        from not_dot_net.backend.page_service import get_page
        page = await get_page(slug)
        if page is None or not page.published:
            ui.colors(primary="#0F52AC")
            with ui.column().classes("absolute-center items-center"):
                ui.icon("error", size="xl", color="negative")
                ui.label(i18n_t("page_not_found")).classes("text-h6")
            return

        ui.colors(primary="#0F52AC")
        with ui.column().classes("w-full max-w-3xl mx-auto pa-6"):
            with ui.row().classes("items-center gap-2 mb-4"):
                ui.link("← LPP Intranet", "/").classes("text-primary")
            ui.label(page.title).classes("text-h4 text-weight-light mb-4").style(
                "color: #0F52AC"
            )
            ui.markdown(page.content).classes("w-full")
```

- [ ] **Step 2: Run all tests**

Run: `uv run pytest -v`
Expected: all pass

- [ ] **Step 3: Commit**

```bash
git add not_dot_net/app.py
git commit -m "feat: add public /pages/{slug} route for markdown pages"
```

---

### Task 6: Full Integration Test

**Files:**
- Create: `tests/test_pages_integration.py`

- [ ] **Step 1: Write integration test**

```python
# tests/test_pages_integration.py
"""Integration tests for custom pages feature."""

from not_dot_net.backend.page_service import create_page, get_page, list_pages, MANAGE_PAGES


async def test_full_page_lifecycle():
    page = await create_page(
        title="FAQ", slug="faq", content="## FAQ\n\nNothing yet.",
        author_id=None, published=False,
    )

    # Not visible in published list
    published = await list_pages(published_only=True)
    assert not any(p.slug == "faq" for p in published)

    # Visible in all-pages list
    all_p = await list_pages(published_only=False)
    assert any(p.slug == "faq" for p in all_p)

    # Publish it
    from not_dot_net.backend.page_service import update_page
    await update_page(page.id, published=True)

    # Now visible
    published = await list_pages(published_only=True)
    assert any(p.slug == "faq" for p in published)

    # Public fetch by slug
    fetched = await get_page("faq")
    assert fetched is not None
    assert fetched.published is True

    # Delete
    from not_dot_net.backend.page_service import delete_page
    await delete_page(page.id)
    assert await get_page("faq") is None


async def test_manage_pages_permission_exists():
    from not_dot_net.backend.permissions import get_permissions
    perms = get_permissions()
    assert MANAGE_PAGES in perms
    assert perms[MANAGE_PAGES].label == "Manage pages"
```

- [ ] **Step 2: Run all tests**

Run: `uv run pytest -v`
Expected: all pass (including the new integration tests + existing 198 tests)

- [ ] **Step 3: Commit**

```bash
git add tests/test_pages_integration.py
git commit -m "test: add integration tests for custom pages"
```

- [ ] **Step 4: Run full test suite one final time**

Run: `uv run pytest -q`
Expected: all pass, 0 failures
