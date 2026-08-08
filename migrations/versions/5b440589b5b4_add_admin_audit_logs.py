"""add admin audit logs

Revision ID: 5b440589b5b4
Revises: 9316507b7275
Create Date: 2026-08-08 00:00:10.728295

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5b440589b5b4'
down_revision: Union[str, Sequence[str], None] = '9316507b7275'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admin_audit_logs",
        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "admin_user",
            sa.String(length=160),
            nullable=False,
        ),
        sa.Column(
            "action",
            sa.String(length=80),
            nullable=False,
        ),
        sa.Column(
            "entity_type",
            sa.String(length=80),
            nullable=False,
        ),
        sa.Column(
            "entity_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "summary",
            sa.String(length=500),
            nullable=False,
        ),
        sa.Column(
            "details",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "ip_address",
            sa.String(length=100),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_admin_audit_logs_admin_user",
        "admin_audit_logs",
        ["admin_user"],
    )

    op.create_index(
        "ix_admin_audit_logs_action",
        "admin_audit_logs",
        ["action"],
    )

    op.create_index(
        "ix_admin_audit_logs_entity_type",
        "admin_audit_logs",
        ["entity_type"],
    )

    op.create_index(
        "ix_admin_audit_logs_entity_id",
        "admin_audit_logs",
        ["entity_id"],
    )

    op.create_index(
        "ix_admin_audit_logs_created_at",
        "admin_audit_logs",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_admin_audit_logs_created_at",
        table_name="admin_audit_logs",
    )
    op.drop_index(
        "ix_admin_audit_logs_entity_id",
        table_name="admin_audit_logs",
    )
    op.drop_index(
        "ix_admin_audit_logs_entity_type",
        table_name="admin_audit_logs",
    )
    op.drop_index(
        "ix_admin_audit_logs_action",
        table_name="admin_audit_logs",
    )
    op.drop_index(
        "ix_admin_audit_logs_admin_user",
        table_name="admin_audit_logs",
    )

    op.drop_table(
        "admin_audit_logs"
    )
