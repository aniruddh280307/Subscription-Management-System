"""Add product variants and plan rules.

Revision ID: b31f6c2a9d10
Revises: 807fd70445a0
Create Date: 2026-10-31

This migration is generated for review only. Do not apply it until approved.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b31f6c2a9d10"
down_revision: Union[str, Sequence[str], None] = "807fd70445a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE billing_interval_enum ADD VALUE IF NOT EXISTS 'daily' BEFORE 'weekly'"
    )

    op.add_column(
        "products",
        sa.Column(
            "product_type",
            sa.String(length=50),
            nullable=False,
            server_default="service",
        ),
    )
    op.add_column(
        "products",
        sa.Column(
            "sales_price",
            sa.Numeric(precision=12, scale=2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
    )
    op.add_column(
        "products",
        sa.Column(
            "cost_price",
            sa.Numeric(precision=12, scale=2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
    )
    op.add_column("products", sa.Column("notes", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_product_sales_price_non_negative", "products", "sales_price >= 0"
    )
    op.create_check_constraint(
        "ck_product_cost_price_non_negative", "products", "cost_price >= 0"
    )

    op.create_table(
        "product_variants",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("attribute_name", sa.String(length=100), nullable=False),
        sa.Column("value", sa.String(length=100), nullable=False),
        sa.Column("sku", sa.String(length=80), nullable=True),
        sa.Column(
            "price_modifier",
            sa.Numeric(precision=12, scale=2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "product_id",
            "attribute_name",
            "value",
            name="uq_product_variant_attribute_value",
        ),
    )
    op.create_index(
        "ix_product_variants_product_id",
        "product_variants",
        ["product_id"],
        unique=False,
    )
    op.create_index(
        "ix_product_variants_sku", "product_variants", ["sku"], unique=True
    )

    op.add_column(
        "subscription_plans",
        sa.Column(
            "minimum_quantity",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.add_column(
        "subscription_plans", sa.Column("available_from", sa.Date(), nullable=True)
    )
    op.add_column(
        "subscription_plans", sa.Column("available_until", sa.Date(), nullable=True)
    )
    op.add_column(
        "subscription_plans",
        sa.Column(
            "payment_terms_days",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "subscription_plans",
        sa.Column(
            "auto_close_on_end",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "subscription_plans",
        sa.Column(
            "allow_early_close",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "subscription_plans",
        sa.Column(
            "allow_pause",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "subscription_plans",
        sa.Column(
            "allow_renewal",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.create_check_constraint(
        "ck_plan_minimum_quantity_positive",
        "subscription_plans",
        "minimum_quantity >= 1",
    )
    op.create_check_constraint(
        "ck_plan_payment_terms_non_negative",
        "subscription_plans",
        "payment_terms_days >= 0",
    )
    op.create_check_constraint(
        "ck_plan_availability_dates_valid",
        "subscription_plans",
        "available_until IS NULL OR available_from IS NULL OR available_until >= available_from",
    )
    op.create_index(
        "ix_subscription_plans_available_from",
        "subscription_plans",
        ["available_from"],
        unique=False,
    )
    op.create_index(
        "ix_subscription_plans_available_until",
        "subscription_plans",
        ["available_until"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_subscription_plans_available_until", table_name="subscription_plans"
    )
    op.drop_index(
        "ix_subscription_plans_available_from", table_name="subscription_plans"
    )
    op.drop_constraint(
        "ck_plan_availability_dates_valid", "subscription_plans", type_="check"
    )
    op.drop_constraint(
        "ck_plan_payment_terms_non_negative", "subscription_plans", type_="check"
    )
    op.drop_constraint(
        "ck_plan_minimum_quantity_positive", "subscription_plans", type_="check"
    )
    op.drop_column("subscription_plans", "allow_renewal")
    op.drop_column("subscription_plans", "allow_pause")
    op.drop_column("subscription_plans", "allow_early_close")
    op.drop_column("subscription_plans", "auto_close_on_end")
    op.drop_column("subscription_plans", "payment_terms_days")
    op.drop_column("subscription_plans", "available_until")
    op.drop_column("subscription_plans", "available_from")
    op.drop_column("subscription_plans", "minimum_quantity")

    op.drop_index("ix_product_variants_sku", table_name="product_variants")
    op.drop_index("ix_product_variants_product_id", table_name="product_variants")
    op.drop_table("product_variants")
    op.drop_constraint(
        "ck_product_cost_price_non_negative", "products", type_="check"
    )
    op.drop_constraint(
        "ck_product_sales_price_non_negative", "products", type_="check"
    )
    op.drop_column("products", "notes")
    op.drop_column("products", "cost_price")
    op.drop_column("products", "sales_price")
    op.drop_column("products", "product_type")

    # PostgreSQL cannot remove an enum value in place. Downgrade is safe only
    # when no plan still uses the 'daily' value.
    op.execute("ALTER TYPE billing_interval_enum RENAME TO billing_interval_enum_old")
    op.execute(
        "CREATE TYPE billing_interval_enum AS ENUM ('weekly', 'monthly', 'quarterly', 'yearly')"
    )
    op.execute(
        "ALTER TABLE subscription_plans ALTER COLUMN billing_interval TYPE "
        "billing_interval_enum USING billing_interval::text::billing_interval_enum"
    )
    op.execute("DROP TYPE billing_interval_enum_old")
