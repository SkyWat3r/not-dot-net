"""Group workflow file uploads into the current + historical versions per field."""

import uuid
from dataclasses import dataclass

from sqlalchemy import select

from not_dot_net.backend.db import session_scope
from not_dot_net.backend.workflow_models import WorkflowFile


@dataclass(frozen=True)
class FieldFileGroup:
    step_key: str
    field_name: str
    current: WorkflowFile
    previous: list[WorkflowFile]


def _newest_first(rows: list[WorkflowFile]) -> list[WorkflowFile]:
    # Stable tie-break on id so equal timestamps stay deterministic.
    return sorted(rows, key=lambda f: (f.uploaded_at, str(f.id)), reverse=True)


def group_files_by_field(files: list[WorkflowFile]) -> list[FieldFileGroup]:
    grouped: dict[tuple[str, str], list[WorkflowFile]] = {}
    for f in files:
        grouped.setdefault((f.step_key, f.field_name), []).append(f)
    groups: list[FieldFileGroup] = []
    for (step_key, field_name), rows in grouped.items():
        ordered = _newest_first(rows)
        groups.append(FieldFileGroup(
            step_key=step_key, field_name=field_name,
            current=ordered[0], previous=ordered[1:],
        ))
    return groups


def current_files_by_name(files: list[WorkflowFile]) -> dict[str, WorkflowFile]:
    by_name: dict[str, list[WorkflowFile]] = {}
    for f in files:
        by_name.setdefault(f.field_name, []).append(f)
    return {name: _newest_first(rows)[0] for name, rows in by_name.items()}


async def load_files(request_id: uuid.UUID, step_key: str | None = None) -> list[WorkflowFile]:
    async with session_scope() as session:
        query = select(WorkflowFile).where(WorkflowFile.request_id == request_id)
        if step_key is not None:
            query = query.where(WorkflowFile.step_key == step_key)
        return list((await session.execute(query)).scalars().all())
