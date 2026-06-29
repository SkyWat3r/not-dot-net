"""Admin detail card groups uploads per field: current + collapsible history."""
import uuid
from contextlib import asynccontextmanager

from nicegui.testing import User as UiUser

from not_dot_net.backend.db import session_scope, get_user_db
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
                db_user = await manager.create(UserCreate(email=email, password="pw123456"))
        db_user.role = "admin"
        session.add(db_user)
        await session.commit()
        await session.refresh(db_user)
    return db_user


async def test_admin_card_shows_current_and_previous(user: UiUser):
    admin = await _make_admin("ver-admin@test.com")
    req = await create_request(
        workflow_type="onboarding", created_by=admin.id,
        data={"contact_email": "n@e.com", "status": "PhD"}, actor=admin,
    )
    req = await submit_step(req.id, admin.id, "submit", data={}, actor_user=admin)

    from datetime import datetime
    async with session_scope() as session:
        session.add(WorkflowFile(
            request_id=req.id, step_key="newcomer_info", field_name="id_document",
            filename="OLD.png", storage_path="data/uploads/x/OLD.png",
            uploaded_at=datetime(2026, 6, 10, 17, 19)))
        session.add(WorkflowFile(
            request_id=req.id, step_key="newcomer_info", field_name="id_document",
            filename="NEW.png", storage_path="data/uploads/x/NEW.png",
            uploaded_at=datetime(2026, 6, 29, 17, 4)))
        await session.commit()

    token = await get_jwt_strategy().write_token(admin)
    user.http_client.cookies.set("fastapiusersauth", token)
    await user.open(f"/workflow/request/{req.id}")

    await user.should_see("NEW.png")        # current
    await user.should_see("previous version")  # history expansion label
