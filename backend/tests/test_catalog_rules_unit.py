from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models import Base, BillingInterval
from app.schemas.plan import PlanCreateRequest
from app.schemas.product import ProductCreateRequest
from app.services.dashboard_service import _normalize_to_monthly
from app.services.subscription_service import calculate_period_end


def test_product_accepts_generic_variant_price_modifier():
    product = ProductCreateRequest(
        name="Business Software",
        code="BUS-SW",
        product_type="software",
        sales_price="1200.00",
        cost_price="500.00",
        notes="Sold as a recurring service",
        variants=[
            {
                "attribute_name": "Brand",
                "value": "Odoo",
                "sku": "BUS-ODOO",
                "price_modifier": "560.00",
            }
        ],
    )
    assert product.variants[0].attribute_name == "Brand"
    assert product.variants[0].price_modifier == Decimal("560.00")


@pytest.mark.parametrize("field", ["sales_price", "cost_price"])
def test_product_rejects_negative_base_prices(field):
    payload = {"name": "Invalid", "code": "INVALID", field: "-0.01"}
    with pytest.raises(ValidationError):
        ProductCreateRequest(**payload)


def test_plan_accepts_daily_rules_and_availability():
    plan = PlanCreateRequest(
        product_id="00000000-0000-0000-0000-000000000001",
        name="Daily Flex",
        code="DAILY-FLEX",
        billing_interval="daily",
        interval_count=2,
        price="10.00",
        minimum_quantity=3,
        available_from="2026-11-01",
        available_until="2026-12-31",
        payment_terms_days=7,
        auto_close_on_end=True,
        allow_pause=False,
    )
    assert plan.billing_interval == BillingInterval.DAILY
    assert plan.minimum_quantity == 3
    assert plan.allow_pause is False


def test_plan_rejects_reversed_availability_dates():
    with pytest.raises(ValidationError):
        PlanCreateRequest(
            product_id="00000000-0000-0000-0000-000000000001",
            name="Invalid Dates",
            code="INVALID-DATES",
            billing_interval="monthly",
            price="10.00",
            available_from=date(2026, 12, 1),
            available_until=date(2026, 11, 1),
        )


def test_daily_period_math():
    start = datetime(2026, 10, 31, 9, 30, tzinfo=timezone.utc)
    assert calculate_period_end(start, BillingInterval.DAILY, 3) == datetime(
        2026, 11, 3, 9, 30, tzinfo=timezone.utc
    )


def test_mrr_normalization_respects_multi_interval_cycles():
    assert _normalize_to_monthly(
        Decimal("100.00"), BillingInterval.MONTHLY, 2
    ) == Decimal("50.00")
    assert _normalize_to_monthly(
        Decimal("365.00"), BillingInterval.DAILY, 1
    ).quantize(Decimal("0.01")) == Decimal("11102.08")


def test_metadata_contains_new_catalog_columns_and_table():
    assert "product_variants" in Base.metadata.tables
    assert {
        "product_type",
        "sales_price",
        "cost_price",
        "notes",
    }.issubset(Base.metadata.tables["products"].columns.keys())
    assert {
        "minimum_quantity",
        "available_from",
        "available_until",
        "payment_terms_days",
        "auto_close_on_end",
        "allow_early_close",
        "allow_pause",
        "allow_renewal",
    }.issubset(Base.metadata.tables["subscription_plans"].columns.keys())
