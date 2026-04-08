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
