"""A file already uploaded must carry over on corrections: shown as present,
submittable without re-upload."""
import uuid
from datetime import datetime, timedelta, timezone

from nicegui.testing import User
from sqlalchemy import select

from not_dot_net.backend.db import session_scope
from not_dot_net.backend.workflow_models import WorkflowFile, WorkflowRequest
from not_dot_net.backend.workflow_service import WorkflowsConfig, workflows_config
from not_dot_net.config import FieldConfig, WorkflowConfig, WorkflowStepConfig


async def test_existing_file_carries_over_and_submits(user: User, monkeypatch):
    import not_dot_net.frontend.workflow_token as wt_mod

    await workflows_config.set(WorkflowsConfig(workflows={
        "doc_wf": WorkflowConfig(label="Docs", steps=[
            WorkflowStepConfig(
                key="docs", type="form", assignee="target_person",
                fields=[FieldConfig(name="id_document", type="file",
                                    required=True, label="id_document")],
                actions=["submit"]),
            WorkflowStepConfig(key="done", type="form",
                               assignee="target_person", actions=["submit"]),
        ]),
    }))

    tok = str(uuid.uuid4())
    expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1)
    async with session_scope() as s:
        row = WorkflowRequest(type="doc_wf", current_step="docs",
                              token=tok, token_expires_at=expiry)
        s.add(row)
        await s.commit()
        await s.refresh(row)
        req_id = row.id
        s.add(WorkflowFile(request_id=req_id, step_key="docs",
                           field_name="id_document", filename="ALREADY.png",
                           storage_path="data/uploads/x/ALREADY.png"))
        await s.commit()

    async def _true(*_a, **_kw):
        return True

    async def _false(*_a, **_kw):
        return False

    monkeypatch.setattr(wt_mod, "is_locked_out", _false)
    monkeypatch.setattr(wt_mod, "has_valid_code", _true)
    monkeypatch.setattr(wt_mod, "verify_code", _true)

    await user.open(f"/workflow/token/{tok}")
    user.find("Verify").click()
    await user.should_see("ALREADY.png")  # shown as already uploaded

    user.find("Submit").click()
    await user.should_see("Step submitted")  # no re-upload required

    async with session_scope() as s:
        req = (await s.execute(
            select(WorkflowRequest).where(WorkflowRequest.id == req_id)
        )).scalar_one()
        assert req.current_step == "done"
