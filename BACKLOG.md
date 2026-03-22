# Code Review Backlog (2026-03-22)

## Critical (Security)

- [x] **#1** LDAP injection in `backend/auth/ldap.py:49` — username interpolated into LDAP filter without escaping
- [x] **#2** XSS via `redirect_to` in `frontend/login.py:24` — query param injected into JavaScript
- [x] **#3** Open redirect in `frontend/login.py:12-14` — no validation that `redirect_to` is a relative path
- [ ] **#8** `cookie_httponly=False` in `backend/users.py:50` — reverted, NiceGUI requires JS cookie access for login flow

## High (Bugs)

- [x] **#4** `_resolve_names` in `audit.py:134-141` mutates `target_id` from UUID to display name
- [x] **#6** `authenticated` flag in `login.py` never set — dead code on line 13-14
- [x] **#7** Race condition in booking conflict check — `booking_service.py:131-138` not atomic
- [x] **#29** `workflow_engine.py:62` `compute_next_step` crashes with ValueError on unknown step

## Medium (Quality)

- [x] **#10** Repeated `asynccontextmanager(get_async_session)` boilerplate (~25 occurrences)
- [x] **#11** `local.py` re-creates CryptContext per request and duplicates FastAPI-Users auth
- [x] **#13** `list_actionable` is O(N) full table scan in `workflow_service.py:266-298`
- [x] **#15** `seed_data.py:119` runs `_generate_people(100)` at import time
- [x] **#16** Status strings as magic constants — use an enum
- [x] **#17** `requires-python = ">=3.9"` but code uses `str | None` syntax (needs 3.10+)

## Low (Cleanup)

- [x] **#12** Duplicate `TokenResponse` model in `local.py` and `ldap.py`
- [x] **#14** `_fire_notifications` N+1 queries in `workflow_service.py:20-55`
- [x] **#18** `_dev.py` has no config file parameter
- [x] **#25** `users.py` is a 294-line grab bag — split seeding logic into `seeding.py`
- [ ] **#26** `bookings.py` is 620 lines — tightly coupled UI, splitting would fragment without benefit
- [x] **#27** `dashboard.py:94` N+1 queries for events per request row
- [x] **#28** `i18n.py` translation keys are plain strings with no validation
- [ ] **#30** No CSRF protection on REST endpoints — `csrf.py` written but disabled (NiceGUI ASGI compat issue)

## Missing Tests

- [x] **#19** Booking system — zero test coverage (21 tests added)
- [x] **#20** Auth endpoints — no integration tests (6 tests added)
- [x] **#21** `audit.py` — no tests (6 tests added)
- [x] **#22** `app_settings.py` — no tests (6 tests added)
- [x] **#23** Profile edit/delete — no tests (5 tests added)
- [x] **#24** Token expiry — not tested (3 tests added)
