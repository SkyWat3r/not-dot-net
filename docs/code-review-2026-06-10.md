# Code Review — 2026-06-10

Full-codebase review (bugs, pragmatic security, UX, refactoring, test gaps).
Supersedes nothing; complements `docs/backlog.md` (2026-04-11), whose items are
mostly fixed. Issue numbers below are referenced by the fix commits.

## Bugs

### R-01: AD effects credential prompt fires after the step is already committed
- **Files:** `backend/workflow_service.py` (commit at ~566, `run_effects` at ~620),
  `backend/workflow_effects.py:178`, `frontend/workflow_detail.py:280`
- `submit_step` commits the transition, event, and audit row, and only then calls
  `run_effects`, which raises `AdCredentialsRequired` when credentials are missing.
  The frontend catches it, prompts, and retries the whole `submit_step`. The request
  has already advanced: terminal steps double-log the action and re-fire
  notifications; mid-workflow steps fail the retry with "Action not allowed". The
  AD effect from the first submit is silently lost. The seeder works around this by
  always passing `ad_creds=("seed", "seed")`.
- **Fix:** pre-check matching effects + missing creds before the transaction
  (mirror the `ad_account_creation` pre-check).

### R-02: Returning-person search in onboarding is dead code
- **File:** `frontend/new_request.py:82, 99-129`
- `prefill.update(await _render_returning_search(fc))` copies the returned dict
  while still empty; later mutations by `select_user` are invisible. Submitted data
  comes solely from form widgets, so `returning_user_id` / prefilled
  `contact_email` never reach `create_request`. The backend consumer
  (`workflow_service.py` `returning_user_id` branch) is only reachable from tests.

### R-03: `start_role` advertised but never enforced
- **File:** `frontend/new_request.py:44`, editor tooltip `i18n.py:325`
- Editor says "Anyone with this role sees this workflow on the new-request page";
  the page only checks `create_workflows`, identically for every workflow.

### R-04: Booking reminders may never fire
- **File:** `app.py:102`, `backend/booking_service.py:310`
- APScheduler `interval, hours=24` with no `next_run_time`: first run is 24 h after
  pod start and every restart resets the clock. The job also skips a lead day
  forever if it wasn't running on that exact day (`days_until_end not in lead_days`).
- **Fix:** cron trigger at a fixed hour + send when `days_until_end <= lead` and
  that lead not yet recorded.

### R-05: Encrypted-file retention is dead machinery
- **File:** `backend/encrypted_storage.py:163`
- `mark_for_retention` sets `retained_until` on workflow completion, but nothing
  ever calls `delete_expired()` — personal documents are kept forever.

### R-06: The "already logged in" redirect on /login is dead
- **File:** `frontend/login.py:183`, `frontend/shell.py:160`
- `app.storage.user["authenticated"]` is only ever set to `False`; auth is
  cookie-based. Logged-in users always see the login form again.

### R-07: Booking end-date displayed off by one
- **Files:** `frontend/bookings.py` (submit converts picker "to" to exclusive
  end +1 day), cards and reminder email display raw `end_date`
- A booking made for "June 10 → 12" displays as "June 10 → 13" everywhere after
  creation. Conflict math is consistent; only display semantics are wrong.

## Security (pragmatic intranet level)

### R-08: Initial AD password emailed in cleartext and retained forever
- **Files:** `workflow_service.py` (`account_created` send), `notifications.py`
  template, `backend/mail_outbox.py` (sent rows never purged)
- The temp password lands in the recipient mailbox and stays in `mail_outbox`
  indefinitely. `must_change_password` mitigates, but a newcomer who never logs in
  leaves a valid credential in the DB.
- **Fix:** drop the password from the email (operator hands it over via the copy
  dialog) and purge sent outbox rows older than ~30 days.

### R-09: CSRF middleware is dead code
- **File:** `backend/csrf.py` — never wired; deliberately disabled in March 2026
  ("NiceGUI's ASGI stack doesn't tolerate additional middleware wrapping"), the
  explanatory comment was lost in `d8e27b7`. `OrgConfig.allowed_origins` is also
  dead (the real allowlist is the `ALLOWED_ORIGINS` env var).
- Actual exposure: login CSRF only — UI actions go over Socket.IO, origin-locked
  in production. **Fix:** delete both; update CLAUDE.md.

### R-10: Newer callbacks skip the permission re-check pattern
- Photo upload/remove + user delete in `frontend/directory.py`, software-config
  save in `frontend/bookings.py`. `save_profile_photo`/`remove_profile_photo`
  have no actor check server-side at all.

### R-11: Token submits can inject arbitrary keys into `req.data`
- `submit_step`/`save_draft` merge the whole client dict. A token holder can set
  e.g. `returning_user_id` (changes whose tenure record is created on completion).
- **Fix:** restrict merged keys to the current step's field names.

## UX

- **R-12** Verification-code lockout dead end: after 5 failed attempts the page
  offers "Send code", which refuses with "code already sent". Show "too many
  attempts, retry later" instead. (`verification.py`, `workflow_token.py`)
- **R-13** Free-text date fields in directory edit + tenure dialogs crash on
  typos (`date.fromisoformat` unhandled).
- **R-14** 1-hour hard session expiry (`users.py` cookie_max_age + JWT lifetime);
  silently logs people out mid-workday.
- **R-15** "My Requests" shows everyone's requests for `view_audit_log` holders
  under an unchanged title. (`dashboard.py`)

## Refactoring

- **R-16** `cancel_booking` legacy `user_id`/`is_admin` path (backlog B-29); all
  UI callers pass `actor`.
- **R-17** Booking detail does per-booking owner lookups (B-31); the card grid
  batches correctly — unify on `resolve_actor_names` (F-01).
- **R-18** All ldap3 calls are synchronous on the event loop; a slow DC freezes
  the UI for everyone. Wrap bulk sync / account creation in `asyncio.to_thread`.
- **R-19** Profile photos fully processed twice per upload (validate then save).
- **R-20** `workflow_service.py` (1,100 lines) mixes seed workflow configs,
  upload validation, service layer, AD account creation — split.

## Test gaps

- `submit_step` with effects + missing AD creds incl. prompt-retry (R-01).
- New-request returning-person flow end to end (R-02).
- Reminder scheduling semantics across restarts (R-04); retention job (R-05).
- B-T3 (CSRF) resolves itself when the dead middleware is deleted (R-09).

## Fix status (2026-06-10)

All items fixed in the commit series following this review, each with a
reproducer test, except:

- **R-19** (photo processed twice per upload) — deliberately skipped: a
  one-off re-encode of a ≤2 MB image per upload is negligible, and avoiding
  it would force an awkward validate/save API split (KISS).
- **R-18** applied to bulk operations only (AD bulk sync, UID seeding,
  account creation, step effects). Per-login LDAP auth and single-attribute
  directory writes stay on the event loop: they are fast, and the cached
  per-user connections are not thread-safe to share.
- **R-03** resolved by removing `start_role` (never enforced; enforcing it
  retroactively would have locked existing deployments' persisted
  `start_role: staff` rows to staff-only visibility).

Bonus finding while reproducing R-02: NiceGUI user-fixture tests ran
against ./dev.db (app main() rebound the engine after the conftest
installed the in-memory one). Fixed in conftest; reproducer in
tests/test_db_isolation.py.
