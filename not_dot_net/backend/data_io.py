"""Import/export pages and booking resources as JSON."""

import json
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from not_dot_net.backend.booking_models import Resource
from not_dot_net.backend.db import session_scope
from not_dot_net.backend.page_models import Page


def _serialize_page(p: Page) -> dict:
    return {
        "title": p.title,
        "slug": p.slug,
        "content": p.content,
        "sort_order": p.sort_order,
        "published": p.published,
    }


def _serialize_resource(r: Resource) -> dict:
    return {
        "name": r.name,
        "resource_type": r.resource_type,
        "description": r.description,
        "location": r.location,
        "specs": r.specs,
        "active": r.active,
    }


async def export_pages() -> list[dict]:
    async with session_scope() as session:
        result = await session.execute(select(Page).order_by(Page.sort_order, Page.title))
        return [_serialize_page(p) for p in result.scalars().all()]


async def export_resources() -> list[dict]:
    async with session_scope() as session:
        result = await session.execute(select(Resource).order_by(Resource.name))
        return [_serialize_resource(r) for r in result.scalars().all()]


async def export_all() -> dict:
    return {
        "version": 1,
        "exported_at": datetime.utcnow().isoformat(),
        "pages": await export_pages(),
        "resources": await export_resources(),
    }


async def import_pages(data: list[dict], *, replace: bool = False) -> dict[str, int]:
    created, updated, skipped = 0, 0, 0
    async with session_scope() as session:
        for item in data:
            slug = item.get("slug", "").strip()
            if not slug:
                skipped += 1
                continue
            existing = (await session.execute(
                select(Page).where(Page.slug == slug)
            )).scalar_one_or_none()
            if existing:
                if replace:
                    existing.title = item.get("title", existing.title)
                    existing.content = item.get("content", existing.content)
                    existing.sort_order = item.get("sort_order", existing.sort_order)
                    existing.published = item.get("published", existing.published)
                    updated += 1
                else:
                    skipped += 1
            else:
                session.add(Page(
                    title=item["title"],
                    slug=slug,
                    content=item.get("content", ""),
                    sort_order=item.get("sort_order", 0),
                    published=item.get("published", False),
                ))
                created += 1
        await session.commit()
    return {"created": created, "updated": updated, "skipped": skipped}


async def import_resources(data: list[dict], *, replace: bool = False) -> dict[str, int]:
    created, updated, skipped = 0, 0, 0
    async with session_scope() as session:
        for item in data:
            name = item.get("name", "").strip()
            if not name:
                skipped += 1
                continue
            existing = (await session.execute(
                select(Resource).where(Resource.name == name)
            )).scalar_one_or_none()
            if existing:
                if replace:
                    existing.resource_type = item.get("resource_type", existing.resource_type)
                    existing.description = item.get("description", existing.description)
                    existing.location = item.get("location", existing.location)
                    existing.specs = item.get("specs", existing.specs)
                    existing.active = item.get("active", existing.active)
                    updated += 1
                else:
                    skipped += 1
            else:
                session.add(Resource(
                    name=name,
                    resource_type=item.get("resource_type", "desktop"),
                    description=item.get("description"),
                    location=item.get("location"),
                    specs=item.get("specs"),
                    active=item.get("active", True),
                ))
                created += 1
        await session.commit()
    return {"created": created, "updated": updated, "skipped": skipped}


async def import_all(data: dict, *, replace: bool = False) -> dict:
    result = {}
    if "pages" in data:
        result["pages"] = await import_pages(data["pages"], replace=replace)
    if "resources" in data:
        result["resources"] = await import_resources(data["resources"], replace=replace)
    return result
