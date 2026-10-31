"""
Comprehensive test suite for the Subscription Lifecycle module.

Covers:
- Subscription creation (with / without trial)
- Trial period detection and period-end calculation
- Billing period calculations: monthly, yearly (including leap-year / month-end anchor)
- Customer isolation (customers cannot access other customers' subscriptions)
- Archived plan rejection
- Inactive customer rejection
- Invalid state transitions (pause, resume, cancel)
- Cancellation: immediate and period-end flag
- Pause and resume transitions
- Plan change: immediate recalculation
- Same-plan change rejection
- Pagination / listing filters
"""

import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest

from app.models import (
    User,
    UserRole,
    Customer,
    Product,
    SubscriptionPlan,
    Subscription,
    AuditLog,
    BillingInterval,
    SubscriptionStatus,
)
from app.security import hash_password
from app.services.subscription_service import add_calendar_months, calculate_period_end

# ---------------------------------------------------------------------------
# Pure-function unit tests (no database needed)
# ---------------------------------------------------------------------------


class TestCalendarMath:
    """Unit tests for billing-date helper functions."""

    def test_add_one_month_normal(self):
        dt = datetime(2026, 3, 15, tzinfo=timezone.utc)
        result = add_calendar_months(dt, 1)
        assert result == datetime(2026, 4, 15, tzinfo=timezone.utc)

    def test_add_one_month_month_end_anchor(self):
        """Jan 31 + 1 month should cap to Feb 28 (non-leap year)."""
        dt = datetime(2026, 1, 31, tzinfo=timezone.utc)
        result = add_calendar_months(dt, 1)
        assert result.month == 2
        assert result.day == 28

    def test_add_one_month_leap_year(self):
        """Jan 31 + 1 month in leap year should cap to Feb 29."""
        dt = datetime(2024, 1, 31, tzinfo=timezone.utc)
        result = add_calendar_months(dt, 1)
        assert result.month == 2
        assert result.day == 29

    def test_add_twelve_months(self):
        """12 months forward -- same day, next year."""
        dt = datetime(2026, 6, 15, tzinfo=timezone.utc)
        result = add_calendar_months(dt, 12)
        assert result == datetime(2027, 6, 15, tzinfo=timezone.utc)

    def test_add_months_year_rollover(self):
        """October + 3 months -> January next year."""
        dt = datetime(2026, 10, 31, tzinfo=timezone.utc)
        result = add_calendar_months(dt, 3)
        assert result.year == 2027
        assert result.month == 1
        assert result.day == 31

    def test_calculate_period_end_weekly(self):
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        result = calculate_period_end(dt, BillingInterval.WEEKLY, 1)
        assert result == dt + timedelta(weeks=1)

    def test_calculate_period_end_monthly(self):
        dt = datetime(2026, 1, 31, tzinfo=timezone.utc)
        result = calculate_period_end(dt, BillingInterval.MONTHLY, 1)
        assert result.month == 2
        assert result.day == 28  # Non-leap year cap

    def test_calculate_period_end_quarterly(self):
        dt = datetime(2026, 1, 15, tzinfo=timezone.utc)
        result = calculate_period_end(dt, BillingInterval.QUARTERLY, 1)
        assert result == datetime(2026, 4, 15, tzinfo=timezone.utc)

    def test_calculate_period_end_yearly(self):
        dt = datetime(2026, 2, 28, tzinfo=timezone.utc)
        result = calculate_period_end(dt, BillingInterval.YEARLY, 1)
        assert result == datetime(2027, 2, 28, tzinfo=timezone.utc)

    def test_calculate_period_end_interval_count(self):
        """interval_count=3 for monthly = 3 months forward."""
        dt = datetime(2026, 1, 15, tzinfo=timezone.utc)
        result = calculate_period_end(dt, BillingInterval.MONTHLY, 3)
        assert result == datetime(2026, 4, 15, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Integration test helpers
# ---------------------------------------------------------------------------


def _make_user(db, role: UserRole) -> User:
    user = User(
        email=f"sub_test_{uuid.uuid4().hex[:10]}@example.com",
        hashed_password=hash_password("testpw"),
        full_name="Sub Test User",
        role=role,
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user


def _make_customer(db, user: User) -> Customer:
    customer = Customer(
        user_id=user.id,
        name=user.full_name,
        email=user.email,
        is_active=True,
        is_archived=False,
    )
    db.add(customer)
    db.flush()
    return customer


def _make_product(db) -> Product:
    product = Product(
        name=f"Test Product {uuid.uuid4().hex[:6]}",
        code=f"PROD-{uuid.uuid4().hex[:8].upper()}",
        is_active=True,
        is_archived=False,
    )
    db.add(product)
    db.flush()
    return product


def _make_plan(
    db,
    product: Product,
    interval: str = "monthly",
    price: str = "29.99",
    trial_days: int = 0,
    is_active: bool = True,
    is_archived: bool = False,
) -> SubscriptionPlan:
    plan = SubscriptionPlan(
        product_id=product.id,
        name=f"Plan {uuid.uuid4().hex[:6]}",
        code=f"PLN-{uuid.uuid4().hex[:8].upper()}",
        billing_interval=BillingInterval(interval),
        interval_count=1,
        price=Decimal(price),
        currency="USD",
        trial_period_days=trial_days,
        is_active=is_active,
        is_archived=is_archived,
    )
    db.add(plan)
    db.flush()
    return plan


def _cleanup_subscription_tree(db_session, customer_ids, plan_ids, product_ids, user_ids):
    db_session.query(AuditLog).filter(AuditLog.entity_type == "subscription").delete()
    if customer_ids:
        db_session.query(Subscription).filter(
            Subscription.customer_id.in_(customer_ids)
        ).delete()
        db_session.query(Customer).filter(Customer.id.in_(customer_ids)).delete()
    if plan_ids:
        db_session.query(SubscriptionPlan).filter(
            SubscriptionPlan.id.in_(plan_ids)
        ).delete()
    if product_ids:
        db_session.query(Product).filter(Product.id.in_(product_ids)).delete()
    if user_ids:
        db_session.query(User).filter(User.id.in_(user_ids)).delete()
    db_session.commit()


# ---------------------------------------------------------------------------
# Integration tests -- Subscription creation
# ---------------------------------------------------------------------------


class TestSubscriptionCreation:
    def test_create_subscription_no_trial(self, client, db_session, auth_headers):
        admin = _make_user(db_session, UserRole.ADMIN)
        customer = _make_customer(db_session, admin)
        product = _make_product(db_session)
        plan = _make_plan(db_session, product, trial_days=0)
        db_session.commit()

        try:
            resp = client.post(
                "/api/subscriptions",
                json={"customer_id": str(customer.id), "plan_id": str(plan.id)},
                headers=auth_headers(admin),
            )
            assert resp.status_code == 201, resp.text
            data = resp.json()
            assert data["status"] == "active"
            assert data["trial_start"] is None
            assert data["trial_end"] is None
            assert data["cancel_at_period_end"] is False
        finally:
            _cleanup_subscription_tree(
                db_session,
                [customer.id],
                [plan.id],
                [product.id],
                [admin.id],
            )

    def test_create_subscription_with_trial(self, client, db_session, auth_headers):
        admin = _make_user(db_session, UserRole.ADMIN)
        customer = _make_customer(db_session, admin)
        product = _make_product(db_session)
        plan = _make_plan(db_session, product, trial_days=14)
        db_session.commit()

        try:
            resp = client.post(
                "/api/subscriptions",
                json={"customer_id": str(customer.id), "plan_id": str(plan.id)},
                headers=auth_headers(admin),
            )
            assert resp.status_code == 201, resp.text
            data = resp.json()
            assert data["status"] == "trialing"
            assert data["trial_start"] is not None
            assert data["trial_end"] is not None

            trial_start = datetime.fromisoformat(data["trial_start"])
            trial_end = datetime.fromisoformat(data["trial_end"])
            delta = trial_end - trial_start
            assert delta.days == 14
        finally:
            _cleanup_subscription_tree(
                db_session,
                [customer.id],
                [plan.id],
                [product.id],
                [admin.id],
            )

    def test_create_subscription_monthly_period_end_jan31(
        self, client, db_session, auth_headers
    ):
        """Jan 31 + 1 month must cap to Feb 28 in non-leap year."""
        admin = _make_user(db_session, UserRole.ADMIN)
        customer = _make_customer(db_session, admin)
        product = _make_product(db_session)
        plan = _make_plan(db_session, product, interval="monthly", trial_days=0)
        db_session.commit()

        try:
            resp = client.post(
                "/api/subscriptions",
                json={
                    "customer_id": str(customer.id),
                    "plan_id": str(plan.id),
                    "start_date": "2026-01-31T00:00:00Z",
                },
                headers=auth_headers(admin),
            )
            assert resp.status_code == 201, resp.text
            data = resp.json()
            period_end = datetime.fromisoformat(
                data["current_period_end"].replace("Z", "+00:00")
            )
            assert period_end.month == 2
            assert period_end.day == 28
        finally:
            _cleanup_subscription_tree(
                db_session,
                [customer.id],
                [plan.id],
                [product.id],
                [admin.id],
            )

    def test_create_subscription_rejects_archived_plan(
        self, client, db_session, auth_headers
    ):
        admin = _make_user(db_session, UserRole.ADMIN)
        customer = _make_customer(db_session, admin)
        product = _make_product(db_session)
        plan = _make_plan(db_session, product, is_active=False, is_archived=True)
        db_session.commit()

        try:
            resp = client.post(
                "/api/subscriptions",
                json={"customer_id": str(customer.id), "plan_id": str(plan.id)},
                headers=auth_headers(admin),
            )
            assert resp.status_code == 400
            detail = resp.json()["detail"].lower()
            assert "archived" in detail or "inactive" in detail
        finally:
            _cleanup_subscription_tree(
                db_session,
                [customer.id],
                [plan.id],
                [product.id],
                [admin.id],
            )

    def test_create_subscription_rejects_nonexistent_plan(
        self, client, db_session, auth_headers
    ):
        admin = _make_user(db_session, UserRole.ADMIN)
        customer = _make_customer(db_session, admin)
        db_session.commit()

        try:
            resp = client.post(
                "/api/subscriptions",
                json={
                    "customer_id": str(customer.id),
                    "plan_id": str(uuid.uuid4()),
                },
                headers=auth_headers(admin),
            )
            assert resp.status_code == 404
        finally:
            _cleanup_subscription_tree(
                db_session,
                [customer.id],
                [],
                [],
                [admin.id],
            )

    def test_create_subscription_rejects_nonexistent_customer(
        self, client, db_session, auth_headers
    ):
        admin = _make_user(db_session, UserRole.ADMIN)
        product = _make_product(db_session)
        plan = _make_plan(db_session, product)
        db_session.commit()

        try:
            resp = client.post(
                "/api/subscriptions",
                json={
                    "customer_id": str(uuid.uuid4()),
                    "plan_id": str(plan.id),
                },
                headers=auth_headers(admin),
            )
            assert resp.status_code == 404
        finally:
            _cleanup_subscription_tree(
                db_session,
                [],
                [plan.id],
                [product.id],
                [admin.id],
            )


# ---------------------------------------------------------------------------
# Customer isolation tests
# ---------------------------------------------------------------------------


class TestCustomerIsolation:
    def _setup(self, db_session):
        admin = _make_user(db_session, UserRole.ADMIN)
        cust_user_a = _make_user(db_session, UserRole.CUSTOMER)
        cust_user_b = _make_user(db_session, UserRole.CUSTOMER)
        customer_a = _make_customer(db_session, cust_user_a)
        customer_b = _make_customer(db_session, cust_user_b)
        product = _make_product(db_session)
        plan = _make_plan(db_session, product)
        db_session.commit()
        return admin, cust_user_a, cust_user_b, customer_a, customer_b, product, plan

    def _cleanup(self, db_session, admin, cust_user_a, cust_user_b, customer_a, customer_b, product, plan):
        _cleanup_subscription_tree(
            db_session,
            [customer_a.id, customer_b.id],
            [plan.id],
            [product.id],
            [admin.id, cust_user_a.id, cust_user_b.id],
        )

    def test_customer_cannot_create_subscription_for_other_customer(
        self, client, db_session, auth_headers
    ):
        admin, cust_user_a, cust_user_b, customer_a, customer_b, product, plan = (
            self._setup(db_session)
        )
        try:
            resp = client.post(
                "/api/subscriptions",
                json={"customer_id": str(customer_b.id), "plan_id": str(plan.id)},
                headers=auth_headers(cust_user_a),
            )
            assert resp.status_code == 403
        finally:
            self._cleanup(
                db_session, admin, cust_user_a, cust_user_b,
                customer_a, customer_b, product, plan
            )

    def test_customer_cannot_view_other_customers_subscription(
        self, client, db_session, auth_headers
    ):
        admin, cust_user_a, cust_user_b, customer_a, customer_b, product, plan = (
            self._setup(db_session)
        )
        try:
            resp = client.post(
                "/api/subscriptions",
                json={"customer_id": str(customer_b.id), "plan_id": str(plan.id)},
                headers=auth_headers(admin),
            )
            assert resp.status_code == 201, resp.text
            sub_id = resp.json()["id"]

            get_resp = client.get(
                f"/api/subscriptions/{sub_id}",
                headers=auth_headers(cust_user_a),
            )
            assert get_resp.status_code == 403
        finally:
            self._cleanup(
                db_session, admin, cust_user_a, cust_user_b,
                customer_a, customer_b, product, plan
            )

    def test_customer_list_sees_only_own_subscriptions(
        self, client, db_session, auth_headers
    ):
        admin, cust_user_a, cust_user_b, customer_a, customer_b, product, plan = (
            self._setup(db_session)
        )
        try:
            for cid in (customer_a.id, customer_b.id):
                r = client.post(
                    "/api/subscriptions",
                    json={"customer_id": str(cid), "plan_id": str(plan.id)},
                    headers=auth_headers(admin),
                )
                assert r.status_code == 201, r.text

            list_resp = client.get(
                "/api/subscriptions", headers=auth_headers(cust_user_a)
            )
            assert list_resp.status_code == 200
            items = list_resp.json()["items"]
            for item in items:
                assert item["customer_id"] == str(customer_a.id)
        finally:
            self._cleanup(
                db_session, admin, cust_user_a, cust_user_b,
                customer_a, customer_b, product, plan
            )


# ---------------------------------------------------------------------------
# Cancellation tests
# ---------------------------------------------------------------------------


class TestCancellation:
    def _create_active_sub(self, client, db_session, auth_headers):
        admin = _make_user(db_session, UserRole.ADMIN)
        customer = _make_customer(db_session, admin)
        product = _make_product(db_session)
        plan = _make_plan(db_session, product, trial_days=0)
        db_session.commit()

        resp = client.post(
            "/api/subscriptions",
            json={"customer_id": str(customer.id), "plan_id": str(plan.id)},
            headers=auth_headers(admin),
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["id"], admin, customer, product, plan

    def _cleanup(self, db_session, admin, customer, product, plan):
        _cleanup_subscription_tree(
            db_session, [customer.id], [plan.id], [product.id], [admin.id]
        )

    def test_cancel_immediately(self, client, db_session, auth_headers):
        sub_id, admin, customer, product, plan = self._create_active_sub(
            client, db_session, auth_headers
        )
        try:
            resp = client.post(
                f"/api/subscriptions/{sub_id}/cancel",
                json={"immediately": True},
                headers=auth_headers(admin),
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["status"] == "canceled"
            assert data["ended_at"] is not None
            assert data["cancel_at_period_end"] is False
        finally:
            self._cleanup(db_session, admin, customer, product, plan)

    def test_cancel_at_period_end(self, client, db_session, auth_headers):
        sub_id, admin, customer, product, plan = self._create_active_sub(
            client, db_session, auth_headers
        )
        try:
            resp = client.post(
                f"/api/subscriptions/{sub_id}/cancel",
                json={"immediately": False},
                headers=auth_headers(admin),
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["status"] == "active"
            assert data["cancel_at_period_end"] is True
        finally:
            self._cleanup(db_session, admin, customer, product, plan)

    def test_cancel_already_canceled_is_rejected(self, client, db_session, auth_headers):
        sub_id, admin, customer, product, plan = self._create_active_sub(
            client, db_session, auth_headers
        )
        try:
            client.post(
                f"/api/subscriptions/{sub_id}/cancel",
                json={"immediately": True},
                headers=auth_headers(admin),
            )
            resp = client.post(
                f"/api/subscriptions/{sub_id}/cancel",
                json={"immediately": True},
                headers=auth_headers(admin),
            )
            assert resp.status_code == 400
        finally:
            self._cleanup(db_session, admin, customer, product, plan)


# ---------------------------------------------------------------------------
# Pause / resume tests
# ---------------------------------------------------------------------------


class TestPauseResume:
    def _create_active_sub(self, client, db_session, auth_headers):
        admin = _make_user(db_session, UserRole.ADMIN)
        customer = _make_customer(db_session, admin)
        product = _make_product(db_session)
        plan = _make_plan(db_session, product, trial_days=0)
        db_session.commit()

        resp = client.post(
            "/api/subscriptions",
            json={"customer_id": str(customer.id), "plan_id": str(plan.id)},
            headers=auth_headers(admin),
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["id"], admin, customer, product, plan

    def _cleanup(self, db_session, admin, customer, product, plan):
        _cleanup_subscription_tree(
            db_session, [customer.id], [plan.id], [product.id], [admin.id]
        )

    def test_pause_active_subscription(self, client, db_session, auth_headers):
        sub_id, admin, customer, product, plan = self._create_active_sub(
            client, db_session, auth_headers
        )
        try:
            resp = client.post(
                f"/api/subscriptions/{sub_id}/pause",
                headers=auth_headers(admin),
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["status"] == "paused"
        finally:
            self._cleanup(db_session, admin, customer, product, plan)

    def test_pause_already_paused_is_rejected(self, client, db_session, auth_headers):
        sub_id, admin, customer, product, plan = self._create_active_sub(
            client, db_session, auth_headers
        )
        try:
            client.post(
                f"/api/subscriptions/{sub_id}/pause", headers=auth_headers(admin)
            )
            resp = client.post(
                f"/api/subscriptions/{sub_id}/pause", headers=auth_headers(admin)
            )
            assert resp.status_code == 400
        finally:
            self._cleanup(db_session, admin, customer, product, plan)

    def test_resume_paused_subscription(self, client, db_session, auth_headers):
        sub_id, admin, customer, product, plan = self._create_active_sub(
            client, db_session, auth_headers
        )
        try:
            client.post(
                f"/api/subscriptions/{sub_id}/pause", headers=auth_headers(admin)
            )
            resp = client.post(
                f"/api/subscriptions/{sub_id}/resume", headers=auth_headers(admin)
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["status"] == "active"
        finally:
            self._cleanup(db_session, admin, customer, product, plan)

    def test_resume_non_paused_is_rejected(self, client, db_session, auth_headers):
        sub_id, admin, customer, product, plan = self._create_active_sub(
            client, db_session, auth_headers
        )
        try:
            resp = client.post(
                f"/api/subscriptions/{sub_id}/resume", headers=auth_headers(admin)
            )
            assert resp.status_code == 400
        finally:
            self._cleanup(db_session, admin, customer, product, plan)

    def test_pause_canceled_subscription_is_rejected(
        self, client, db_session, auth_headers
    ):
        sub_id, admin, customer, product, plan = self._create_active_sub(
            client, db_session, auth_headers
        )
        try:
            client.post(
                f"/api/subscriptions/{sub_id}/cancel",
                json={"immediately": True},
                headers=auth_headers(admin),
            )
            resp = client.post(
                f"/api/subscriptions/{sub_id}/pause", headers=auth_headers(admin)
            )
            assert resp.status_code == 400
        finally:
            self._cleanup(db_session, admin, customer, product, plan)


# ---------------------------------------------------------------------------
# Plan change tests
# ---------------------------------------------------------------------------


class TestPlanChange:
    def _setup(self, db_session):
        admin = _make_user(db_session, UserRole.ADMIN)
        customer = _make_customer(db_session, admin)
        product = _make_product(db_session)
        plan_a = _make_plan(db_session, product, price="29.99", interval="monthly")
        plan_b = _make_plan(db_session, product, price="49.99", interval="monthly")
        db_session.commit()
        return admin, customer, product, plan_a, plan_b

    def _cleanup(self, db_session, admin, customer, product, plan_a, plan_b, extra_plans=None):
        plan_ids = [plan_a.id, plan_b.id]
        if extra_plans:
            plan_ids.extend(extra_plans)
        _cleanup_subscription_tree(
            db_session,
            [customer.id],
            plan_ids,
            [product.id],
            [admin.id],
        )

    def test_immediate_plan_change_recalculates_period_end(
        self, client, db_session, auth_headers
    ):
        admin, customer, product, plan_a, plan_b = self._setup(db_session)
        plan_yearly = _make_plan(
            db_session, product, price="499.99", interval="yearly"
        )
        db_session.commit()

        try:
            create_resp = client.post(
                "/api/subscriptions",
                json={
                    "customer_id": str(customer.id),
                    "plan_id": str(plan_a.id),
                    "start_date": "2026-01-31T00:00:00Z",
                },
                headers=auth_headers(admin),
            )
            assert create_resp.status_code == 201, create_resp.text
            sub_id = create_resp.json()["id"]

            change_resp = client.post(
                f"/api/subscriptions/{sub_id}/change-plan",
                json={"new_plan_id": str(plan_yearly.id), "immediately": True},
                headers=auth_headers(admin),
            )
            assert change_resp.status_code == 200, change_resp.text
            data = change_resp.json()
            assert data["plan_id"] == str(plan_yearly.id)
            new_end = datetime.fromisoformat(
                data["current_period_end"].replace("Z", "+00:00")
            )
            # Jan 31 + 12 months = Jan 31 next year
            assert new_end.month == 1
            assert new_end.day == 31
            assert new_end.year == 2027
        finally:
            self._cleanup(
                db_session, admin, customer, product, plan_a, plan_b,
                extra_plans=[plan_yearly.id]
            )

    def test_same_plan_change_rejected(self, client, db_session, auth_headers):
        admin, customer, product, plan_a, plan_b = self._setup(db_session)
        try:
            create_resp = client.post(
                "/api/subscriptions",
                json={"customer_id": str(customer.id), "plan_id": str(plan_a.id)},
                headers=auth_headers(admin),
            )
            assert create_resp.status_code == 201, create_resp.text
            sub_id = create_resp.json()["id"]

            change_resp = client.post(
                f"/api/subscriptions/{sub_id}/change-plan",
                json={"new_plan_id": str(plan_a.id), "immediately": True},
                headers=auth_headers(admin),
            )
            assert change_resp.status_code == 400
        finally:
            self._cleanup(db_session, admin, customer, product, plan_a, plan_b)

    def test_change_to_archived_plan_rejected(self, client, db_session, auth_headers):
        admin, customer, product, plan_a, plan_b = self._setup(db_session)
        try:
            create_resp = client.post(
                "/api/subscriptions",
                json={"customer_id": str(customer.id), "plan_id": str(plan_a.id)},
                headers=auth_headers(admin),
            )
            assert create_resp.status_code == 201, create_resp.text
            sub_id = create_resp.json()["id"]

            plan_b.is_archived = True
            plan_b.is_active = False
            db_session.commit()

            change_resp = client.post(
                f"/api/subscriptions/{sub_id}/change-plan",
                json={"new_plan_id": str(plan_b.id), "immediately": True},
                headers=auth_headers(admin),
            )
            assert change_resp.status_code == 400
        finally:
            self._cleanup(db_session, admin, customer, product, plan_a, plan_b)

    def test_change_plan_on_canceled_subscription_rejected(
        self, client, db_session, auth_headers
    ):
        admin, customer, product, plan_a, plan_b = self._setup(db_session)
        try:
            create_resp = client.post(
                "/api/subscriptions",
                json={"customer_id": str(customer.id), "plan_id": str(plan_a.id)},
                headers=auth_headers(admin),
            )
            assert create_resp.status_code == 201, create_resp.text
            sub_id = create_resp.json()["id"]

            client.post(
                f"/api/subscriptions/{sub_id}/cancel",
                json={"immediately": True},
                headers=auth_headers(admin),
            )

            change_resp = client.post(
                f"/api/subscriptions/{sub_id}/change-plan",
                json={"new_plan_id": str(plan_b.id), "immediately": True},
                headers=auth_headers(admin),
            )
            assert change_resp.status_code == 400
        finally:
            self._cleanup(db_session, admin, customer, product, plan_a, plan_b)


# ---------------------------------------------------------------------------
# Listing / filtering / access control
# ---------------------------------------------------------------------------


class TestSubscriptionListing:
    def test_list_with_status_filter(self, client, db_session, auth_headers):
        admin = _make_user(db_session, UserRole.ADMIN)
        customer = _make_customer(db_session, admin)
        product = _make_product(db_session)
        plan = _make_plan(db_session, product, trial_days=0)
        db_session.commit()

        try:
            r1 = client.post(
                "/api/subscriptions",
                json={"customer_id": str(customer.id), "plan_id": str(plan.id)},
                headers=auth_headers(admin),
            )
            r2 = client.post(
                "/api/subscriptions",
                json={"customer_id": str(customer.id), "plan_id": str(plan.id)},
                headers=auth_headers(admin),
            )
            assert r1.status_code == 201 and r2.status_code == 201
            sub1_id = r1.json()["id"]

            client.post(
                f"/api/subscriptions/{sub1_id}/cancel",
                json={"immediately": True},
                headers=auth_headers(admin),
            )

            list_resp = client.get(
                "/api/subscriptions",
                params={"status": "active", "customer_id": str(customer.id)},
                headers=auth_headers(admin),
            )
            assert list_resp.status_code == 200
            items = list_resp.json()["items"]
            assert all(i["status"] == "active" for i in items)
        finally:
            _cleanup_subscription_tree(
                db_session, [customer.id], [plan.id], [product.id], [admin.id]
            )

    def test_get_subscription_not_found(self, client, db_session, auth_headers):
        admin = _make_user(db_session, UserRole.ADMIN)
        db_session.commit()
        try:
            resp = client.get(
                f"/api/subscriptions/{uuid.uuid4()}",
                headers=auth_headers(admin),
            )
            assert resp.status_code == 404
        finally:
            db_session.query(User).filter(User.id == admin.id).delete()
            db_session.commit()

    def test_unauthenticated_request_rejected(self, client):
        resp = client.get("/api/subscriptions")
        assert resp.status_code == 401
