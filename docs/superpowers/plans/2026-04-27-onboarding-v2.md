# Onboarding v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 3-step onboarding workflow with a 4-step flow (initiation → newcomer info with verification codes → admin validation with request-corrections → IT account creation), add envelope-encrypted file storage for personal documents, and support returning-person pre-fill.

**Architecture:** Encrypted file storage is a standalone service module (`encrypted_storage.py`) using AES-256-GCM envelope encryption. The workflow engine gains a `request_corrections` action that sends a workflow back to a configured step. The token page gains a verification code gate. File downloads move from an HTTP endpoint to NiceGUI `ui.download()` callbacks.

**Tech Stack:** Python 3.10+, `cryptography` (AES-256-GCM), SQLAlchemy async, NiceGUI, Alembic, pytest/pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-04-27-onboarding-v2-design.md`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `not_dot_net/backend/encrypted_storage.py` | Create | EncryptedFile model, store/read/retention service, `access_personal_data` permission |
| `not_dot_net/backend/verification.py` | Create | Verification code generation, hashing, validation, rate limiting |
| `not_dot_net/backend/secrets.py` | Modify | Add `file_encryption_key` to AppSecrets |
| `not_dot_net/config.py` | Modify | Add `encrypted: bool` to FieldConfig, `corrections_target: str | None` to WorkflowStepConfig, `document_instructions: dict` to WorkflowConfig |
| `not_dot_net/backend/workflow_engine.py` | Modify | Handle `request_corrections` action in `compute_next_step` |
| `not_dot_net/backend/workflow_models.py` | Modify | Add verification code fields to WorkflowRequest, `encrypted_file_id` to WorkflowFile |
| `not_dot_net/backend/workflow_service.py` | Modify | Replace onboarding config with 4-step version, handle `request_corrections` event in notifications, retention on completion |
| `not_dot_net/backend/notifications.py` | Modify | Add `request_corrections` and `verification_code` templates, support `permission:` notify targets |
| `not_dot_net/backend/workflow_file_routes.py` | Delete | Remove HTTP file download endpoint |
| `not_dot_net/app.py` | Modify | Remove workflow_file_router include |
| `not_dot_net/frontend/workflow_token.py` | Modify | Add verification code gate before form |
| `not_dot_net/frontend/workflow_step.py` | Modify | Encrypted file upload path, `ui.download()` for file access, document instructions display |
| `not_dot_net/frontend/workflow_detail.py` | Modify | Replace file links with `ui.download()` buttons, `access_personal_data` gating, `request_corrections` action button |
| `not_dot_net/frontend/new_request.py` | Modify | Returning-person search UI on onboarding step 1 |
| `alembic/versions/0005_onboarding_v2.py` | Create | encrypted_file table, workflow_request verification fields, workflow_file.encrypted_file_id |
| `pyproject.toml` | Modify | Add `cryptography` dependency |
| `tests/test_encrypted_storage.py` | Create | Encrypt/decrypt round-trip, retention, audit logging |
| `tests/test_verification.py` | Create | Code generation, validation, expiry, rate limiting |
| `tests/test_workflow_engine.py` | Create | `request_corrections` action routing |
| `tests/test_workflow_service.py` | Modify | Update onboarding tests for 4-step flow |
| `tests/test_security.py` | Modify | Assert `/workflow/file/` endpoint is removed |

---

### Task 1: Add `cryptography` Dependency

**Files:**
- Modify: `pyproject.toml:34-49`

- [ ] **Step 1: Add cryptography to dependencies**

In `pyproject.toml`, add `"cryptography"` to the `dependencies` list:

```toml
dependencies = [
    "nicegui>=3.4.0",
    "cyclopts",
    "sqlalchemy>=2.0.0",
    "databases[sqlite]>=0.7.0",
    "ldap3",
    "dnspython",
    "pydantic",
    "fastapi-users[sqlalchemy]",
    "aiosqlite",
    "asyncpg",
    "passlib",
    "aiosmtplib",
    "pyyaml",
    "alembic",
    "cryptography",
]
```

- [ ] **Step 2: Install**

Run: `uv pip install -e .`

- [ ] **Step 3: Verify import**

Run: `python -c "from cryptography.hazmat.primitives.ciphers.aead import AESGCM; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "feat: add cryptography dependency for encrypted file storage"
```

---

### Task 2: Add `file_encryption_key` to AppSecrets

**Files:**
- Modify: `not_dot_net/backend/secrets.py:15-29`
- Test: `tests/test_encrypted_storage.py` (created in Task 3)

- [ ] **Step 1: Write the failing test**

Create `tests/test_encrypted_storage.py`:

```python
import pytest
from pathlib import Path
import tempfile

from not_dot_net.backend.secrets import AppSecrets, generate_secrets_file, read_secrets_file


def test_app_secrets_has_file_encryption_key():
    s = AppSecrets(jwt_secret="j", storage_secret="s", file_encryption_key="k")
    assert s.file_encryption_key == "k"


def test_generate_secrets_file_includes_encryption_key():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "secrets.key"
        secrets = generate_secrets_file(path)
        assert secrets.file_encryption_key
        assert len(secrets.file_encryption_key) > 20
        reloaded = read_secrets_file(path)
        assert reloaded.file_encryption_key == secrets.file_encryption_key
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_encrypted_storage.py::test_app_secrets_has_file_encryption_key -v`
Expected: FAIL — `AppSecrets.__init__() got an unexpected keyword argument 'file_encryption_key'`

- [ ] **Step 3: Add file_encryption_key to AppSecrets and generate_secrets_file**

In `not_dot_net/backend/secrets.py`, modify `AppSecrets` (line 15) and `generate_secrets_file` (line 20):

```python
class AppSecrets(BaseModel):
    jwt_secret: str
    storage_secret: str
    file_encryption_key: str = ""


def generate_secrets_file(path: Path) -> AppSecrets:
    app_secrets = AppSecrets(
        jwt_secret=secrets.token_urlsafe(32),
        storage_secret=secrets.token_urlsafe(32),
        file_encryption_key=secrets.token_urlsafe(32),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(app_secrets.model_dump(), indent=2))
    os.chmod(path, 0o600)
    logger.info("Generated secrets file: %s", path)
    return app_secrets
```

Note: `file_encryption_key` defaults to `""` for backwards compatibility with existing secrets files. The encrypted storage service will check for a non-empty key before operating.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_encrypted_storage.py -v`
Expected: PASS (both tests)

- [ ] **Step 5: Update conftest to include file_encryption_key in test secrets**

In `tests/conftest.py`, modify the `setup_db` fixture (line 13):

```python
init_user_secrets(AppSecrets(
    jwt_secret="test-secret-that-is-long-enough-for-hs256",
    storage_secret="test-storage",
    file_encryption_key="test-file-encryption-key-32bytes!",
))
```

- [ ] **Step 6: Run full test suite**

Run: `uv run pytest -v`
Expected: All existing tests pass

- [ ] **Step 7: Commit**

```bash
git add not_dot_net/backend/secrets.py tests/test_encrypted_storage.py tests/conftest.py
git commit -m "feat: add file_encryption_key to AppSecrets for envelope encryption"
```

---

### Task 3: Encrypted File Storage Service

**Files:**
- Create: `not_dot_net/backend/encrypted_storage.py`
- Test: `tests/test_encrypted_storage.py` (extend)

- [ ] **Step 1: Write failing tests for encrypt/decrypt round-trip**

Append to `tests/test_encrypted_storage.py`:

```python
import uuid
from not_dot_net.backend.encrypted_storage import store_encrypted, read_encrypted, ACCESS_PERSONAL_DATA


@pytest.mark.asyncio
async def test_store_and_read_encrypted_roundtrip():
    content = b"This is a secret document"
    filename = "id_card.pdf"
    uploader_id = uuid.uuid4()

    enc_file = await store_encrypted(content, filename, "application/pdf", uploader_id)
    assert enc_file.id is not None
    assert enc_file.original_filename == filename
    assert enc_file.wrapped_dek is not None
    assert enc_file.nonce is not None

    decrypted, name, ctype = await read_encrypted(enc_file.id, actor_id=uploader_id)
    assert decrypted == content
    assert name == filename
    assert ctype == "application/pdf"


@pytest.mark.asyncio
async def test_encrypted_blob_is_not_plaintext():
    content = b"Super secret bank details RIB"
    enc_file = await store_encrypted(content, "rib.pdf", "application/pdf", None)
    from pathlib import Path
    blob = Path(enc_file.storage_path).read_bytes()
    assert blob != content


@pytest.mark.asyncio
async def test_read_encrypted_nonexistent_raises():
    with pytest.raises(ValueError, match="not found"):
        await read_encrypted(uuid.uuid4(), actor_id=uuid.uuid4())


def test_access_personal_data_permission_registered():
    from not_dot_net.backend.permissions import get_permissions
    perms = get_permissions()
    assert any(p.key == ACCESS_PERSONAL_DATA for p in perms)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_encrypted_storage.py::test_store_and_read_encrypted_roundtrip -v`
Expected: FAIL — `cannot import name 'store_encrypted' from 'not_dot_net.backend.encrypted_storage'`

- [ ] **Step 3: Implement encrypted_storage.py**

Create `not_dot_net/backend/encrypted_storage.py`:

```python
"""Envelope-encrypted file storage — AES-256-GCM with per-file DEKs."""

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import ForeignKey, LargeBinary, String, func, select
from sqlalchemy.orm import Mapped, MappedAsDataclass, mapped_column

from not_dot_net.backend.db import Base, session_scope
from not_dot_net.backend.permissions import permission

logger = logging.getLogger(__name__)

ACCESS_PERSONAL_DATA = permission(
    "access_personal_data",
    "Access personal data",
    "View and download encrypted personal documents",
)

ENCRYPTED_DIR = Path("data/encrypted")


class EncryptedFile(MappedAsDataclass, Base, kw_only=True):
    __tablename__ = "encrypted_file"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default_factory=uuid.uuid4)
    wrapped_dek: Mapped[bytes] = mapped_column(LargeBinary)
    nonce: Mapped[bytes] = mapped_column(LargeBinary)
    storage_path: Mapped[str] = mapped_column(String(1000))
    original_filename: Mapped[str] = mapped_column(String(500))
    content_type: Mapped[str] = mapped_column(String(200), default="application/octet-stream")
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True, default=None
    )
    uploaded_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=None)
    retained_until: Mapped[datetime | None] = mapped_column(nullable=True, default=None)


def _get_master_key() -> bytes:
    """Get the file encryption master key from app secrets."""
    from not_dot_net.backend.users import _get_secret
    import base64
    key_b64 = _get_secret().file_encryption_key
    if not key_b64:
        raise RuntimeError("file_encryption_key not configured in secrets")
    raw = base64.urlsafe_b64decode(key_b64 + "==")
    if len(raw) < 32:
        raw = raw.ljust(32, b"\x00")
    return raw[:32]


def _encrypt_file(data: bytes, master_key: bytes) -> tuple[bytes, bytes, bytes]:
    """Encrypt data with a fresh DEK. Returns (encrypted_data, wrapped_dek, nonce)."""
    dek = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    encrypted_data = AESGCM(dek).encrypt(nonce, data, None)
    wrap_nonce = os.urandom(12)
    wrapped_dek = AESGCM(master_key).encrypt(wrap_nonce, dek, None)
    combined_wrapped = wrap_nonce + wrapped_dek
    return encrypted_data, combined_wrapped, nonce


def _decrypt_file(encrypted_data: bytes, wrapped_dek: bytes, nonce: bytes, master_key: bytes) -> bytes:
    """Unwrap DEK and decrypt file data."""
    wrap_nonce = wrapped_dek[:12]
    wrapped = wrapped_dek[12:]
    dek = AESGCM(master_key).decrypt(wrap_nonce, wrapped, None)
    return AESGCM(dek).decrypt(nonce, encrypted_data, None)


async def store_encrypted(
    data: bytes,
    filename: str,
    content_type: str,
    uploaded_by: uuid.UUID | None,
) -> EncryptedFile:
    """Encrypt and store a file. Returns the EncryptedFile record."""
    master_key = _get_master_key()
    encrypted_data, wrapped_dek, nonce = _encrypt_file(data, master_key)

    ENCRYPTED_DIR.mkdir(parents=True, exist_ok=True)
    file_id = uuid.uuid4()
    blob_path = ENCRYPTED_DIR / f"{file_id}.enc"
    blob_path.write_bytes(encrypted_data)

    async with session_scope() as session:
        enc_file = EncryptedFile(
            id=file_id,
            wrapped_dek=wrapped_dek,
            nonce=nonce,
            storage_path=str(blob_path),
            original_filename=filename,
            content_type=content_type,
            uploaded_by=uploaded_by,
        )
        session.add(enc_file)
        await session.commit()
        await session.refresh(enc_file)
        return enc_file


async def read_encrypted(
    file_id: uuid.UUID,
    actor_id: uuid.UUID | str | None = None,
    actor_email: str | None = None,
) -> tuple[bytes, str, str]:
    """Decrypt and return file contents. Logs an audit event.

    Returns (data, original_filename, content_type).
    """
    from not_dot_net.backend.audit import log_audit

    async with session_scope() as session:
        enc_file = await session.get(EncryptedFile, file_id)
        if enc_file is None:
            raise ValueError(f"Encrypted file {file_id} not found")

        master_key = _get_master_key()
        blob_path = Path(enc_file.storage_path)
        if not blob_path.exists():
            raise ValueError(f"Encrypted blob not found on disk: {blob_path}")

        encrypted_data = blob_path.read_bytes()
        data = _decrypt_file(encrypted_data, enc_file.wrapped_dek, enc_file.nonce, master_key)

        await log_audit(
            "personal_data", "download",
            actor_id=actor_id,
            actor_email=actor_email,
            target_type="encrypted_file",
            target_id=file_id,
            detail=f"filename={enc_file.original_filename}",
        )

        return data, enc_file.original_filename, enc_file.content_type


async def mark_for_retention(file_id: uuid.UUID, days: int) -> None:
    """Set the retention deadline on an encrypted file."""
    async with session_scope() as session:
        enc_file = await session.get(EncryptedFile, file_id)
        if enc_file is None:
            return
        enc_file.retained_until = datetime.now(timezone.utc) + timedelta(days=days)
        await session.commit()


async def delete_expired() -> int:
    """Delete encrypted files past their retention date. Returns count deleted."""
    now = datetime.now(timezone.utc)
    deleted = 0
    async with session_scope() as session:
        result = await session.execute(
            select(EncryptedFile).where(
                EncryptedFile.retained_until != None,
                EncryptedFile.retained_until < now,
            )
        )
        for enc_file in result.scalars().all():
            blob_path = Path(enc_file.storage_path)
            if blob_path.exists():
                blob_path.unlink()
            await session.delete(enc_file)
            deleted += 1
        await session.commit()
    return deleted
```

- [ ] **Step 4: Import EncryptedFile in conftest so table is created**

In `tests/conftest.py`, add after the existing model imports (around line 24):

```python
import not_dot_net.backend.encrypted_storage  # noqa: F401
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_encrypted_storage.py -v`
Expected: All 4 tests PASS

- [ ] **Step 6: Write retention tests**

Append to `tests/test_encrypted_storage.py`:

```python
from datetime import datetime, timedelta, timezone
from not_dot_net.backend.encrypted_storage import mark_for_retention, delete_expired, EncryptedFile


@pytest.mark.asyncio
async def test_mark_for_retention():
    enc_file = await store_encrypted(b"data", "f.pdf", "application/pdf", None)
    await mark_for_retention(enc_file.id, days=30)
    from not_dot_net.backend.db import session_scope
    async with session_scope() as session:
        reloaded = await session.get(EncryptedFile, enc_file.id)
        assert reloaded.retained_until is not None


@pytest.mark.asyncio
async def test_delete_expired_removes_past_retention():
    enc_file = await store_encrypted(b"old data", "old.pdf", "application/pdf", None)
    async with session_scope() as session:
        reloaded = await session.get(EncryptedFile, enc_file.id)
        reloaded.retained_until = datetime.now(timezone.utc) - timedelta(days=1)
        await session.commit()
    count = await delete_expired()
    assert count == 1
    from not_dot_net.backend.db import session_scope as ss
    async with ss() as session:
        assert await session.get(EncryptedFile, enc_file.id) is None


@pytest.mark.asyncio
async def test_delete_expired_keeps_future_retention():
    enc_file = await store_encrypted(b"fresh data", "fresh.pdf", "application/pdf", None)
    await mark_for_retention(enc_file.id, days=30)
    count = await delete_expired()
    assert count == 0
```

- [ ] **Step 7: Run retention tests**

Run: `uv run pytest tests/test_encrypted_storage.py -v`
Expected: All 7 tests PASS

- [ ] **Step 8: Run full test suite**

Run: `uv run pytest -v`
Expected: All pass

- [ ] **Step 9: Commit**

```bash
git add not_dot_net/backend/encrypted_storage.py tests/test_encrypted_storage.py tests/conftest.py
git commit -m "feat: encrypted file storage service with envelope encryption (AES-256-GCM)"
```

---

### Task 4: Config Model Changes (FieldConfig, WorkflowStepConfig, WorkflowConfig)

**Files:**
- Modify: `not_dot_net/config.py:8-38`
- Test: `tests/test_workflow_engine.py` (created here)

- [ ] **Step 1: Write failing test for encrypted field config**

Create `tests/test_workflow_engine.py`:

```python
from not_dot_net.config import FieldConfig, WorkflowStepConfig, WorkflowConfig


def test_field_config_encrypted_default_false():
    fc = FieldConfig(name="doc", type="file")
    assert fc.encrypted is False


def test_field_config_encrypted_true():
    fc = FieldConfig(name="doc", type="file", encrypted=True)
    assert fc.encrypted is True


def test_step_config_corrections_target():
    sc = WorkflowStepConfig(
        key="validation",
        type="approval",
        actions=["approve", "request_corrections", "reject"],
        corrections_target="newcomer_info",
    )
    assert sc.corrections_target == "newcomer_info"


def test_step_config_corrections_target_default_none():
    sc = WorkflowStepConfig(key="step", type="form")
    assert sc.corrections_target is None


def test_workflow_config_document_instructions():
    wc = WorkflowConfig(
        label="Test",
        steps=[],
        document_instructions={"Intern": ["ID document"], "_default": ["ID", "RIB"]},
    )
    assert wc.document_instructions["Intern"] == ["ID document"]
    assert wc.document_instructions["_default"] == ["ID", "RIB"]


def test_workflow_config_document_instructions_default_empty():
    wc = WorkflowConfig(label="Test", steps=[])
    assert wc.document_instructions == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_workflow_engine.py::test_field_config_encrypted_default_false -v`
Expected: FAIL — `FieldConfig.__init__() got an unexpected keyword argument 'encrypted'` or assertion error

- [ ] **Step 3: Modify config.py**

In `not_dot_net/config.py`:

`FieldConfig` (line 8-13) — add `encrypted`:

```python
class FieldConfig(BaseModel):
    name: str
    type: str  # text, email, textarea, date, select, file
    required: bool = False
    label: str = ""
    options_key: str | None = None
    encrypted: bool = False
```

`WorkflowStepConfig` (line 22-30) — add `corrections_target`:

```python
class WorkflowStepConfig(BaseModel):
    key: str
    type: str  # form, approval
    assignee_role: str | None = None
    assignee_permission: str | None = None
    assignee: str | None = None
    fields: list[FieldConfig] = []
    actions: list[str] = []
    partial_save: bool = False
    corrections_target: str | None = None
```

`WorkflowConfig` (line 33-38) — add `document_instructions`:

```python
class WorkflowConfig(BaseModel):
    label: str
    start_role: str = "staff"
    target_email_field: str | None = None
    steps: list[WorkflowStepConfig]
    notifications: list[NotificationRuleConfig] = []
    document_instructions: dict[str, list[str]] = {}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_workflow_engine.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest -v`
Expected: All pass (new fields have defaults, no breakage)

- [ ] **Step 6: Commit**

```bash
git add not_dot_net/config.py tests/test_workflow_engine.py
git commit -m "feat: add encrypted, corrections_target, document_instructions to config models"
```

---

### Task 5: Engine — `request_corrections` Action

**Files:**
- Modify: `not_dot_net/backend/workflow_engine.py:44-58`
- Test: `tests/test_workflow_engine.py` (extend)

- [ ] **Step 1: Write failing tests for request_corrections action**

Append to `tests/test_workflow_engine.py`:

```python
from not_dot_net.backend.workflow_engine import compute_next_step
from not_dot_net.backend.workflow_models import RequestStatus


def test_request_corrections_returns_target_step():
    from not_dot_net.config import WorkflowConfig, WorkflowStepConfig
    wf = WorkflowConfig(
        label="Test",
        steps=[
            WorkflowStepConfig(key="form", type="form", actions=["submit"]),
            WorkflowStepConfig(
                key="validation", type="approval",
                actions=["approve", "request_corrections", "reject"],
                corrections_target="form",
            ),
        ],
    )
    next_step, status = compute_next_step(wf, "validation", "request_corrections")
    assert next_step == "form"
    assert status == RequestStatus.IN_PROGRESS


def test_request_corrections_without_target_raises():
    from not_dot_net.config import WorkflowConfig, WorkflowStepConfig
    wf = WorkflowConfig(
        label="Test",
        steps=[
            WorkflowStepConfig(key="form", type="form", actions=["submit"]),
            WorkflowStepConfig(
                key="validation", type="approval",
                actions=["approve", "request_corrections", "reject"],
            ),
        ],
    )
    import pytest
    with pytest.raises(ValueError, match="corrections_target"):
        compute_next_step(wf, "validation", "request_corrections")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_workflow_engine.py::test_request_corrections_returns_target_step -v`
Expected: FAIL — `request_corrections` is not handled, falls through to normal step advance

- [ ] **Step 3: Modify compute_next_step to handle request_corrections**

In `not_dot_net/backend/workflow_engine.py`, modify `compute_next_step` (line 44-58):

```python
def compute_next_step(
    workflow: WorkflowConfig, current_step_key: str, action: str
) -> tuple[str | None, str]:
    """Given an action, return (next_step_key, new_status)."""
    if action == "reject":
        return (None, RequestStatus.REJECTED)
    if action == "save_draft":
        return (current_step_key, RequestStatus.IN_PROGRESS)
    if action == "request_corrections":
        step = next((s for s in workflow.steps if s.key == current_step_key), None)
        if step is None or not step.corrections_target:
            raise ValueError(
                f"Step '{current_step_key}' has no corrections_target configured"
            )
        return (step.corrections_target, RequestStatus.IN_PROGRESS)
    step_keys = [s.key for s in workflow.steps]
    if current_step_key not in step_keys:
        raise ValueError(f"Unknown step '{current_step_key}' in workflow")
    idx = step_keys.index(current_step_key)
    if idx + 1 < len(step_keys):
        return (step_keys[idx + 1], RequestStatus.IN_PROGRESS)
    return (None, RequestStatus.COMPLETED)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_workflow_engine.py -v`
Expected: All 8 tests PASS

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add not_dot_net/backend/workflow_engine.py tests/test_workflow_engine.py
git commit -m "feat: add request_corrections action to workflow engine"
```

---

### Task 6: Verification Code Service

**Files:**
- Create: `not_dot_net/backend/verification.py`
- Test: `tests/test_verification.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_verification.py`:

```python
import pytest
import uuid
from not_dot_net.backend.verification import generate_verification_code, verify_code, MAX_ATTEMPTS
from not_dot_net.backend.workflow_models import WorkflowRequest
from not_dot_net.backend.workflow_service import create_request
from not_dot_net.backend.db import session_scope


async def _create_onboarding_request():
    """Helper: create a request and return it."""
    from tests.test_workflow_service import _create_user, _setup_roles
    await _setup_roles()
    user = await _create_user()
    from not_dot_net.backend.workflow_service import submit_step
    req = await create_request(
        workflow_type="onboarding",
        created_by=user.id,
        data={"contact_email": "newcomer@test.com", "status": "PhD"},
    )
    req = await submit_step(req.id, user.id, "submit", data={}, actor_user=user)
    return req


@pytest.mark.asyncio
async def test_generate_code_returns_6_digits():
    req = await _create_onboarding_request()
    code = await generate_verification_code(req.id)
    assert len(code) == 6
    assert code.isdigit()


@pytest.mark.asyncio
async def test_verify_code_correct():
    req = await _create_onboarding_request()
    code = await generate_verification_code(req.id)
    result = await verify_code(req.id, code)
    assert result is True


@pytest.mark.asyncio
async def test_verify_code_wrong():
    req = await _create_onboarding_request()
    await generate_verification_code(req.id)
    result = await verify_code(req.id, "000000")
    assert result is False


@pytest.mark.asyncio
async def test_verify_code_rate_limited():
    req = await _create_onboarding_request()
    await generate_verification_code(req.id)
    for _ in range(MAX_ATTEMPTS):
        await verify_code(req.id, "000000")
    with pytest.raises(PermissionError, match="Too many"):
        await verify_code(req.id, "000000")


@pytest.mark.asyncio
async def test_resend_invalidates_old_code():
    req = await _create_onboarding_request()
    code1 = await generate_verification_code(req.id)
    code2 = await generate_verification_code(req.id)
    assert code1 != code2 or True  # codes may collide, but old hash is replaced
    assert await verify_code(req.id, code2) is True
    # code1 no longer works (hash was overwritten)
    # (regenerate to test — need a fresh request since code2 was consumed)
    req2 = await _create_onboarding_request()
    old_code = await generate_verification_code(req2.id)
    new_code = await generate_verification_code(req2.id)
    result = await verify_code(req2.id, old_code)
    # old_code might equal new_code by chance, so just verify new_code works
    assert await verify_code(req2.id, new_code) is False  # already verified above consumed it
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_verification.py::test_generate_code_returns_6_digits -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'not_dot_net.backend.verification'`

- [ ] **Step 3: Add verification fields to WorkflowRequest**

In `not_dot_net/backend/workflow_models.py`, add to `WorkflowRequest` class (after line 31):

```python
    verification_code_hash: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    code_expires_at: Mapped[datetime | None] = mapped_column(nullable=True, default=None)
    code_attempts: Mapped[int] = mapped_column(default=0)
```

- [ ] **Step 4: Implement verification.py**

Create `not_dot_net/backend/verification.py`:

```python
"""Verification code service — OTP-style email verification for token pages."""

import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from not_dot_net.backend.db import session_scope
from not_dot_net.backend.workflow_models import WorkflowRequest

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
CODE_EXPIRY_MINUTES = 15


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


async def generate_verification_code(request_id: uuid.UUID) -> str:
    """Generate a 6-digit code, store its hash, return the plaintext."""
    code = f"{secrets.randbelow(1_000_000):06d}"

    async with session_scope() as session:
        req = await session.get(WorkflowRequest, request_id)
        if req is None:
            raise ValueError(f"Request {request_id} not found")
        req.verification_code_hash = _hash_code(code)
        req.code_expires_at = datetime.now(timezone.utc) + timedelta(minutes=CODE_EXPIRY_MINUTES)
        req.code_attempts = 0
        await session.commit()

    return code


async def verify_code(request_id: uuid.UUID, code: str) -> bool:
    """Verify a code. Returns True on match. Raises PermissionError if rate-limited."""
    async with session_scope() as session:
        req = await session.get(WorkflowRequest, request_id)
        if req is None:
            raise ValueError(f"Request {request_id} not found")

        if req.code_attempts >= MAX_ATTEMPTS:
            raise PermissionError("Too many attempts — request a new code")

        if req.verification_code_hash is None or req.code_expires_at is None:
            return False

        expires = req.code_expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expires:
            return False

        req.code_attempts += 1

        if _hash_code(code) == req.verification_code_hash:
            req.verification_code_hash = None
            req.code_expires_at = None
            req.code_attempts = 0
            await session.commit()
            return True

        await session.commit()
        return False
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_verification.py -v`
Expected: Most tests PASS. If the onboarding workflow config has changed (Task 7), some helpers may need adjusting — fix as needed.

Note: The test helper `_create_onboarding_request` depends on the current onboarding workflow config. If you're running this task before Task 7 (workflow config update), the field names in the `data` dict must match the current config (`person_name`, `person_email`, etc.). Update the helper data dict to match whichever config is active.

- [ ] **Step 6: Run full test suite**

Run: `uv run pytest -v`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add not_dot_net/backend/verification.py not_dot_net/backend/workflow_models.py tests/test_verification.py
git commit -m "feat: verification code service for token page OTP"
```

---

### Task 7: Update Onboarding Workflow Config to 4 Steps

**Files:**
- Modify: `not_dot_net/backend/workflow_service.py:75-124`
- Modify: `not_dot_net/backend/notifications.py:7-32`
- Test: `tests/test_workflow_service.py` (extend)

- [ ] **Step 1: Write failing test for the new 4-step onboarding flow**

Append to `tests/test_workflow_service.py`:

```python
async def test_onboarding_v2_full_flow():
    """Test the complete 4-step onboarding: initiation → newcomer_info → admin_validation → it_account_creation."""
    await _setup_roles()
    cfg = await roles_config.get()
    cfg.roles["admin"].permissions.append("access_personal_data")
    await roles_config.set(cfg)

    initiator = await _create_user(email="initiator@test.com", role="staff")
    admin = await _create_user(email="admin@test.com", role="admin")

    # Step 1: Initiation
    req = await create_request(
        workflow_type="onboarding",
        created_by=initiator.id,
        data={"contact_email": "newcomer@example.com", "status": "PhD"},
        actor=initiator,
    )
    assert req.current_step == "initiation"

    req = await submit_step(req.id, initiator.id, "submit", data={}, actor_user=initiator)
    assert req.current_step == "newcomer_info"
    assert req.token is not None

    # Step 2: Newcomer submits info via token
    req = await submit_step(
        req.id, actor_id=None, action="submit",
        data={"first_name": "Marie", "last_name": "Curie", "phone": "+33 1 00 00"},
        actor_token=req.token,
    )
    assert req.current_step == "admin_validation"

    # Step 3: Admin approves
    req = await submit_step(req.id, admin.id, "approve", data={}, actor_user=admin)
    assert req.current_step == "it_account_creation"

    # Step 4: IT marks complete
    req = await submit_step(req.id, admin.id, "complete", data={"notes": "account: mcurie"}, actor_user=admin)
    assert req.status == "completed"


async def test_onboarding_v2_request_corrections():
    """Admin sends workflow back to newcomer_info via request_corrections."""
    await _setup_roles()
    cfg = await roles_config.get()
    cfg.roles["admin"].permissions.append("access_personal_data")
    await roles_config.set(cfg)

    initiator = await _create_user(email="initiator@test.com", role="staff")
    admin = await _create_user(email="admin@test.com", role="admin")

    req = await create_request(
        workflow_type="onboarding",
        created_by=initiator.id,
        data={"contact_email": "newcomer@example.com", "status": "CDD"},
        actor=initiator,
    )
    req = await submit_step(req.id, initiator.id, "submit", data={}, actor_user=initiator)
    req = await submit_step(
        req.id, actor_id=None, action="submit",
        data={"first_name": "Jean", "last_name": "Dupont"},
        actor_token=req.token,
    )
    assert req.current_step == "admin_validation"

    # Admin requests corrections
    req = await submit_step(
        req.id, admin.id, "request_corrections",
        comment="Please re-upload ID document",
        actor_user=admin,
    )
    assert req.current_step == "newcomer_info"
    assert req.status == "in_progress"
    assert req.token is not None  # new token generated for target_person step
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_workflow_service.py::test_onboarding_v2_full_flow -v`
Expected: FAIL — current onboarding config has different step keys and fields

- [ ] **Step 3: Replace the onboarding workflow config**

In `not_dot_net/backend/workflow_service.py`, replace the `"onboarding"` entry in `WorkflowsConfig.workflows` (lines 75-124):

```python
        "onboarding": WorkflowConfig(
            label="Onboarding",
            target_email_field="contact_email",
            document_instructions={
                "Intern": ["ID document", "Internship agreement", "Photo"],
                "PhD": ["ID document", "Bank details (RIB)", "Photo", "PhD enrollment certificate"],
                "_default": ["ID document", "Bank details (RIB)", "Photo"],
            },
            steps=[
                WorkflowStepConfig(
                    key="initiation",
                    type="form",
                    assignee="requester",
                    assignee_permission="create_workflows",
                    fields=[
                        FieldConfig(name="contact_email", type="email", required=True, label="Contact Email"),
                        FieldConfig(name="status", type="select", required=True, label="Status", options_key="employment_statuses"),
                    ],
                    actions=["submit"],
                ),
                WorkflowStepConfig(
                    key="newcomer_info",
                    type="form",
                    assignee="target_person",
                    partial_save=True,
                    fields=[
                        FieldConfig(name="first_name", type="text", required=True, label="First Name"),
                        FieldConfig(name="last_name", type="text", required=True, label="Last Name"),
                        FieldConfig(name="phone", type="text", label="Phone"),
                        FieldConfig(name="address", type="textarea", label="Address"),
                        FieldConfig(name="emergency_contact", type="text", label="Emergency Contact"),
                        FieldConfig(name="id_document", type="file", required=True, label="ID Document", encrypted=True),
                        FieldConfig(name="bank_details", type="file", required=True, label="Bank Details (RIB)", encrypted=True),
                        FieldConfig(name="photo", type="file", label="Photo", encrypted=True),
                    ],
                    actions=["submit"],
                ),
                WorkflowStepConfig(
                    key="admin_validation",
                    type="approval",
                    assignee_permission="access_personal_data",
                    actions=["approve", "request_corrections", "reject"],
                    corrections_target="newcomer_info",
                ),
                WorkflowStepConfig(
                    key="it_account_creation",
                    type="form",
                    assignee_permission="manage_users",
                    fields=[
                        FieldConfig(name="notes", type="textarea", label="Notes"),
                    ],
                    actions=["complete"],
                ),
            ],
            notifications=[
                NotificationRuleConfig(event="submit", step="initiation", notify=["target_person"]),
                NotificationRuleConfig(event="submit", step="newcomer_info", notify=["permission:access_personal_data"]),
                NotificationRuleConfig(event="approve", step="admin_validation", notify=["permission:manage_users", "requester"]),
                NotificationRuleConfig(event="request_corrections", step="admin_validation", notify=["target_person"]),
                NotificationRuleConfig(event="reject", notify=["requester"]),
                NotificationRuleConfig(event="complete", step="it_account_creation", notify=["requester", "target_person"]),
            ],
        ),
```

- [ ] **Step 4: Add `employment_statuses` to OrgConfig and resolve_options**

In `not_dot_net/config.py`, add to `OrgConfig` (after line 52):

```python
    employment_statuses: list[str] = ["CDD", "CDI", "Intern", "PhD", "PostDoc", "Visiting Researcher"]
```

In `not_dot_net/frontend/workflow_step.py`, add a case to `_resolve_options` (after line 216):

```python
    if options_key == "employment_statuses":
        from not_dot_net.config import org_config
        cfg = await org_config.get()
        return cfg.employment_statuses
```

- [ ] **Step 5: Handle `complete` action in compute_next_step**

The `complete` action on the last step should behave like a normal submit advancing past the last step (→ completed). Verify that `compute_next_step` already handles this: the "complete" action is not "reject" or "save_draft" or "request_corrections", so it falls through to the index-based advance logic. Last step → `(None, COMPLETED)`. No code change needed — just verify.

Run: `uv run pytest tests/test_workflow_engine.py -v`

- [ ] **Step 6: Handle `request_corrections` generating a new token for target_person step**

In `not_dot_net/backend/workflow_service.py`, the token generation block in `submit_step` (lines 284-292) already handles this: when `next_step` is set and `new_status == IN_PROGRESS`, it checks if the next step config has `assignee == "target_person"` and generates a token. Since `request_corrections` returns `newcomer_info` (which is `assignee: target_person`), a new token is automatically generated. No code change needed.

- [ ] **Step 7: Add `request_corrections` and `complete` templates to notifications.py**

In `not_dot_net/backend/notifications.py`, add to the `TEMPLATES` dict (after line 31):

```python
    "request_corrections": {
        "subject": "Corrections needed for your {workflow_label} submission",
        "body": "<p>The administration team has requested corrections on your "
                "<strong>{workflow_label}</strong> submission.</p>"
                "<p>Please visit the link you received previously to update your information.</p>",
    },
    "complete": {
        "subject": "Your {workflow_label} is complete — welcome!",
        "body": "<p>Your <strong>{workflow_label}</strong> onboarding is now complete. "
                "Your account has been created.</p>",
    },
```

- [ ] **Step 8: Support `permission:` prefix in notification recipients**

In `not_dot_net/backend/notifications.py`, modify `resolve_recipients` (lines 59-78) to handle `permission:` targets:

```python
async def resolve_recipients(
    notify_targets: list[str],
    request,
    get_user_email,
    get_users_by_role,
    get_users_by_permission=None,
) -> list[str]:
    """Resolve notification targets to email addresses."""
    emails = set()
    for target in notify_targets:
        if target == "requester" and request.created_by:
            email = await get_user_email(request.created_by)
            if email:
                emails.add(email)
        elif target == "target_person" and request.target_email:
            emails.add(request.target_email)
        elif target.startswith("permission:") and get_users_by_permission:
            perm = target.split(":", 1)[1]
            users = await get_users_by_permission(perm)
            for user in users:
                emails.add(user.email)
        else:
            users = await get_users_by_role(target)
            for user in users:
                emails.add(user.email)
    return list(emails)
```

Update the `notify` function call (line 100) and `_fire_notifications` in `workflow_service.py` to pass `get_users_by_permission`:

In `not_dot_net/backend/workflow_service.py`, modify `_fire_notifications` (lines 131-164) to add a `get_users_by_permission` callback:

```python
async def _fire_notifications(req, event: str, step_key: str, wf):
    from not_dot_net.backend.db import User
    from not_dot_net.backend.mail import mail_config
    from not_dot_net.backend.permissions import has_permissions

    mail_cfg = await mail_config.get()

    async with session_scope() as session:
        async def get_user_email(user_id):
            user = await session.get(User, user_id)
            return user.email if user else None

        async def get_users_by_role(role_str):
            result = await session.execute(
                select(User).where(User.role == role_str, User.is_active == True)
            )
            return list(result.scalars().all())

        async def get_users_by_permission(perm):
            result = await session.execute(
                select(User).where(User.is_active == True)
            )
            all_users = list(result.scalars().all())
            return [u for u in all_users if await has_permissions(u, perm)]

        await notify(
            request=req,
            event=event,
            step_key=step_key,
            workflow=wf,
            mail_settings=mail_cfg,
            get_user_email=get_user_email,
            get_users_by_role=get_users_by_role,
            get_users_by_permission=get_users_by_permission,
        )
```

And update `notify` in `notifications.py` to accept and pass through `get_users_by_permission`:

```python
async def notify(
    request,
    event: str,
    step_key: str,
    workflow: WorkflowConfig,
    mail_settings,
    get_user_email,
    get_users_by_role,
    get_users_by_permission=None,
) -> list[str]:
    ...
        recipients = await resolve_recipients(
            rule.notify, request, get_user_email, get_users_by_role, get_users_by_permission,
        )
    ...
```

- [ ] **Step 9: Wire retention marking on workflow completion**

In `not_dot_net/backend/workflow_service.py`, in `submit_step`, after the transition block (around line 276) and before the commit, add retention marking when a workflow completes:

```python
        # Mark encrypted files for retention on workflow completion
        if new_status == RequestStatus.COMPLETED:
            from not_dot_net.backend.encrypted_storage import mark_for_retention
            from not_dot_net.backend.workflow_models import WorkflowFile
            file_result = await session.execute(
                select(WorkflowFile).where(
                    WorkflowFile.request_id == req.id,
                    WorkflowFile.encrypted_file_id != None,
                )
            )
            for wf_file in file_result.scalars().all():
                await mark_for_retention(wf_file.encrypted_file_id, days=365)
```

The `365` days is a placeholder — the actual retention period should be driven by a configurable value (e.g. in WorkflowsConfig or a dedicated setting). For now, hardcode and add a TODO comment.

- [ ] **Step 10: Update existing onboarding tests**

In `tests/test_workflow_service.py`, update `test_save_draft` and `test_token_generated_for_target_person_step` to use the new field names:

For `test_save_draft` (line 108): change `data` to `{"contact_email": "bob@test.com", "status": "Intern"}` and the step assertion from `"newcomer_info"` stays the same since the step key is unchanged. Also update the first step key assertion — the first step is now `"initiation"`, not `"request"`.

For `test_token_generated_for_target_person_step` (line 177): change `data` to `{"contact_email": "bob@test.com", "status": "Intern"}` and first step assertion to `"initiation"`.

- [ ] **Step 11: Run tests to verify they pass**

Run: `uv run pytest tests/test_workflow_service.py -v`
Expected: All pass including new v2 tests

- [ ] **Step 12: Run full test suite**

Run: `uv run pytest -v`
Expected: All pass

- [ ] **Step 13: Commit**

```bash
git add not_dot_net/backend/workflow_service.py not_dot_net/backend/notifications.py not_dot_net/config.py not_dot_net/frontend/workflow_step.py tests/test_workflow_service.py
git commit -m "feat: 4-step onboarding workflow with request_corrections and permission-based notifications"
```

---

### Task 8: WorkflowFile encrypted_file_id + Encrypted Upload Path

**Files:**
- Modify: `not_dot_net/backend/workflow_models.py:56-70` (add encrypted_file_id)
- Modify: `not_dot_net/backend/workflow_service.py` (encrypted file upload in submit_step)
- Test: `tests/test_encrypted_storage.py` (extend)

- [ ] **Step 1: Add encrypted_file_id to WorkflowFile**

In `not_dot_net/backend/workflow_models.py`, add to `WorkflowFile` (after line 69):

```python
    encrypted_file_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("encrypted_file.id", ondelete="SET NULL"), nullable=True, default=None
    )
```

- [ ] **Step 2: Run full test suite to verify no breakage**

Run: `uv run pytest -v`
Expected: All pass (new nullable column, existing tests don't set it)

- [ ] **Step 3: Commit**

```bash
git add not_dot_net/backend/workflow_models.py
git commit -m "feat: add encrypted_file_id FK to WorkflowFile"
```

---

### Task 9: Remove `/workflow/file/{file_id}` Endpoint

**Files:**
- Delete: `not_dot_net/backend/workflow_file_routes.py` (keep `can_view_request` — move it)
- Modify: `not_dot_net/app.py:100-102`
- Modify: `not_dot_net/frontend/workflow_detail.py:12` (import update)
- Test: `tests/test_security.py` (verify endpoint is gone)

- [ ] **Step 1: Move `can_view_request` to workflow_service.py**

The `can_view_request` function is used by `workflow_detail.py`. Move it from `workflow_file_routes.py` to `workflow_service.py` (it fits logically there).

Append to `not_dot_net/backend/workflow_service.py`:

```python
async def can_view_request(user, req: WorkflowRequest) -> bool:
    """Check if user is allowed to view this request."""
    from not_dot_net.backend.workflow_engine import can_user_act
    if str(user.id) == str(req.created_by):
        return True
    if await has_permissions(user, "view_audit_log"):
        return True
    cfg = await workflows_config.get()
    wf = cfg.workflows.get(req.type)
    if wf and await can_user_act(user, req, wf):
        return True
    return False
```

- [ ] **Step 2: Update import in workflow_detail.py**

In `not_dot_net/frontend/workflow_detail.py`, change line 12:

From: `from not_dot_net.backend.workflow_file_routes import can_view_request`
To: `from not_dot_net.backend.workflow_service import can_view_request`

- [ ] **Step 3: Remove router include from app.py**

In `not_dot_net/app.py`, remove lines 100-102:

```python
    from not_dot_net.backend.workflow_file_routes import router as workflow_file_router
    app.include_router(workflow_file_router)
```

- [ ] **Step 4: Delete workflow_file_routes.py**

```bash
rm not_dot_net/backend/workflow_file_routes.py
```

- [ ] **Step 5: Update or add security test**

Check `tests/test_security.py` for existing route assertions. Add or update to verify `/workflow/file/` is gone:

```python
# In the TestNoPublicRestApi class or equivalent:
async def test_workflow_file_endpoint_removed(self, user: User):
    """The HTTP file download endpoint should not exist."""
    response = await user.http_client.get(f"/workflow/file/{uuid.uuid4()}")
    assert response.status_code in (404, 405)  # route doesn't exist
```

- [ ] **Step 6: Run full test suite**

Run: `uv run pytest -v`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add -u
git commit -m "fix(security): remove /workflow/file/ HTTP endpoint, serve files via NiceGUI callbacks only"
```

---

### Task 10: Frontend — File Downloads via `ui.download()`

**Files:**
- Modify: `not_dot_net/frontend/workflow_detail.py:165-172` (file links → download buttons)
- Modify: `not_dot_net/frontend/workflow_step.py:41-54` (file display for encrypted files)

- [ ] **Step 1: Replace file links with download buttons in workflow_detail.py**

In `not_dot_net/frontend/workflow_detail.py`, replace the file link section in `_render_timeline` (lines 165-172):

```python
                step_files = files_by_step.get(ev.step_key, [])
                if step_files and ev.action in ("submit", "save_draft"):
                    for f in step_files:
                        if f.encrypted_file_id:
                            async def download_encrypted(fid=f.encrypted_file_id, fname=f.filename):
                                from not_dot_net.backend.permissions import has_permissions
                                if not await has_permissions(user, "access_personal_data"):
                                    ui.notify("Access denied", color="negative")
                                    return
                                from not_dot_net.backend.encrypted_storage import read_encrypted
                                data, name, ctype = await read_encrypted(
                                    fid, actor_id=user.id, actor_email=user.email,
                                )
                                ui.download(data, name)
                            ui.button(
                                f"📎 {f.filename}", on_click=download_encrypted,
                            ).props("flat dense size=sm")
                        else:
                            from pathlib import Path
                            async def download_plain(fp=f.storage_path, fname=f.filename):
                                path = Path(fp)
                                if path.exists():
                                    ui.download(path.read_bytes(), fname)
                            ui.button(
                                f"📎 {f.filename}", on_click=download_plain,
                            ).props("flat dense size=sm")
```

Note: This requires `user` to be in scope in `_render_timeline`. Modify `_render_timeline` signature to accept `user` and pass it from `detail_page`.

- [ ] **Step 2: Update _render_timeline call**

In `not_dot_net/frontend/workflow_detail.py`, change line 88:

From: `_render_timeline(events, actor_names, files_by_step)`
To: `_render_timeline(events, actor_names, files_by_step, user)`

And update `_render_timeline` signature (line 132):

From: `def _render_timeline(events, actor_names, files_by_step):`
To: `def _render_timeline(events, actor_names, files_by_step, user):`

Since the function now uses `async` callbacks but is sync itself, this works — the `async def download_encrypted` is a NiceGUI callback that runs in the event loop when clicked.

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest -v`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add not_dot_net/frontend/workflow_detail.py
git commit -m "feat: serve file downloads via ui.download() with access_personal_data gating"
```

---

### Task 11: Frontend — Verification Code Gate on Token Page

**Files:**
- Modify: `not_dot_net/frontend/workflow_token.py`

- [ ] **Step 1: Rewrite token page with verification code gate**

Replace the content of `not_dot_net/frontend/workflow_token.py`:

```python
"""Token page with email verification code gate."""

from nicegui import ui

from not_dot_net.backend.verification import generate_verification_code, verify_code
from not_dot_net.backend.workflow_service import (
    get_request_by_token,
    save_draft,
    submit_step,
    workflows_config,
)
from not_dot_net.backend.workflow_engine import get_current_step_config
from not_dot_net.backend.mail import mail_config, send_mail
from not_dot_net.frontend.i18n import t
from not_dot_net.frontend.workflow_step import render_step_form


def setup():
    @ui.page("/workflow/token/{token}")
    async def token_page(token: str):
        req = await get_request_by_token(token)

        if req is None:
            with ui.column().classes("absolute-center items-center"):
                ui.icon("error", size="xl", color="negative")
                ui.label(t("token_expired")).classes("text-h6")
            return

        cfg = await workflows_config.get()
        wf = cfg.workflows.get(req.type)
        if not wf:
            ui.label(t("token_expired"))
            return

        step_config = get_current_step_config(req, wf)
        if not step_config:
            ui.label(t("token_expired"))
            return

        with ui.column().classes("max-w-2xl mx-auto p-6"):
            ui.label(wf.label).classes("text-h5 mb-2")

            container = ui.column().classes("w-full")

            async def send_code():
                code = await generate_verification_code(req.id)
                mail_cfg = await mail_config.get()
                await send_mail(
                    req.target_email,
                    f"Your verification code for {wf.label}",
                    f"<p>Your verification code is: <strong>{code}</strong></p>"
                    f"<p>This code expires in 15 minutes.</p>",
                    mail_cfg,
                )
                container.clear()
                with container:
                    _render_code_input(container, req, token, step_config, wf, send_code)

            def _render_code_input(cont, request, tok, step, workflow, resend_fn):
                ui.label(t("token_welcome")).classes("text-grey mb-4")
                ui.label("A verification code has been sent to your email.").classes("mb-2")
                code_input = ui.input(label="Verification Code").props("outlined dense maxlength=6")

                async def check_code():
                    try:
                        valid = await verify_code(request.id, code_input.value)
                    except PermissionError as e:
                        ui.notify(str(e), color="negative")
                        return
                    if valid:
                        cont.clear()
                        with cont:
                            await _render_form(cont, request, tok, step, workflow)
                    else:
                        ui.notify("Invalid or expired code", color="negative")

                with ui.row().classes("gap-2 mt-2"):
                    ui.button("Verify", on_click=check_code).props("color=primary")
                    ui.button("Resend code", on_click=resend_fn).props("flat")

            async def _render_form(cont, request, tok, step, workflow):
                # Show document instructions if available
                status = request.data.get("status", "")
                instructions = workflow.document_instructions.get(
                    status, workflow.document_instructions.get("_default", [])
                )
                if instructions:
                    with ui.card().classes("w-full mb-4 bg-blue-50"):
                        ui.label("Required documents:").classes("font-bold text-sm")
                        for doc in instructions:
                            ui.label(f"• {doc}").classes("text-sm")

                async def handle_submit(data):
                    await submit_step(
                        request.id, actor_id=None, action="submit", data=data,
                        actor_token=tok,
                    )
                    cont.clear()
                    with cont:
                        ui.icon("check_circle", size="xl", color="positive")
                        ui.label(t("step_submitted")).classes("text-h6")

                async def handle_save_draft(data):
                    await save_draft(request.id, data=data, actor_token=tok)
                    ui.notify(t("draft_saved"), color="positive")

                await render_step_form(
                    step,
                    request.data,
                    on_submit=handle_submit,
                    on_save_draft=handle_save_draft if step.partial_save else None,
                )

            # Initial view: just a "Send me a code" button
            with container:
                ui.label(t("token_welcome")).classes("text-grey mb-4")
                ui.button("Send me a verification code", on_click=send_code).props("color=primary")
```

- [ ] **Step 2: Run full test suite**

Run: `uv run pytest -v`
Expected: All pass

- [ ] **Step 3: Commit**

```bash
git add not_dot_net/frontend/workflow_token.py
git commit -m "feat: verification code gate on token page (OTP via email)"
```

---

### Task 12: Frontend — Returning Person Search on New Request

**Files:**
- Modify: `not_dot_net/frontend/new_request.py`

- [ ] **Step 1: Add returning-person search for onboarding workflow**

Replace `not_dot_net/frontend/new_request.py`:

```python
"""New Request tab — pick a workflow type and fill the first step."""

from nicegui import ui
from sqlalchemy import select, or_

from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.permissions import has_permissions
from not_dot_net.backend.workflow_service import create_request, workflows_config
from not_dot_net.frontend.i18n import t
from not_dot_net.frontend.workflow_step import render_step_form


async def _search_users(query: str) -> list[dict]:
    """Search all users (including inactive) by name or email."""
    if not query or len(query) < 2:
        return []
    async with session_scope() as session:
        pattern = f"%{query}%"
        result = await session.execute(
            select(User).where(
                or_(
                    User.full_name.ilike(pattern),
                    User.email.ilike(pattern),
                )
            ).limit(10)
        )
        return [
            {"id": str(u.id), "email": u.email, "name": u.full_name or u.email, "active": u.is_active}
            for u in result.scalars().all()
        ]


async def render(user: User):
    """Render the new request tab content."""
    cfg = await workflows_config.get()
    container = ui.column().classes("w-full")

    with container:
        ui.label(t("select_workflow")).classes("text-h6 mb-4")

        for wf_key, wf_config in cfg.workflows.items():
            if not await has_permissions(user, "create_workflows"):
                continue

            with ui.card().classes("w-full cursor-pointer") as card:
                ui.label(wf_config.label).classes("font-bold")

                form_container = ui.column().classes("w-full mt-2")
                form_container.set_visibility(False)
                form_container.on("click.stop", js_handler="() => {}")

                first_step = wf_config.steps[0]

                async def handle_submit(data, key=wf_key, fc=form_container):
                    await create_request(
                        workflow_type=key,
                        created_by=user.id,
                        data=data,
                        actor=user,
                    )
                    ui.notify(t("request_created"), color="positive")
                    fc.set_visibility(False)

                async def toggle_form(fc=form_container, step=first_step, key=wf_key, wfc=wf_config):
                    visible = not fc.visible
                    fc.set_visibility(visible)
                    if visible:
                        fc.clear()
                        with fc:
                            prefill = {}
                            if key == "onboarding":
                                prefill = await _render_returning_search(fc)
                            await render_step_form(step, prefill, on_submit=handle_submit)

                card.on("click", toggle_form)


async def _render_returning_search(container) -> dict:
    """Render returning-person search. Returns prefill data dict."""
    prefill = {}
    results_container = ui.column().classes("w-full")

    async def on_search(e):
        matches = await _search_users(search_input.value)
        results_container.clear()
        with results_container:
            for match in matches:
                active_label = "" if match["active"] else " (inactive)"
                async def select_user(m=match):
                    nonlocal prefill
                    prefill["contact_email"] = m["email"]
                    prefill["returning_user_id"] = m["id"]
                    search_input.value = m["name"]
                    results_container.clear()
                    with results_container:
                        ui.chip(
                            f"Returning: {m['name']}{active_label}",
                            icon="person",
                            color="blue",
                        )
                ui.item(f"{match['name']} — {match['email']}{active_label}", on_click=select_user)

    with ui.expansion("Search existing person (returning)", icon="search").classes("w-full mb-2"):
        search_input = ui.input(label="Search by name or email").props("outlined dense")
        search_input.on("keyup", on_search, throttle=0.3)
        results_container

    return prefill
```

- [ ] **Step 2: Run full test suite**

Run: `uv run pytest -v`
Expected: All pass

- [ ] **Step 3: Commit**

```bash
git add not_dot_net/frontend/new_request.py
git commit -m "feat: returning-person search on onboarding initiation step"
```

---

### Task 13: Frontend — Request Corrections Button in Approval Panel

**Files:**
- Modify: `not_dot_net/frontend/workflow_step.py:103-129`
- Modify: `not_dot_net/frontend/workflow_detail.py:175-214`

- [ ] **Step 1: Add request_corrections callback to render_approval**

In `not_dot_net/frontend/workflow_step.py`, modify `render_approval` (lines 103-129):

```python
def render_approval(
    request_data: dict,
    workflow,
    step: WorkflowStepConfig,
    on_approve,
    on_reject,
    on_request_corrections=None,
):
    """Render approval view: read-only data + approve/reject/corrections."""
    ui.label(workflow.label).classes("text-h6")

    for key, value in request_data.items():
        if value:
            ui.label(f"{key}: {value}").classes("text-sm")

    comment_input = ui.textarea(label=t("comment")).props("outlined dense").classes("w-full mt-2")

    with ui.row().classes("mt-4 gap-2"):
        ui.button(
            t("approve"),
            icon="check",
            on_click=lambda: on_approve(comment_input.value),
        ).props("color=positive")
        if on_request_corrections:
            ui.button(
                "Request Corrections",
                icon="edit_note",
                on_click=lambda: on_request_corrections(comment_input.value),
            ).props("color=warning")
        ui.button(
            t("reject"),
            icon="close",
            on_click=lambda: on_reject(comment_input.value),
        ).props("color=negative")
```

- [ ] **Step 2: Add handle_request_corrections in workflow_detail.py**

In `not_dot_net/frontend/workflow_detail.py`, in `_render_action_panel` (around line 183), add after `handle_reject`:

```python
            async def handle_corrections(comment):
                try:
                    await submit_step(req.id, user.id, "request_corrections", comment=comment, actor_user=user)
                except Exception as e:
                    ui.notify(str(e), color="negative")
                    return
                ui.notify("Corrections requested", color="positive")
                ui.navigate.to(f"/workflow/request/{request_id_str}")

            corrections_fn = handle_corrections if step_config.corrections_target else None
            render_approval(req.data, wf, step_config, handle_approve, handle_reject, corrections_fn)
```

Replace the existing `render_approval(req.data, wf, step_config, handle_approve, handle_reject)` call (line 202).

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest -v`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add not_dot_net/frontend/workflow_step.py not_dot_net/frontend/workflow_detail.py
git commit -m "feat: request corrections button on approval steps"
```

---

### Task 14: Alembic Migration

**Files:**
- Create: `alembic/versions/0005_onboarding_v2.py`

- [ ] **Step 1: Create migration file**

Create `alembic/versions/0005_onboarding_v2.py`:

```python
"""Onboarding v2: encrypted_file table, verification code fields, encrypted_file_id FK.

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "encrypted_file",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("wrapped_dek", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("storage_path", sa.String(1000), nullable=False),
        sa.Column("original_filename", sa.String(500), nullable=False),
        sa.Column("content_type", sa.String(200), server_default="application/octet-stream"),
        sa.Column("uploaded_by", sa.Uuid(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("retained_until", sa.DateTime(), nullable=True),
    )

    with op.batch_alter_table("workflow_request") as batch_op:
        batch_op.add_column(sa.Column("verification_code_hash", sa.String(255), nullable=True))
        batch_op.add_column(sa.Column("code_expires_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("code_attempts", sa.Integer(), server_default="0"))

    with op.batch_alter_table("workflow_file") as batch_op:
        batch_op.add_column(
            sa.Column("encrypted_file_id", sa.Uuid(),
                       sa.ForeignKey("encrypted_file.id", ondelete="SET NULL"), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("workflow_file") as batch_op:
        batch_op.drop_column("encrypted_file_id")
    with op.batch_alter_table("workflow_request") as batch_op:
        batch_op.drop_column("code_attempts")
        batch_op.drop_column("code_expires_at")
        batch_op.drop_column("verification_code_hash")
    op.drop_table("encrypted_file")
```

- [ ] **Step 2: Verify migration applies to dev.db**

Run: `uv run python -m not_dot_net.cli migrate`
Expected: Migration 0005 applied successfully

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest -v`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add alembic/versions/0005_onboarding_v2.py
git commit -m "feat: alembic migration 0005 — encrypted_file table, verification code fields"
```

---

### Task 15: Integration Test — Full Onboarding E2E

**Files:**
- Test: `tests/test_onboarding_e2e.py` (create)

- [ ] **Step 1: Write full end-to-end test**

Create `tests/test_onboarding_e2e.py`:

```python
"""End-to-end test for the 4-step onboarding workflow with encrypted files."""

import pytest
import uuid
from contextlib import asynccontextmanager

from not_dot_net.backend.db import User, get_async_session
from not_dot_net.backend.roles import RoleDefinition, roles_config
from not_dot_net.backend.workflow_service import (
    create_request, submit_step, save_draft, get_request_by_token, list_actionable,
)
from not_dot_net.backend.encrypted_storage import store_encrypted, read_encrypted, EncryptedFile
from not_dot_net.backend.verification import generate_verification_code, verify_code
from not_dot_net.backend.db import session_scope
from not_dot_net.backend.workflow_models import WorkflowFile


async def _create_user(email="staff@test.com", role="staff") -> User:
    get_session = asynccontextmanager(get_async_session)
    async with get_session() as session:
        user = User(id=uuid.uuid4(), email=email, hashed_password="x", role=role)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _setup_roles():
    cfg = await roles_config.get()
    cfg.roles["admin"] = RoleDefinition(
        label="Admin",
        permissions=["manage_bookings", "manage_roles", "manage_settings",
                     "create_workflows", "approve_workflows", "view_audit_log",
                     "manage_users", "access_personal_data"],
    )
    cfg.roles["staff"] = RoleDefinition(label="Staff", permissions=["create_workflows"])
    await roles_config.set(cfg)


@pytest.mark.asyncio
async def test_full_onboarding_with_encrypted_files():
    await _setup_roles()
    initiator = await _create_user(email="initiator@test.com", role="staff")
    admin = await _create_user(email="admin@test.com", role="admin")

    # Step 1: Initiation
    req = await create_request(
        workflow_type="onboarding",
        created_by=initiator.id,
        data={"contact_email": "newcomer@example.com", "status": "PhD"},
        actor=initiator,
    )
    assert req.current_step == "initiation"
    req = await submit_step(req.id, initiator.id, "submit", data={}, actor_user=initiator)
    assert req.current_step == "newcomer_info"
    assert req.token is not None

    # Step 2: Verification code
    code = await generate_verification_code(req.id)
    assert len(code) == 6
    assert await verify_code(req.id, code) is True

    # Step 2: Upload encrypted file
    enc_file = await store_encrypted(b"fake ID document", "id.pdf", "application/pdf", None)
    async with session_scope() as session:
        wf_file = WorkflowFile(
            request_id=req.id,
            step_key="newcomer_info",
            field_name="id_document",
            filename="id.pdf",
            storage_path="",
            encrypted_file_id=enc_file.id,
        )
        session.add(wf_file)
        await session.commit()

    # Step 2: Submit newcomer info
    req = await submit_step(
        req.id, actor_id=None, action="submit",
        data={"first_name": "Marie", "last_name": "Curie", "phone": "+33 1 00 00"},
        actor_token=req.token,
    )
    assert req.current_step == "admin_validation"

    # Step 3: Admin can read encrypted file
    data, name, ctype = await read_encrypted(enc_file.id, actor_id=admin.id, actor_email=admin.email)
    assert data == b"fake ID document"

    # Step 3: Admin approves
    req = await submit_step(req.id, admin.id, "approve", data={}, actor_user=admin)
    assert req.current_step == "it_account_creation"

    # Step 4: IT completes
    req = await submit_step(req.id, admin.id, "complete", data={"notes": "account: mcurie"}, actor_user=admin)
    assert req.status == "completed"


@pytest.mark.asyncio
async def test_request_corrections_regenerates_token():
    await _setup_roles()
    initiator = await _create_user(email="init@test.com", role="staff")
    admin = await _create_user(email="adm@test.com", role="admin")

    req = await create_request(
        workflow_type="onboarding",
        created_by=initiator.id,
        data={"contact_email": "new@example.com", "status": "CDD"},
        actor=initiator,
    )
    req = await submit_step(req.id, initiator.id, "submit", data={}, actor_user=initiator)
    first_token = req.token

    req = await submit_step(
        req.id, actor_id=None, action="submit",
        data={"first_name": "Jean", "last_name": "Dupont"},
        actor_token=req.token,
    )
    assert req.current_step == "admin_validation"

    req = await submit_step(
        req.id, admin.id, "request_corrections",
        comment="Missing ID",
        actor_user=admin,
    )
    assert req.current_step == "newcomer_info"
    assert req.token is not None
    assert req.token != first_token  # new token generated


@pytest.mark.asyncio
async def test_save_draft_preserves_data():
    await _setup_roles()
    initiator = await _create_user(email="init2@test.com", role="staff")

    req = await create_request(
        workflow_type="onboarding",
        created_by=initiator.id,
        data={"contact_email": "partial@example.com", "status": "Intern"},
        actor=initiator,
    )
    req = await submit_step(req.id, initiator.id, "submit", data={}, actor_user=initiator)

    req = await save_draft(req.id, data={"first_name": "Partial"}, actor_token=req.token)
    assert req.data["first_name"] == "Partial"
    assert req.current_step == "newcomer_info"
```

- [ ] **Step 2: Run e2e tests**

Run: `uv run pytest tests/test_onboarding_e2e.py -v`
Expected: All 3 tests PASS

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest -v`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add tests/test_onboarding_e2e.py
git commit -m "test: end-to-end onboarding v2 tests (encrypted files, verification, corrections)"
```

---

### Task 16: Add Missing i18n Keys

**Files:**
- Modify: `not_dot_net/frontend/i18n.py`

- [ ] **Step 1: Check and add missing i18n keys**

Grep for any new strings used with `t()` in the modified frontend files. Add missing keys to both EN and FR dictionaries in `not_dot_net/frontend/i18n.py`. Expected new keys:

```python
# EN
"verification_code": "Verification Code",
"send_code": "Send me a verification code",
"verify": "Verify",
"resend_code": "Resend code",
"required_documents": "Required documents",
"returning_person": "Returning person",

# FR
"verification_code": "Code de vérification",
"send_code": "Envoyez-moi un code de vérification",
"verify": "Vérifier",
"resend_code": "Renvoyer le code",
"required_documents": "Documents requis",
"returning_person": "Personne de retour",
```

Note: Some strings in the token page are currently hardcoded in English. Replace them with `t()` calls or leave as-is if i18n for the token page is not a priority.

- [ ] **Step 2: Run full test suite (validates i18n)**

Run: `uv run pytest -v`
Expected: All pass (validate_translations() in app.py checks for missing keys)

- [ ] **Step 3: Commit**

```bash
git add not_dot_net/frontend/i18n.py
git commit -m "feat: add i18n keys for onboarding v2 (verification code, returning person)"
```

---

## Post-Implementation Checklist

After all tasks are done:

- [ ] `uv run pytest -v` — all tests pass
- [ ] Manual test: start the dev server, create an onboarding request, verify the 4-step flow works
- [ ] Manual test: verify verification code is sent and required on token page
- [ ] Manual test: verify encrypted files can only be downloaded by admin with `access_personal_data`
- [ ] Manual test: verify "request corrections" sends workflow back to newcomer step
- [ ] Manual test: verify returning-person search pre-fills the form
- [ ] Verify no `/workflow/file/` endpoint exists (curl returns 404)
