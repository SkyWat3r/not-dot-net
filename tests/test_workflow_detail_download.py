"""Encrypted-file downloads on the workflow detail page.

An admin with access_personal_data must be able to download files uploaded
by a newcomer; when the server-side read fails (blob missing on disk, key
mismatch, …) the click must surface an error instead of silently doing
nothing (the production symptom: a dead button, error only in pod logs).
"""

import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from nicegui.testing import User as UiUser

from not_dot_net.backend.db import session_scope, get_user_db
from not_dot_net.backend.encrypted_storage import store_encrypted
from not_dot_net.backend.roles import RoleDefinition, roles_config
from not_dot_net.backend.schemas import UserCreate
from not_dot_net.backend.users import get_user_manager, get_jwt_strategy
from not_dot_net.backend.workflow_models import WorkflowFile
from not_dot_net.backend.workflow_service import create_request, submit_step


async def _make_admin(email: str):
    cfg = await roles_config.get()
    cfg.roles["admin"] = RoleDefinition(
        label="Admin",
        permissions=["create_workflows", "approve_workflows", "access_personal_data"],
    )
    await roles_config.set(cfg)
    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as manager:
                db_user = await manager.create(
                    UserCreate(email=email, password="pw123456")
                )
        db_user.role = "admin"
        session.add(db_user)
        await session.commit()
        await session.refresh(db_user)
    return db_user


async def _onboarding_request_with_encrypted_file(admin, submit_newcomer_step=True):
    """Drive onboarding to newcomer_info with one encrypted upload, mirroring
    exactly what frontend/workflow_token.py persists; optionally submit."""
    req = await create_request(
        workflow_type="onboarding",
        created_by=admin.id,
        data={"contact_email": "newcomer@example.com", "status": "PhD"},
        actor=admin,
    )
    req = await submit_step(req.id, admin.id, "submit", data={}, actor_user=admin)
    assert req.current_step == "newcomer_info"

    enc_file = await store_encrypted(
        b"fake ID document", "id.pdf", "application/pdf", uploaded_by=None
    )
    async with session_scope() as session:
        session.add(WorkflowFile(
            request_id=req.id,
            step_key="newcomer_info",
            field_name="id_document",
            filename="id.pdf",
            storage_path="encrypted",
            encrypted_file_id=enc_file.id,
        ))
        await session.commit()

    if submit_newcomer_step:
        req = await submit_step(
            req.id, actor_id=None, action="submit",
            data={"first_name": "Marie", "last_name": "Curie"},
            actor_token=req.token,
        )
        assert req.current_step == "admin_validation"
    return req, enc_file


async def _open_as(user: UiUser, admin, path: str):
    token = await get_jwt_strategy().write_token(admin)
    user.http_client.cookies.set("fastapiusersauth", token)
    await user.open(path)


async def test_admin_can_download_encrypted_file_from_detail_page(user: UiUser):
    admin = await _make_admin("admin-dl@test.com")
    req, enc_file = await _onboarding_request_with_encrypted_file(admin)

    await _open_as(user, admin, f"/workflow/request/{req.id}")
    await user.should_see("id.pdf")

    user.find("id.pdf").click()
    await user.download.next()
    assert user.download.http_responses[-1].content == b"fake ID document"

    Path(enc_file.storage_path).unlink(missing_ok=True)


async def test_failed_encrypted_download_notifies_instead_of_silence(
    user: UiUser, caplog: pytest.LogCaptureFixture
):
    """Reproducer: blob missing on disk → clicking the file button must show
    an error notification, not silently swallow the exception."""
    admin = await _make_admin("admin-dl2@test.com")
    req, enc_file = await _onboarding_request_with_encrypted_file(admin)

    Path(enc_file.storage_path).unlink()

    await _open_as(user, admin, f"/workflow/request/{req.id}")
    await user.should_see("id.pdf")

    user.find("id.pdf").click()
    await user.should_see("Download failed")
    caplog.clear()  # the ERROR log is the expected server-side trace


async def test_uploaded_but_unsubmitted_files_are_visible(user: UiUser):
    """Reproducer: the token page persists WorkflowFile rows at upload time,
    before any submit/save_draft event exists. Such files must still be
    listed on the detail page — otherwise admins see no download link at all
    while the newcomer is (or got stuck) mid-step."""
    admin = await _make_admin("admin-dl3@test.com")
    req, enc_file = await _onboarding_request_with_encrypted_file(
        admin, submit_newcomer_step=False
    )
    assert req.current_step == "newcomer_info"

    await _open_as(user, admin, f"/workflow/request/{req.id}")
    await user.should_see("id.pdf")

    user.find("id.pdf").click()
    await user.download.next()
    assert user.download.http_responses[-1].content == b"fake ID document"

    Path(enc_file.storage_path).unlink(missing_ok=True)
