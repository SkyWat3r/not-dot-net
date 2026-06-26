# Code Review Backlog

Findings from full codebase review (2026-04-11). Ordered by severity within each category.

**Fixed (2026-04-11):** B-01, B-02, B-03, B-04, B-05, B-07. B-06 confirmed false positive.
**Fixed (2026-04-11):** B-08, B-09, B-10, B-11, B-12, B-13, B-14, B-15, B-16, B-17.
**Fixed (2026-04-15):** B-19 — base_url moved to MailConfig, threaded through notify().
**Fixed (2026-06-26):** B-18, B-22, B-32, B-34, B-36, and B-T2/B-T4/B-T7.
B-T2 turned out to be a real bug, not just a coverage gap: `submit_step`
had no terminal-status guard, so a rejected request could be "approved"
back to completed. Earlier fixes confirmed still in place: B-20, B-21,
B-23 (admin role removed), B-24, B-25, B-26, B-27, B-28, B-29, B-31, B-35, F-01.
Still open by deliberate choice: B-33 (cleartext token, accepted phase 28);
and F-02/F-03/F-04 (minor factorization — F-03 already allowlist-guarded).

---

## Critical — Security / Correctness

### B-01: `can_user_act` grants access to everyone on role/permission-gated steps
- **File:** `backend/workflow_engine.py:75-76`
- Returns `True` for any user when step has `assignee_permission` or `assignee_role`. Service layer never re-checks. Any authenticated user can approve any workflow request.
- **Fix:** Make `can_user_act` async, call `has_permissions()` / check `user.role` inside it.

### B-02: Token-based submit bypasses all authorization
- **Files:** `frontend/workflow_token.py:44-47`, `backend/workflow_service.py:228`
- Token page calls `submit_step` with `actor_user=None`; auth check is skipped entirely. `actor_token` is never validated against `req.token`.
- **Fix:** Add `actor_token` param to `submit_step`, validate it matches `req.token` + expiry. Reject when both `actor_user` and `actor_token` are `None`.

### B-03: Path traversal in file download
- **File:** `backend/workflow_file_routes.py:52-55`
- `storage_path` from DB used directly — no containment check against upload directory.
- **Fix:** Resolve against upload root, assert `path.is_relative_to(upload_root)`.

### B-04: XSS in login page redirect
- **File:** `frontend/login.py:81`
- `safe_dest` from query params injected unescaped into HTML attribute.
- **Fix:** HTML-escape or `urllib.parse.quote` before interpolation.

### B-05: `cookie_secure=False` in production
- **File:** `backend/users.py:64-68`
- Auth cookie transmitted over plain HTTP, interceptable on untrusted networks.
- **Fix:** Set `cookie_secure=True` or derive from `dev_mode`.

### ~~B-06: Booking conflict detection is broken (TOCTOU)~~ — FALSE POSITIVE
- **File:** `backend/booking_service.py:131-155`
- `session.begin()` on a fresh session (no prior autobegin) creates a real transaction, not a savepoint. The conflict check + insert are atomic within the `async with session.begin()` block. `with_for_update()` is a no-op on SQLite but correct for PostgreSQL. Pattern is correct as-is.

### B-07: No authorization on `create_booking`
- **File:** `backend/booking_service.py:119-164`
- No `actor` param, no permission check. Any `user_id` can be passed — allows impersonating other users.
- **Fix:** Add `actor` param, enforce `user_id == actor.id or has MANAGE_BOOKINGS`.

---

## High — Auth / Authorization / Data Integrity

### B-08: LDAP login doesn't check `user.is_active`
- **File:** `backend/auth/ldap.py:91-101`
- Local login checks `is_active`; LDAP does not. Disabled users with valid LDAP credentials get a working JWT.
- **Fix:** Add `if not user or not user.is_active:` guard.

### ~~B-09: CSRF skip prefix too broad~~ — RESOLVED 2026-06-10 (R-09)
`backend/csrf.py` deleted: it was never wired into the app (disabled March 2026
for NiceGUI ASGI compat). See docs/code-review-2026-06-10.md R-09.

### B-09 (original): CSRF skip prefix too broad
- **File:** `backend/csrf.py:15`
- `/auth/` prefix exempts cookie login endpoint from CSRF protection.
- **Fix:** Narrow to `/auth/jwt/`, `/auth/local`, `/auth/ldap` only.

### B-10: `_build_actionable_filters` ignores `assignee_role` steps
- **File:** `backend/workflow_service.py:432-447`
- Steps using `assignee_role` without `assignee_permission` silently skipped — users assigned by role never see their requests in the dashboard.
- **Fix:** Add `elif step.assignee_role and user.role == step.assignee_role:` branch.

### B-11: Notification exceptions silently swallowed
- **File:** `backend/workflow_service.py:286-289`
- Bare `except Exception: pass`. SMTP errors, template errors, DB failures all invisible.
- **Fix:** `logger.exception(...)` at minimum.

### B-12: No permission re-check in page mutation callbacks
- **File:** `frontend/pages.py:70-135`
- Save/delete callbacks don't re-verify `MANAGE_PAGES`. Permission could be revoked between page load and callback execution.
- **Fix:** Add `check_permission(user, MANAGE_PAGES)` at the top of each callback.

### B-13: LDAP bind DN not sanitized
- **File:** `backend/auth/ldap.py:35`
- `username` from HTTP input used directly in bind DN. Safe for UPN-style only; latent injection if DN format changes.
- **Fix:** Validate with strict allowlist regex (`^[a-zA-Z0-9._-]{1,64}$`).

### B-14: Mass-assignment via `**kwargs` in update functions
- **Files:** `backend/booking_service.py:63-75`, `backend/page_service.py:56-66`
- `setattr` from unchecked kwargs allows overwriting `id`, `created_at`, etc.
- **Fix:** Accept explicit parameters or maintain an allowlist of mutable fields.

### B-15: Draft pages leakable via `/pages/{slug}`
- **File:** `frontend/public_page.py:23`
- Unpublished pages fetched and checked client-side. Existence of draft slugs is leakable.
- **Fix:** Filter `published=True` at the DB query layer.

### B-16: Non-unique `row_key` in audit log table
- **File:** `frontend/audit_log.py:83`
- Using `"time"` as row key causes table rendering corruption when events share timestamps.
- **Fix:** Use event `id` or a composite key.

### B-17: Stale user count allows deleting roles with active users
- **File:** `frontend/admin_roles.py:119`
- Role deletion checks user count at fetch time but deletes later. Concurrent assignment could orphan users.
- **Fix:** Re-check count inside the delete transaction, or use a DB constraint.

---

## Medium — Robustness / Correctness

### ~~B-18: Race condition on concurrent `submit_step`~~ — FIXED 2026-06-26
- **File:** `backend/workflow_service.py`
- `submit_step` and `save_draft` now load the row with `session.get(..., with_for_update=True)` — no-op on SQLite, real `FOR UPDATE` on PostgreSQL. Not unit-testable on SQLite; the terminal-status guard (B-T2) additionally blocks the common double-click case.

### B-19: `base_url` hardcoded to `localhost:8088` in notifications
- **File:** `backend/notifications.py:89`
- All token link emails broken in production.
- **Fix:** Read from `OrgConfig` or `MailConfig` and thread through `_fire_notifications`.

### B-20: `save_draft` doesn't validate `partial_save` flag
- **File:** `backend/workflow_service.py:294-330`
- Any step accepts partial saves even when not configured for it.
- **Fix:** Check `step.partial_save` and raise `ValueError` if not set.

### B-21: `save_draft` token auth unenforced
- **File:** `backend/workflow_service.py:310`
- `actor_token` never compared to `req.token` — any caller without `actor_user` gets a free pass.
- **Fix:** Validate `actor_token == req.token` + check expiry.

### ~~B-22: TOCTOU in `ConfigSection.set`~~ — FIXED 2026-06-26
- **File:** `backend/app_config.py`
- `set()` now catches `IntegrityError` on the first-insert path, rolls back, and falls back to updating the row a concurrent writer inserted (same pattern as B-28). One-time-per-prefix race window.

### B-23: In-memory lockout repair never persisted
- **File:** `backend/roles.py:42-51`
- `_enforce_admin_lockout` patches the in-memory object but never writes back. DB stays corrupted.
- **Fix:** Persist repaired value back in `get()` when a repair was made.

### B-24: Audit ORM objects mutated in-session
- **File:** `backend/audit.py:131-143`
- Setting `actor_email` / `_target_display` on ORM objects can dirty-write back to audit DB.
- **Fix:** Project to a plain dataclass/DTO at the boundary.

### B-25: `sys.exit(1)` in library function
- **File:** `backend/secrets.py:33-35`
- Kills process, breaks testability.
- **Fix:** Raise `FileNotFoundError`, let CLI catch and exit.

### B-26: No guard against `--seed-fake-users` in production
- **File:** `backend/seeding.py:30`
- 100 users with password "dev" can be created on a production DB.
- **Fix:** Assert `dev_mode` or SQLite before seeding.

### B-27: `Resource.name` has no uniqueness constraint
- **File:** `backend/booking_models.py:15`
- Duplicate resource names can be created.
- **Fix:** Add `unique=True` to `name` column.

### B-28: Slug uniqueness TOCTOU
- **File:** `backend/page_service.py:37-53`
- Pre-check in separate transaction from insert. Concurrent creates can race.
- **Fix:** Remove pre-check, handle `IntegrityError` from the DB constraint.

### B-29: Legacy `is_admin` bool bypass in `cancel_booking`
- **File:** `backend/booking_service.py:167-181`
- Dual-path auth pattern is fragile — `is_admin=True` with no credential check.
- **Fix:** Remove legacy path, keep only `actor`-based auth.

### B-30: Async `apply_filter` wrapped in sync lambda
- **File:** `frontend/bookings.py:136-138`
- Async function in sync callback — silently never executes.
- **Fix:** Use `ui.timer` or proper async callback wiring.

### B-31: N+1 DB queries for user names in booking detail
- **File:** `frontend/bookings.py:306`
- Per-booking query for owner name. 10 bookings = 10 sequential round-trips.
- **Fix:** Batch lookup like dashboard's `resolve_actor_names`.

### ~~B-32: Token-page submissions unattributed in audit trail~~ — FIXED 2026-06-26
- **File:** `backend/workflow_service.py` (`submit_step`)
- `log_audit` now receives `actor_email=req.target_email` on token submissions, so the audit trail attributes them to the target person. (`actor_token` is deliberately NOT persisted on the event row — column dropped in migration 0009.)

### B-33: Token stored cleartext in DB
- **File:** `backend/workflow_models.py:30`
- DB read = full token compromise.
- **Fix:** Store `sha256(token)`, compare hash on lookup.

### ~~B-34: Raw Markdown in dashboard page previews~~ — FIXED 2026-06-26
- **File:** `frontend/dashboard.py`
- New `_preview_line()` helper strips inline Markdown (links, emphasis, inline code) from the first meaningful content line. Unit-tested in `tests/test_dashboard_preview.py`.

---

## Low — i18n / Polish

### B-35: Missing translation keys for admin roles
- **File:** `frontend/i18n.py`
- Keys `roles`, `role_key`, `role_label`, `default_role`, `add` missing from both locales. Raw keys displayed in admin UI.

### ~~B-36: Setup wizard not i18n'd~~ — FIXED 2026-06-26
- **File:** `frontend/setup_wizard.py`
- All five strings externalized to `setup_*` keys in `frontend/i18n.py` (EN + FR).

---

## Test Coverage Gaps

### B-T1: `submit_step` authorization completely untested (Critical)
- Tests never pass `actor_user` — the permission check path is never exercised.

### ~~B-T2: `submit_step` on completed/rejected request untested~~ — FIXED 2026-06-26 (was a real bug)
- The missing test exposed a missing guard: `submit_step` had no terminal-status check, so acting on a completed/rejected request re-executed the action (a rejected request could be "approved" back to completed). Added `if req.status != IN_PROGRESS: raise ValueError("Cannot act ...")` to `submit_step` + `save_draft`. Tests: `test_submit_step_rejects_action_on_{completed,rejected}_request`.

### ~~B-T3: CSRF middleware has zero tests~~ — RESOLVED 2026-06-10 (middleware deleted, R-09)

### B-T3 (original): CSRF middleware has zero tests (Critical)
- Custom ASGI implementation with non-trivial skip/fallback logic, entirely untested.

### ~~B-T4: Token page has zero tests~~ — CLOSED 2026-06-26
- Valid-submission page path covered by `test_fieldref_encrypted_file_stored_encrypted`; expired/unknown-token page render by `test_token_page_shows_expired_for_unknown_token`. Expiry/replay also covered at service level (`test_token_expiry.py`, `test_token_security.py`).

### B-T5: `save_draft` authorization + validation untested (High)
- Neither `actor_user` nor `actor_token` paths tested. `partial_save` flag not validated.

### B-T6: `can_view_request` permission branch untested (High)
- Active-step assignee branch with `assignee_permission` never tested.

### ~~B-T7: `list_events_batch` / `list_all_requests` untested~~ — CLOSED 2026-06-26
- `list_all_requests` covered via `test_returning_person_selection_prefills_and_submits`; `list_events_batch` now has `test_list_events_batch_groups_by_request` + `test_list_events_batch_empty`.

---

## Factorization Opportunities

### F-01: Unify user name resolution
- `audit._resolve_names`, `bookings._get_user_name`, `dashboard.resolve_actor_names` all do user-id-to-name lookups differently.
- **Target:** Single `resolve_user_names(ids) -> dict[UUID, str]` utility. Eliminates N+1 and ORM mutation hack.

### F-02: Standardize service-layer auth
- `create_booking` has no auth, `cancel_booking` has dual-path auth, `create_resource` uses `actor`.
- **Target:** Every mutating service function takes `actor: User`, checks permission once.

### F-03: Replace `**kwargs` update pattern
- `update_resource` and `update_page` both use fragile `setattr` loop.
- **Target:** Explicit typed params, or shared `apply_updates(obj, allowed_fields, values)` helper.

### F-04: Standardize session transaction patterns
- `session_scope()` + nested `session.begin()` vs `async_session_maker.begin()` used inconsistently.
- **Target:** Document when each is appropriate; pick one default pattern for service functions.
