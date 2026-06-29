# Versioned workflow uploads with carry-over

**Date:** 2026-06-29
**Status:** Approved (design)

## Problem

Workflow file uploads create a new `WorkflowFile` row per upload, tagged
`(request_id, step_key, field_name)`. Two consequences hurt the onboarding flow:

1. **Forced re-upload on corrections.** When `request_corrections` loops a
   request back to `newcomer_info`, the token form re-renders with an empty
   in-session `files` dict. Already-uploaded documents are not shown as present,
   so the newcomer must re-upload everything — and the required-file check
   (added 2026-06-29) now hard-blocks submit until they do.
2. **Admin view mixes versions.** `_render_files_section` lists every
   `WorkflowFile` row flat, so multiple upload rounds interleave (e.g. an
   `ID Document` from round 1 and another from round 2 appear as two unlabelled
   sibling rows). The reviewer cannot tell which file is current or when each was
   uploaded.

Both stem from the absence of a "current file per field" concept.

## Approach

Treat the newest `WorkflowFile` per `(request_id, step_key, field_name)` (by
`uploaded_at`) as the **current** file; older rows are history. Supersede on
re-upload, keep history, delete nothing.

**No schema change.** `WorkflowFile.uploaded_at` (server_default `now()`) already
exists and is sufficient to order versions.

## Components

### 1. `backend/workflow_files.py` (new, small)

Pure grouping plus one thin loader. No mutation of the model.

```python
@dataclass(frozen=True)
class FieldFileGroup:
    step_key: str
    field_name: str
    current: WorkflowFile
    previous: list[WorkflowFile]   # newest-first, excludes current

def group_files_by_field(files: list[WorkflowFile]) -> list[FieldFileGroup]:
    """Group rows by (step_key, field_name); within a group sort by
    uploaded_at desc → current = newest, previous = the rest."""

def current_files_by_name(files: list[WorkflowFile]) -> dict[str, WorkflowFile]:
    """field_name -> current WorkflowFile, for a single step's files."""
```

`group_files_by_field` is pure (input list → output list); deterministic order
within a group is by `uploaded_at` desc, ties broken by `id` for stability.

### 2. Corrections form carries files over

**`workflow_token.py`** — before rendering the step form, load existing
`WorkflowFile` rows for `(request.id, step.key)` and seed
`uploaded_files = {name: current.filename}` via `current_files_by_name`. This
dict is the same one `handle_file_upload` mutates, so a fresh upload still
overrides the seeded entry for that field in-session.

**`workflow_step.py`** (`_render_field`, `type == "file"`) — when a field shows
as already-uploaded, render `✓ <label>: <filename>` plus a small **Replace**
button. Clicking Replace swaps the field's container to the `ui.upload` widget so
the newcomer can pick a new file. Fields left untouched carry over (their rows
already exist); a replacement just creates a newer superseding row.

Effect on validation: the required-file check (`_field_is_filled`) sees the
seeded entry and passes, so corrections no longer force re-upload.

### 3. Admin card grouped per field

**`workflow_detail.py:_render_files_section`** — replace the flat loop with
`group_files_by_field`. Render one entry per field, ordered by the workflow's
declared field order (iterate `wf.steps` resolved fields, file-type only):

- **Current:** `<label>: 📎 <filename>` + `uploaded_at` timestamp (download link).
- **History:** a collapsible `N previous version(s)` listing older files with
  their timestamps and download links.
- **Orphans:** files whose `(step_key, field_name)` no longer maps to a current
  config field are shown in a trailing **"Other"** group so nothing is hidden.

Download behaviour is unchanged — each version remains its own `WorkflowFile`
with its own (encrypted or plain) blob.

## Data flow

```
request_corrections → token form loads current files for the step
  → file fields show "✓ already uploaded" + Replace
  → newcomer optionally replaces some (new rows supersede; old kept as history)
  → submit (required check passes from seeded current files)
  → admin detail page groups rows → current per field + collapsed history
```

## Testing

- `group_files_by_field`: current = newest by `uploaded_at`; previous newest-first;
  multiple fields grouped independently; tie-break stable.
- Corrections form: a request with an existing upload re-rendered via the token
  page shows the file as present and submits successfully with **no** re-upload.
- Replace: uploading again for a field creates a second row; the newer one is
  current, the older becomes history.
- Admin card: renders one current entry per field in field order, with a
  collapsible previous-versions section; orphaned files land in "Other".

## Out of scope

- No deletion or retention changes — superseded blobs follow existing retention.
- No cross-step file sharing or de-duplication of identical re-uploads.
- No change to the encrypted-storage or download/permission paths.
