"""Store IDCIF count in daily cutoffs

Revision ID: 9316507b7275
Revises: baecd89230aa
Create Date: 2026-08-06 15:48:27.684420

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9316507b7275'
down_revision: Union[str, Sequence[str], None] = 'baecd89230aa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "daily_cutoffs",
        sa.Column(
            "idcif_count",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.execute(
        """
        UPDATE daily_cutoffs
        SET idcif_count = delivered_count
        WHERE idcif_count IS NULL
        """
    )

    op.alter_column(
        "daily_cutoffs",
        "idcif_count",
        nullable=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column(
        "daily_cutoffs",
        "idcif_count",
    )
