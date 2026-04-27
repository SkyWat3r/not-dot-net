import pytest
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from not_dot_net.backend.db import User, get_async_session
from not_dot_net.backend.workflow_service import (
    create_request,
    submit_step,
    workflows_config,
    resend_notification,
)
from not_dot_net.backend.roles import RoleDefinition, roles_config


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
        permissions=[
            "create_workflows", "approve_workflows", "view_audit_log",
            "manage_users", "access_personal_data",
        ],
    )
    cfg.roles["staff"] = RoleDefinition(
        label="Staff",
        permissions=["create_workflows"],
    )
    cfg.roles["director"] = RoleDefinition(
        label="Director",
        permissions=["create_workflows", "approve_workflows"],
    )
    await roles_config.set(cfg)


async def test_resend_notification_regenerates_token():
    """resend_notification replaces the token and sets a future expiry."""
    await _setup_roles()
    admin = await _create_user("admin@test.com", role="admin")
    staff = await _create_user("staff@test.com", role="staff")

    # Create an onboarding request and advance to newcomer_info (target_person step).
    req = await create_request(
        workflow_type="onboarding",
        created_by=staff.id,
        data={"contact_email": "newcomer@test.com", "status": "PhD", "employer": "CNRS"},
        actor=staff,
    )
    req = await submit_step(req.id, staff.id, "submit", data={}, actor_user=staff)
    assert req.current_step == "newcomer_info"
    original_token = req.token
    assert original_token is not None

    updated = await resend_notification(req.id, actor_user=admin)

    assert updated.token != original_token
    assert updated.token is not None
    assert updated.token_expires_at is not None
    expires = updated.token_expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    assert expires > datetime.now(timezone.utc)


async def test_resend_notification_requires_permission():
    """A plain staff user cannot resend notifications."""
    await _setup_roles()
    staff = await _create_user("staff2@test.com", role="staff")

    req = await create_request(
        workflow_type="onboarding",
        created_by=staff.id,
        data={"contact_email": "newcomer2@test.com", "status": "PhD", "employer": "CNRS"},
        actor=staff,
    )
    req = await submit_step(req.id, staff.id, "submit", data={}, actor_user=staff)
    assert req.current_step == "newcomer_info"

    with pytest.raises(PermissionError):
        await resend_notification(req.id, actor_user=staff)


async def test_resend_only_for_target_person_steps():
    """resend_notification raises ValueError when current step is not target_person."""
    await _setup_roles()
    staff = await _create_user("staff3@test.com", role="staff")

    # vpn_access: after submit the step moves to 'approval' (director, not target_person).
    req = await create_request(
        workflow_type="vpn_access",
        created_by=staff.id,
        data={"target_name": "Bob", "target_email": "bob@test.com"},
        actor=staff,
    )
    req = await submit_step(req.id, staff.id, "submit", data={}, actor_user=staff)
    assert req.current_step == "approval"

    admin = await _create_user("admin2@test.com", role="admin")
    with pytest.raises(ValueError, match="not assigned to target_person"):
        await resend_notification(req.id, actor_user=admin)
