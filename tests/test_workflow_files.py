import uuid
from datetime import datetime, timedelta, timezone

from not_dot_net.backend.workflow_models import WorkflowFile, WorkflowRequest
from not_dot_net.backend.workflow_files import (
    group_files_by_field,
    current_files_by_name,
    load_files,
)
from not_dot_net.backend.db import session_scope

REQ = uuid.uuid4()


def _wf(field: str, dt: datetime, name: str, step: str = "newcomer_info") -> WorkflowFile:
    return WorkflowFile(
        request_id=REQ, step_key=step, field_name=field,
        filename=name, storage_path="x", uploaded_at=dt,
    )


def test_group_current_is_newest_and_previous_ordered():
    old = _wf("id_document", datetime(2026, 6, 10, 17, 19), "old.png")
    mid = _wf("id_document", datetime(2026, 6, 20, 9, 0), "mid.png")
    new = _wf("id_document", datetime(2026, 6, 29, 17, 4), "new.png")
    groups = group_files_by_field([old, new, mid])
    assert len(groups) == 1
    g = groups[0]
    assert g.field_name == "id_document"
    assert g.current.filename == "new.png"
    assert [p.filename for p in g.previous] == ["mid.png", "old.png"]


def test_group_separates_fields():
    a = _wf("id_document", datetime(2026, 6, 10, 1, 0), "id.png")
    b = _wf("bank_details", datetime(2026, 6, 10, 2, 0), "rib.png")
    by_field = {g.field_name: g for g in group_files_by_field([a, b])}
    assert set(by_field) == {"id_document", "bank_details"}
    assert by_field["bank_details"].previous == []


def test_current_files_by_name_picks_newest():
    old = _wf("id_document", datetime(2026, 6, 10, 1, 0), "old.png")
    new = _wf("id_document", datetime(2026, 6, 29, 1, 0), "new.png")
    current = current_files_by_name([old, new])
    assert current["id_document"].filename == "new.png"


async def test_load_files_filters_by_step():
    expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1)
    async with session_scope() as s:
        row = WorkflowRequest(type="doc_wf", current_step="docs",
                              token=str(uuid.uuid4()), token_expires_at=expiry)
        s.add(row)
        await s.commit()
        await s.refresh(row)
        rid = row.id
    async with session_scope() as s:
        s.add(WorkflowFile(request_id=rid, step_key="newcomer_info",
                           field_name="id_document", filename="a.png", storage_path="x"))
        s.add(WorkflowFile(request_id=rid, step_key="other_step",
                           field_name="x", filename="b.png", storage_path="x"))
        await s.commit()

    all_rows = await load_files(rid)
    step_rows = await load_files(rid, "newcomer_info")
    assert len(all_rows) == 2
    assert [f.filename for f in step_rows] == ["a.png"]
