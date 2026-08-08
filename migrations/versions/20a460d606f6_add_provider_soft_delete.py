"""add provider soft delete

Revision ID: 20a460d606f6
Revises: f92c149615df
Create Date: 2026-08-08 12:04:46.024418

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20a460d606f6'
down_revision: Union[str, Sequence[str], None] = 'f92c149615df'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "providers",
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.create_index(
        "ix_providers_deleted_at",
        "providers",
        ["deleted_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_providers_deleted_at",
        table_name="providers",
    )

    op.drop_column(
        "providers",
        "deleted_at",
    )
