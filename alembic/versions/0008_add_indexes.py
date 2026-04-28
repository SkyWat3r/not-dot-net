"""Add indexes on frequently queried columns.

Revision ID: 0008
Revises: 0007
"""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

INDEXES = [
    # workflow_request
    ("ix_workflow_request_created_by", "workflow_request", ["created_by"]),
    ("ix_workflow_request_created_at", "workflow_request", ["created_at"]),
    ("ix_workflow_request_current_step", "workflow_request", ["current_step"]),
    ("ix_workflow_request_target_email", "workflow_request", ["target_email"]),
    ("ix_workflow_request_token", "workflow_request", ["token"]),
    # workflow_event
    ("ix_workflow_event_request_id", "workflow_event", ["request_id"]),
    # workflow_file
    ("ix_workflow_file_request_id", "workflow_file", ["request_id"]),
    # booking
    ("ix_booking_resource_id", "booking", ["resource_id"]),
    ("ix_booking_user_id", "booking", ["user_id"]),
    ("ix_booking_start_date", "booking", ["start_date"]),
    ("ix_booking_end_date", "booking", ["end_date"]),
    # audit_event
    ("ix_audit_event_category", "audit_event", ["category"]),
    ("ix_audit_event_actor_email", "audit_event", ["actor_email"]),
    ("ix_audit_event_created_at", "audit_event", ["created_at"]),
    # user_tenure
    ("ix_user_tenure_end_date", "user_tenure", ["end_date"]),
]


def upgrade() -> None:
    for name, table, columns in INDEXES:
        op.create_index(name, table, columns)


def downgrade() -> None:
    for name, table, _ in reversed(INDEXES):
        op.drop_index(name, table_name=table)
