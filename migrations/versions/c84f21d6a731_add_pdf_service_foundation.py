"""Add PDF service foundation.

Revision ID: c84f21d6a731
Revises: 20a460d606f6
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c84f21d6a731"
down_revision: str | None = "20a460d606f6"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "clients",
        sa.Column(
            "generic_price_per_request",
            sa.Numeric(12, 2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
    )

    op.add_column(
        "clients",
        sa.Column(
            "generic_pdf_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.add_column(
        "clients",
        sa.Column(
            "idcif_pdf_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.add_column(
        "requests",
        sa.Column(
            "service_type",
            sa.String(length=20),
            nullable=False,
            server_default="RFC_IDCIF",
        ),
    )

    op.add_column(
        "requests",
        sa.Column(
            "delivery_format",
            sa.String(length=10),
            nullable=False,
            server_default="TEXT",
        ),
    )

    op.add_column(
        "requests",
        sa.Column(
            "lookup_route",
            sa.String(length=64),
            nullable=True,
        ),
    )

    op.add_column(
        "requests",
        sa.Column(
            "pdf_status",
            sa.String(length=30),
            nullable=True,
        ),
    )

    op.add_column(
        "requests",
        sa.Column(
            "pdf_url",
            sa.Text(),
            nullable=True,
        ),
    )

    op.add_column(
        "requests",
        sa.Column(
            "pdf_filename",
            sa.String(length=255),
            nullable=True,
        ),
    )

    op.add_column(
        "requests",
        sa.Column(
            "pdf_error",
            sa.Text(),
            nullable=True,
        ),
    )

    op.add_column(
        "requests",
        sa.Column(
            "pdf_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )

    op.add_column(
        "requests",
        sa.Column(
            "pdf_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.add_column(
        "requests",
        sa.Column(
            "pdf_next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.add_column(
        "requests",
        sa.Column(
            "pdf_delivered_message_id",
            sa.String(length=200),
            nullable=True,
        ),
    )

    op.create_index(
        "ix_requests_service_type",
        "requests",
        ["service_type"],
        unique=False,
    )

    op.create_index(
        "ix_requests_pdf_status",
        "requests",
        ["pdf_status"],
        unique=False,
    )

    op.create_index(
        "ix_requests_pdf_next_attempt_at",
        "requests",
        ["pdf_next_attempt_at"],
        unique=False,
    )

    op.drop_constraint(
        "uq_requests_message_identifier",
        "requests",
        type_="unique",
    )

    op.create_unique_constraint(
        "uq_requests_message_identifier_service",
        "requests",
        [
            "whatsapp_message_id",
            "identifier_key",
            "service_type",
        ],
    )

    op.add_column(
        "daily_cutoffs",
        sa.Column(
            "generic_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column(
        "daily_cutoffs",
        "generic_count",
    )

    op.drop_constraint(
        "uq_requests_message_identifier_service",
        "requests",
        type_="unique",
    )

    op.create_unique_constraint(
        "uq_requests_message_identifier",
        "requests",
        [
            "whatsapp_message_id",
            "identifier_key",
        ],
    )

    op.drop_index(
        "ix_requests_pdf_next_attempt_at",
        table_name="requests",
    )

    op.drop_index(
        "ix_requests_pdf_status",
        table_name="requests",
    )

    op.drop_index(
        "ix_requests_service_type",
        table_name="requests",
    )

    op.drop_column(
        "requests",
        "pdf_delivered_message_id",
    )

    op.drop_column(
        "requests",
        "pdf_next_attempt_at",
    )

    op.drop_column(
        "requests",
        "pdf_started_at",
    )

    op.drop_column(
        "requests",
        "pdf_attempts",
    )

    op.drop_column(
        "requests",
        "pdf_error",
    )

    op.drop_column(
        "requests",
        "pdf_filename",
    )

    op.drop_column(
        "requests",
        "pdf_url",
    )

    op.drop_column(
        "requests",
        "pdf_status",
    )

    op.drop_column(
        "requests",
        "lookup_route",
    )

    op.drop_column(
        "requests",
        "delivery_format",
    )

    op.drop_column(
        "requests",
        "service_type",
    )

    op.drop_column(
        "clients",
        "idcif_pdf_enabled",
    )

    op.drop_column(
        "clients",
        "generic_pdf_enabled",
    )

    op.drop_column(
        "clients",
        "generic_price_per_request",
    )
