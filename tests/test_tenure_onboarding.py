import pytest
import uuid
from contextlib import asynccontextmanager

from not_dot_net.backend.db import User, get_async_session
from not_dot_net.backend.workflow_service import (
    create_request, submit_step, workflows_config,
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
    cfg.roles["staff"] = RoleDefinition(label="Staff", permissions=["create_workflows"])
    await roles_config.set(cfg)


async def test_onboarding_initiation_has_employer_field():
    cfg = await workflows_config.get()
    onboarding = cfg.workflows["onboarding"]
    initiation = onboarding.steps[0]
    field_names = [f.name for f in initiation.fields]
    assert "employer" in field_names
    employer_field = next(f for f in initiation.fields if f.name == "employer")
    assert employer_field.type == "select"
    assert employer_field.options_key == "employers"


async def test_onboarding_employer_stored_in_request_data():
    await _setup_roles()
    user = await _create_user()
    req = await create_request(
        workflow_type="onboarding",
        created_by=user.id,
        data={"contact_email": "new@test.com", "status": "PhD", "employer": "CNRS"},
        actor=user,
    )
    assert req.data["employer"] == "CNRS"


async def test_org_config_has_employers():
    from not_dot_net.config import org_config
    cfg = await org_config.get()
    assert hasattr(cfg, "employers")
    assert "CNRS" in cfg.employers
