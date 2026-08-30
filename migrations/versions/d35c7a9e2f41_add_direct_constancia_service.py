"""add direct constancia service

Revision ID: d35c7a9e2f41
Revises: c84f21d6a731
Create Date: 2026-08-30
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "d35c7a9e2f41"
down_revision: str | None = "c84f21d6a731"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "clients",
        sa.Column(
            "direct_price_per_request",
            sa.Numeric(12, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "clients",
        sa.Column(
            "direct_pdf_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "daily_cutoffs",
        sa.Column(
            "direct_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("daily_cutoffs", "direct_count")
    op.drop_column("clients", "direct_pdf_enabled")
    op.drop_column("clients", "direct_price_per_request")
