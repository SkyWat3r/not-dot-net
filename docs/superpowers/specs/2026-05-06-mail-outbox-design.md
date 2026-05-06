# Mail outbox

## Goal

Decouple outbound mail from the action that triggers it. Mail must:

- never crash the caller,
- survive a server restart,
- eventually be delivered (or visibly fail) without admin SQL.

`backend.mail.send_mail` becomes the single public API and is an *enqueue*
operation: it INSERTs a row into `mail_outbox` and returns. A background
worker, started in `app.startup`, drains the table with a capped
exponential-backoff retry policy.

## Non-goals

- Multi-replica worker safety. Single pod is the deployment model; if it
  ever grows to N>1, the worker query needs `SELECT ... FOR UPDATE SKIP
  LOCKED` (or move to a separate worker pod). Out of scope.
- Per-mail priority, scheduling, or send windows.
- Templating in the queue. Bodies are rendered at enqueue time and stored
  as ready-to-send HTML.
- Action buttons in the admin UI (requeue, cancel, edit). Read-only v1.

## Schema

One Alembic migration adds `mail_outbox`:

| column | type | notes |
|---|---|---|
| `id` | UUID PK | client-generated (`default_factory=uuid.uuid4`) |
| `to_address` | varchar(255) | recipient |
| `subject` | varchar(500) | |
| `body_html` | text | rendered at enqueue time |
| `created_at` | timestamp | `server_default=func.now()`, index |
| `attempts` | int | starts 0, incremented per send attempt |
| `next_attempt_at` | timestamp | initially equals `created_at`; bumped on retry |
| `sent_at` | timestamp NULL | populated when SMTP returns OK |
| `failed_at` | timestamp NULL | populated when `attempts >= 7` after a failure (≈31h total) |
| `last_error` | text NULL | last SMTP exception summary, truncated to 1KB |

Indexes:

- `(sent_at, failed_at, next_attempt_at)` for the worker's poll query.
- `(failed_at)` for the admin "Failed" tab.

## API

`backend/mail.py:send_mail(to, subject, body_html)`:

- Drops the `mail_settings: MailConfig` argument. The worker loads the
  current `MailConfig` at send time so admin SMTP changes apply to
  already-pending rows.
- INSERTs a row, returns the new `id`.
- Stays async-shaped (`async def`) so existing call sites compile
  unchanged.

The four current callers — `backend/notifications.notify`,
`backend/security_alerts.send_security_alert`,
`backend/workflow_service._send_token_link`,
`frontend/workflow_token.send_code` — drop their `await mail_config.get()`
plumbing and just call `await send_mail(to, subject, body)`.

The `_BACKGROUND_ALERT_TASKS` set + `queue_security_alert` helper in
`backend/security_alerts.py` are removed; the outbox replaces them.
`notify_superuser_login_success` / `_failed` / `_granted` await
`send_mail` directly (which now enqueues).

## Worker

`backend/mail_outbox.py` (new):

- `_drain_outbox_once()`: pure step that reads ≤50 rows where
  `sent_at IS NULL AND failed_at IS NULL AND next_attempt_at <= now()`,
  ordered by `next_attempt_at`, and processes each. Per row:
  - Load `MailConfig`. If `dev_mode`, log and set `sent_at`.
  - Otherwise call `aiosmtplib.send`; on success set `sent_at`; on
    exception increment `attempts`, set `last_error[:1024]`, then: if
    `attempts >= 7` set `failed_at` and leave `next_attempt_at` as-is;
    otherwise set `next_attempt_at = now() + BACKOFF[attempts-1]`.
  - Per-row commits, so one bad row doesn't roll back the others.
- `BACKOFF = [60s, 5m, 15m, 1h, 6h, 24h]` (6 retry waits → max 7 send
  attempts, total worst-case ≈31h before giving up).
- `run_outbox_worker()`: top-level coroutine. Sleeps until the soonest
  pending row's `next_attempt_at`, capped at 60s ceiling so freshly
  enqueued rows are picked up promptly. After waking, calls
  `_drain_outbox_once()`. Catches and logs per-iteration exceptions; the
  loop never exits unless cancelled.

`app.py:startup` schedules `asyncio.create_task(run_outbox_worker())`,
keeps the task in a module-level set for GC, and cancels it in
`shutdown`.

## Admin UI

`frontend/admin_settings.py` already renders the `MailConfig` form. Below
that form, add an "Outbox" section: a dense Quasar table with a tab
selector for *Pending* and *Failed*.

- Pending query: `sent_at IS NULL AND failed_at IS NULL`, ordered by
  `created_at`, limited to 100.
- Failed query: `failed_at IS NOT NULL`, ordered by `failed_at DESC`,
  limited to 100.
- Columns: created_at, recipient, subject (truncated), attempts,
  next_attempt_at (pending) or failed_at (failed), last_error (truncated,
  expandable).

No action buttons. Permission gate: `manage_settings` (the same as the
SMTP form above it).

## Failure modes and observability

- DB unreachable on enqueue: `send_mail` raises. The caller is already in
  a bad state (DB is unreachable for everything else too), so this is
  acceptable.
- Worker startup failure: surfaces as an unhandled exception in
  `app.startup`, crashing the pod. K8s will restart and an admin will
  notice — louder than silent failure.
- Worker per-iteration failure: caught, logged via `logger.exception`,
  loop continues.
- Per-row send failure: caught, recorded on the row, loop continues.

## Testing

- `tests/test_mail_outbox.py` (new):
  - `send_mail` inserts a row with `sent_at IS NULL` and
    `next_attempt_at <= now()`.
  - `_drain_outbox_once` in dev mode marks the row `sent_at`.
  - `_drain_outbox_once` with a patched failing `aiosmtplib.send`
    increments `attempts` and bumps `next_attempt_at`.
  - After 7 consecutive failures, `failed_at` is set and the row stops
    being polled.
  - `last_error` truncates payloads >1KB.
- `tests/test_workflow_notifications_integration.py`,
  `tests/test_security_alerts.py`,
  `tests/test_resend_notification.py`: existing tests patch `send_mail`
  at the import site; they keep working since `send_mail` is still the
  public API. Where tests want to assert "the mail was sent",
  they migrate to inspecting the `mail_outbox` row.
- `tests/test_app_startup` (or equivalent): worker task is started in
  `app.startup` and cancelled in `shutdown`.
- Migration test: new table round-trips an empty insert + select.

## Migration and rollout

Single Alembic migration `0012_add_mail_outbox.py`. No backfill needed —
the new table starts empty. The dev SQLite path adds the table via
`Base.metadata.create_all` automatically.

Existing in-flight `_BACKGROUND_ALERT_TASKS` (in-memory) are dropped at
deploy time. Acceptable: those tasks are best-effort by design and the
window is the deploy itself.

## Out of scope (potential follow-ups)

- Action buttons (requeue / cancel) in the admin UI.
- Per-mail priority or scheduled-send timestamps.
- Multi-replica worker.
- Mail templates as DB rows (currently they live in
  `backend/notifications.py`'s `EMAIL_TEMPLATES` dict).
- Bounce / delivery-receipt handling.
