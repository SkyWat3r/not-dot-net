# Onboarding v2 — Encrypted Files, 4-Step Workflow, Returning Persons

## Overview

Replace the current 3-step onboarding workflow with a production-ready 4-step flow. Add encrypted file storage for personal documents (ID, bank details). Support returning persons (pre-fill from previous account).

## Encrypted File Storage

### Module: `backend/encrypted_storage.py`

Standalone service for at-rest encryption of sensitive files. Reusable beyond onboarding.

**Crypto scheme — envelope encryption:**
- Per-file random 256-bit data encryption key (DEK)
- File content encrypted with AES-256-GCM (12-byte random nonce)
- DEK wrapped with a master key using AES-256-GCM
- Master key stored in `secrets.key` as `file_encryption_key` (32 bytes, base64-encoded)
- Library: `cryptography` (already a transitive dependency)

**DB model — `EncryptedFile`:**

| Column | Type | Notes |
|---|---|---|
| id | UUID | PK |
| wrapped_dek | LargeBinary | DEK encrypted with master key |
| nonce | LargeBinary | 12-byte GCM nonce |
| storage_path | str | Relative path to encrypted blob in `data/encrypted/` |
| original_filename | str | For download content-disposition |
| content_type | str | MIME type |
| uploaded_by | UUID, nullable | FK → user.id (null for token-based uploads) |
| uploaded_at | datetime | |
| retained_until | datetime, nullable | Set on workflow completion, deletion target |

**Service API:**
- `store_encrypted(data, filename, content_type, uploaded_by) → EncryptedFile`
- `read_encrypted(file_id) → (bytes, filename, content_type)` — logs audit event on every call
- `mark_for_retention(file_id, days)` — sets `retained_until`
- `delete_expired()` — removes blobs + DB rows past retention date

**Audit:** Every `read_encrypted` call creates an audit log entry (who, when, which file).

### Secrets Change

`AppSecrets` gains a `file_encryption_key: str` field. Auto-generated on first run alongside existing secrets.

### Access Control

New permission: `access_personal_data` — registered in `encrypted_storage.py`.

Only users with `access_personal_data` can trigger decryption. Downloads served via NiceGUI `ui.download()` callbacks, not HTTP endpoints. No public route for encrypted files.

### Removal of `/workflow/file/{file_id}` Endpoint

All workflow file downloads (encrypted or not) move to NiceGUI callbacks with permission checks. The HTTP endpoint is removed entirely.

### FieldConfig Change

New flag: `encrypted: bool = False`. When true on a file-type field, uploads go through `store_encrypted` instead of raw disk write. `WorkflowFile.encrypted_file_id` (FK → `EncryptedFile.id`, nullable) links to the encrypted blob.

## Onboarding Workflow — 4 Steps

### Step 1 — Initiation (`key: initiation`)

- **Type:** `form`
- **Assignee:** `requester`
- **Who can start:** Anyone except interns (permission-gated or role-gated in frontend)
- **Fields:**
  - `contact_email` (email, required) — newcomer's personal email
  - `status` (select, required) — extensible list via config (CDD, Intern, PhD, PostDoc, CDI, ...)
- **No name fields** — the newcomer fills in their own name in step 2
- **Returning person search:** UI shows a search box above the form. Autocomplete searches all users including `is_active=False`. Selecting a match pre-fills `contact_email` and stores `returning_user_id` in the request data. A visual indicator shows "Returning — existing account found."

### Step 2 — Newcomer Info (`key: newcomer_info`)

- **Type:** `form`
- **Assignee:** `target_person` (resolved from `contact_email`)
- **Partial save:** `true` — newcomer can save progress and return later
- **Token link:** Sent by email on step 1→2 transition, 30-day validity
- **Verification code:** On each new session:
  1. Newcomer clicks token link → sees only a "Send me a verification code" button
  2. 6-digit code emailed to `contact_email`, hashed in DB, 15-minute expiry, single use
  3. Newcomer enters code → form unlocks for the session
  4. Rate-limited to prevent brute force
  5. "Resend code" button generates a new code, invalidates the old one
- **Fields:**
  - `first_name` (text, required)
  - `last_name` (text, required)
  - `phone` (text)
  - `address` (textarea)
  - `emergency_contact` (text)
  - `id_document` (file, encrypted, required)
  - `bank_details` (file, encrypted, required for non-interns)
  - `photo` (file, encrypted)
  - Additional fields as needed
- **Per-status document instructions:** Config mapping displayed as guidance text above file fields:
  ```python
  document_instructions: dict[str, list[str]] = {
      "Intern": ["ID document", "Internship agreement", "Photo"],
      "PhD": ["ID document", "Bank details (RIB)", "Photo", "PhD enrollment certificate"],
      "_default": ["ID document", "Bank details (RIB)", "Photo"],
  }
  ```
- **Returning person:** Text fields pre-fill from existing user record. File fields are always empty (re-upload required regardless of retention state — new legal context, documents may be outdated).

### Step 3 — Admin Validation (`key: admin_validation`)

- **Type:** `approval`
- **Assignee:** `assignee_permission: access_personal_data` (administration team)
- **Actions:**
  - `approve` — advances to next step
  - `request_corrections` — sends workflow back to step 2 (`newcomer_info`) with a comment. Newcomer gets an email to fix/re-upload.
  - `reject` — terminates the workflow (fundamental problem)
- **File access:** Only this step's assignees can view/download encrypted files via `ui.download()` callback with `access_personal_data` check.

### Step 4 — IT Account Creation (`key: it_account_creation`)

- **Type:** `form` (not approval — no reject action)
- **Assignee:** `assignee_permission: manage_users` (IT team)
- **Actions:** `complete` only
- **Fields:** `notes` (textarea, optional) — for account name, comments. Future AD integration point.
- **On completion:**
  - Workflow status → `completed`
  - `mark_for_retention()` called on all encrypted files with legally required retention period

### Extensibility — Inserting Validation Steps

Additional approval steps (e.g. FSD validation) can be inserted anywhere in the step list by adding a config entry. Design ensures this works cleanly:
- "Request corrections" targets `newcomer_info` by step key, not by position
- Notifications are tied to step keys, not indices
- Each approval step follows the same pattern: assignee + approve/corrections/reject actions

## Engine Changes

### 1. "Request corrections" action

`compute_next_step` gains a `corrections` action type that returns a configured target step key instead of advancing forward. Configured per-step:
```python
class WorkflowStepConfig(BaseModel):
    # ... existing fields ...
    corrections_target: str | None = None  # step key to return to
```

### 2. Verification code support

Not in the engine itself. New fields on `WorkflowRequest`:
- `verification_code_hash: str | None`
- `code_expires_at: datetime | None`
- `code_attempts: int = 0`

Service functions:
- `generate_verification_code(request_id) → code` — generates 6-digit code, hashes and stores it, sends email, returns plaintext for the email only
- `verify_code(request_id, code) → bool` — checks hash, expiry, attempt count

### 3. `encrypted: bool` on FieldConfig

Existing change, described above.

### 4. Remove `/workflow/file/{file_id}` endpoint

Replace with NiceGUI `ui.download()` callbacks everywhere.

## Notifications

| Transition | Recipients | Content |
|---|---|---|
| Step 1 → 2 | newcomer (contact_email) | Token link + what to provide |
| Step 2 → 3 | users with `access_personal_data` | "New onboarding submission ready for review" |
| Step 3 approve → 4 | users with `manage_users` + initiator | "Approved, IT processing" |
| Step 3 request_corrections → 2 | newcomer (contact_email) | "Corrections needed" + admin comment |
| Step 3 reject | initiator | "Onboarding rejected" + comment |
| Step 4 complete | initiator + newcomer | "Welcome, account ready" |
| Verification code (step 2) | newcomer (contact_email) | 6-digit code, 15-min expiry |

## Retention Policy

- On workflow completion (step 4 `complete`), all encrypted files get `retained_until` set to `now + N days` (N configurable, driven by legal requirements)
- `delete_expired()` removes expired blobs and DB rows
- Triggered by admin action or periodic task (no background scheduler in the app currently — can be a CLI command or admin UI button)
- Returning persons always re-upload — old files' retention is independent

## Migration

Alembic migration (0004 or next):
- `encrypted_file` table
- `workflow_file.encrypted_file_id` column (nullable FK)
- `workflow_request.verification_code_hash`, `code_expires_at`, `code_attempts` columns
