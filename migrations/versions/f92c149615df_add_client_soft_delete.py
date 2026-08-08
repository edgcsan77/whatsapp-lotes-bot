"""add client soft delete

Revision ID: f92c149615df
Revises: 5b440589b5b4
Create Date: 2026-08-08 12:01:10.830992

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f92c149615df'
down_revision: Union[str, Sequence[str], None] = '5b440589b5b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "clients",
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.create_index(
        "ix_clients_deleted_at",
        "clients",
        ["deleted_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_clients_deleted_at",
        table_name="clients",
    )

    op.drop_column(
        "clients",
        "deleted_at",
    )
