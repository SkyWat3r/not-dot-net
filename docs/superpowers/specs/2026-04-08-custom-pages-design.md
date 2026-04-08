# Custom Markdown Pages

## Goal

Allow authorized users to create and edit markdown pages that are rendered and visible to anyone (including unauthenticated users). Pages are discoverable from a "Pages" tab in the authenticated shell and directly accessible at public URLs.

## Data Model

Single `page` table:

| Column       | Type         | Notes                                          |
|--------------|--------------|-------------------------------------------------|
| `id`         | UUID         | PK, stable identifier (future attachment FK)   |
| `slug`       | str(200)     | unique, URL-safe, used in `/pages/<slug>`      |
| `title`      | str(200)     | display title                                  |
| `content`    | Text         | markdown body                                  |
| `sort_order` | int          | ordering in the Pages tab listing, default 0   |
| `published`  | bool         | only published pages are publicly visible      |
| `author_id`  | UUID FK      | references `user.id`                           |
| `created_at` | datetime     | server default                                 |
| `updated_at` | datetime     | server default, updated on modification        |

The UUID is intentionally stable to support a future `attachments` table referencing pages.

## Permission

`manage_pages` — registered via `permission()` in the service module. Controls create, edit, and delete. Follows the existing pattern (e.g. `MANAGE_BOOKINGS` in `booking_service.py`).

## Service Layer

`backend/page_service.py`:

- `list_pages(published_only=True)` — ordered by `sort_order` then `title`
- `get_page(slug)` — returns page or None
- `create_page(title, slug, content, author_id, sort_order, published)` — validates slug uniqueness
- `update_page(page_id, **fields)` — partial update
- `delete_page(page_id)` — hard delete

No permission checks in service functions — callers (frontend/routes) handle authorization. Same pattern as `booking_service.py`.

## Frontend

### Pages Tab (authenticated shell)

Visible to all authenticated users in the main shell tabs (alongside Dashboard, People, etc.).

- Lists published pages as clickable items (title, sorted by `sort_order` then `title`)
- Users with `manage_pages` permission see:
  - "New Page" button
  - Edit and delete controls per page
  - Unpublished (draft) pages shown with a visual indicator

### Standalone Public Route (`/pages/<slug>`)

- No authentication required
- Renders the page title + markdown content
- Returns 404 if slug not found or page not published
- Uses `ui.markdown()` for rendering

### Editor

Shown inline in the Pages tab for users with `manage_pages`:

- Title input
- Slug input (auto-generated from title, editable)
- Markdown textarea
- Sort order (number)
- Published toggle
- Save / Cancel buttons

## Files to Create/Modify

**New files:**
- `backend/page_models.py` — Page SQLAlchemy model
- `backend/page_service.py` — CRUD functions + `manage_pages` permission
- `frontend/pages.py` — Pages tab renderer + editor

**Modified files:**
- `backend/db.py` — import `page_models` to register with Base
- `frontend/shell.py` — add Pages tab, import renderer
- `app.py` — add public `/pages/<slug>` route (standalone, no auth)

## Future Considerations

- Per-page visibility (public vs auth-required) — not needed now
- File attachments stored on disk, linked via `page.id` FK
