"""Portfolio analyzer: portfolios, instruments, formalize investment_* tables.

Revision ID: 008_portfolio_analyzer
Revises: 007_task_deadline
Create Date: 2026-07-19

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from migration_utils import add_column_if_missing, table_exists

revision: str = "008_portfolio_analyzer"
down_revision: Union[str, None] = "007_task_deadline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ensure_portfolio_tables() -> None:
    if not table_exists("portfolios"):
        op.create_table(
            "portfolios",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("slug", sa.String(length=50), nullable=False),
            sa.Column("type", sa.String(length=20), nullable=False),
            sa.Column("legacy_goal_id", sa.Integer(), nullable=True),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("broker_contract", sa.String(length=50), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
            ),
        )
        op.create_index("ix_portfolios_slug", "portfolios", ["slug"], unique=True)

    if not table_exists("portfolio_goals"):
        op.create_table(
            "portfolio_goals",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("portfolio_id", sa.Integer(), sa.ForeignKey("portfolios.id"), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("target_amount", sa.Float(), nullable=False),
            sa.Column("current_amount", sa.Float(), server_default="0"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
            ),
        )

    if not table_exists("instruments"):
        op.create_table(
            "instruments",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("ticker", sa.String(length=20), nullable=True),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("asset_type", sa.String(length=20), nullable=False, server_default="other"),
            sa.Column("maturity_date", sa.Date(), nullable=True),
            sa.Column("coupon_rate", sa.Float(), nullable=True),
            sa.Column("aliases", sa.JSON(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
            ),
        )

    if not table_exists("positions"):
        op.create_table(
            "positions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("portfolio_id", sa.Integer(), sa.ForeignKey("portfolios.id"), nullable=False),
            sa.Column("instrument_id", sa.Integer(), sa.ForeignKey("instruments.id"), nullable=False),
            sa.Column("snapshot_date", sa.Date(), nullable=False),
            sa.Column("quantity", sa.Float(), nullable=False),
            sa.Column("avg_price", sa.Float(), nullable=True),
            sa.Column("market_value", sa.Float(), nullable=False),
            sa.Column("weight_pct", sa.Float(), nullable=True),
            sa.UniqueConstraint(
                "portfolio_id",
                "instrument_id",
                "snapshot_date",
                name="uq_position_snapshot",
            ),
        )

    if not table_exists("import_log"):
        op.create_table(
            "import_log",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("portfolio_id", sa.Integer(), sa.ForeignKey("portfolios.id"), nullable=False),
            sa.Column("report_date", sa.Date(), nullable=False),
            sa.Column("source", sa.String(length=50), nullable=False, server_default="hermes"),
            sa.Column("payload_hash", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="ok"),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
            ),
        )


def _ensure_investment_tables() -> None:
    if not table_exists("investment_snapshots"):
        op.create_table(
            "investment_snapshots",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("goal_id", sa.Integer(), nullable=True),
            sa.Column("portfolio_id", sa.Integer(), sa.ForeignKey("portfolios.id"), nullable=True),
            sa.Column("date", sa.Date(), nullable=False),
            sa.Column("total_balance", sa.Float(), nullable=False),
        )
    else:
        add_column_if_missing(
            "investment_snapshots",
            sa.Column("portfolio_id", sa.Integer(), nullable=True),
        )

    if not table_exists("investment_flows"):
        op.create_table(
            "investment_flows",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("goal_id", sa.Integer(), nullable=True),
            sa.Column("portfolio_id", sa.Integer(), sa.ForeignKey("portfolios.id"), nullable=True),
            sa.Column("date", sa.Date(), nullable=False),
            sa.Column("type", sa.String(length=50), nullable=False),
            sa.Column("amount", sa.Float(), nullable=False),
            sa.Column("description", sa.String(length=500), nullable=True),
        )
    else:
        add_column_if_missing(
            "investment_flows",
            sa.Column("portfolio_id", sa.Integer(), nullable=True),
        )


def _seed_portfolio_data() -> None:
    bind = op.get_bind()
    count = bind.execute(sa.text("SELECT COUNT(*) FROM portfolios")).scalar()
    if count and count > 0:
        _backfill_portfolio_ids()
        return

    op.execute(
        sa.text(
            """
            INSERT INTO portfolios (id, name, slug, type, legacy_goal_id, sort_order, broker_contract)
            VALUES
                (1, 'ИИС', 'iis', 'iis', 1, 1, '9248208'),
                (2, 'Подушка', 'podushka', 'reserve', 3, 2, '1226101/21-л'),
                (3, 'Брокерский 1', 'broker-1', 'broker', 6, 3, '1149213'),
                (4, 'Брокерский 2', 'broker-2', 'broker', 7, 4, NULL)
            """
        )
    )

    op.execute(
        sa.text(
            "UPDATE financial_goals SET name = 'Брокерский 1' WHERE id = 6"
        )
    )

    op.execute(
        sa.text(
            """
            INSERT INTO portfolio_goals (portfolio_id, name, target_amount, current_amount)
            SELECT 3, 'Автомобиль', target_amount, current_amount
            FROM financial_goals
            WHERE id = 6
            """
        )
    )

    _backfill_portfolio_ids()


def _backfill_portfolio_ids() -> None:
    op.execute(
        sa.text(
            """
            UPDATE investment_snapshots
            SET portfolio_id = (
                SELECT p.id FROM portfolios p
                WHERE p.legacy_goal_id = investment_snapshots.goal_id
            )
            WHERE portfolio_id IS NULL AND goal_id IS NOT NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE investment_flows
            SET portfolio_id = (
                SELECT p.id FROM portfolios p
                WHERE p.legacy_goal_id = investment_flows.goal_id
            )
            WHERE portfolio_id IS NULL AND goal_id IS NOT NULL
            """
        )
    )


def upgrade() -> None:
    _ensure_portfolio_tables()
    _ensure_investment_tables()
    _seed_portfolio_data()


def downgrade() -> None:
    pass
