"""
Tests for the Dashboard and Reports module.

Approach:
- Creates real DB records (customers, subscriptions, invoices, payments) then
  verifies the aggregation endpoints return correct values.
- All metrics verified to match the data actually inserted — no hardcoded or
  random expected values.
- Does not test MRR/ARR exact decimal math exhaustively (covered implicitly);
  focuses on structural correctness, access control, and data presence.
"""

import uuid
from datetime import date, timedelta, datetime, timezone
from decimal import Decimal

import pytest

from app.models import (
    User,
    UserRole,
    Customer,
    Product,
    SubscriptionPlan,
    Subscription,
    SubscriptionStatus,
    BillingInterval,
    Invoice,
    InvoiceItem,
    InvoiceStatus,
    Payment,
    PaymentStatus,
    AuditLog,
)
from app.security import hash_password


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(db, role=UserRole.STAFF):
    u = User(
        email=f"dash_{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("pw"),
        full_name="Dash Tester",
        role=role,
        is_active=True,
    )
    db.add(u)
    db.flush()
    return u


def _make_customer(db, user):
    c = Customer(
        user_id=user.id,
        name=user.full_name,
        email=user.email,
        is_active=True,
        is_archived=False,
    )
    db.add(c)
    db.flush()
    return c


def _make_plan(db, product, interval="monthly", price="100.00"):
    plan = SubscriptionPlan(
        product_id=product.id,
        name=f"Plan {uuid.uuid4().hex[:6]}",
        code=f"PLN-{uuid.uuid4().hex[:8].upper()}",
        billing_interval=BillingInterval(interval),
        interval_count=1,
        price=Decimal(price),
        currency="USD",
        trial_period_days=0,
        is_active=True,
        is_archived=False,
    )
    db.add(plan)
    db.flush()
    return plan


def _make_product(db):
    p = Product(
        name=f"Product {uuid.uuid4().hex[:6]}",
        code=f"PROD-{uuid.uuid4().hex[:8].upper()}",
        is_active=True,
        is_archived=False,
    )
    db.add(p)
    db.flush()
    return p


def _make_active_subscription(db, customer, plan):
    now = datetime.now(timezone.utc)
    sub = Subscription(
        customer_id=customer.id,
        plan_id=plan.id,
        status=SubscriptionStatus.ACTIVE,
        start_date=now,
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
        cancel_at_period_end=False,
    )
    db.add(sub)
    db.flush()
    return sub


def _make_open_invoice(db, customer, amount="200.00"):
    today = date.today()
    inv = Invoice(
        invoice_number=f"INV-TEST-{uuid.uuid4().hex[:8].upper()}",
        customer_id=customer.id,
        status=InvoiceStatus.OPEN,
        subtotal=Decimal(amount),
        tax_amount=Decimal("0.00"),
        discount_amount=Decimal("0.00"),
        total_amount=Decimal(amount),
        amount_paid=Decimal("0.00"),
        amount_due=Decimal(amount),
        currency="USD",
        issue_date=today,
        due_date=today + timedelta(days=30),
    )
    db.add(inv)
    db.flush()
    return inv


def _make_overdue_invoice(db, customer, amount="150.00"):
    yesterday = date.today() - timedelta(days=1)
    inv = Invoice(
        invoice_number=f"INV-OVD-{uuid.uuid4().hex[:8].upper()}",
        customer_id=customer.id,
        status=InvoiceStatus.OVERDUE,
        subtotal=Decimal(amount),
        tax_amount=Decimal("0.00"),
        discount_amount=Decimal("0.00"),
        total_amount=Decimal(amount),
        amount_paid=Decimal("0.00"),
        amount_due=Decimal(amount),
        currency="USD",
        issue_date=yesterday - timedelta(days=30),
        due_date=yesterday,
    )
    db.add(inv)
    db.flush()
    return inv


def _make_payment(db, invoice, amount="200.00", status=PaymentStatus.SUCCEEDED):
    pay = Payment(
        payment_number=f"PAY-TEST-{uuid.uuid4().hex[:8].upper()}",
        invoice_id=invoice.id,
        customer_id=invoice.customer_id,
        amount=Decimal(amount),
        currency="USD",
        status=status,
        payment_method="bank_transfer",
        refunded_amount=Decimal("0.00"),
    )
    db.add(pay)
    db.flush()
    return pay


def _cleanup_all(db_session, user_ids, customer_ids, plan_ids=None, product_ids=None, sub_ids=None):
    db_session.query(AuditLog).filter(
        AuditLog.entity_type.in_(["invoice", "payment", "subscription"])
    ).delete()
    if customer_ids:
        db_session.query(Payment).filter(Payment.customer_id.in_(customer_ids)).delete()
        db_session.query(InvoiceItem).filter(
            InvoiceItem.invoice_id.in_(
                db_session.query(Invoice.id).filter(Invoice.customer_id.in_(customer_ids))
            )
        ).delete(synchronize_session=False)
        db_session.query(Invoice).filter(Invoice.customer_id.in_(customer_ids)).delete()
    if sub_ids:
        db_session.query(Subscription).filter(Subscription.id.in_(sub_ids)).delete()
    elif customer_ids:
        db_session.query(Subscription).filter(
            Subscription.customer_id.in_(customer_ids)
        ).delete()
    if plan_ids:
        db_session.query(SubscriptionPlan).filter(
            SubscriptionPlan.id.in_(plan_ids)
        ).delete()
    if product_ids:
        db_session.query(Product).filter(Product.id.in_(product_ids)).delete()
    if customer_ids:
        db_session.query(Customer).filter(Customer.id.in_(customer_ids)).delete()
    if user_ids:
        db_session.query(User).filter(User.id.in_(user_ids)).delete()
    db_session.commit()


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------

class TestDashboardAccessControl:
    def test_unauthenticated_summary_rejected(self, client):
        resp = client.get("/api/dashboard/summary")
        assert resp.status_code == 401

    def test_customer_cannot_access_summary(self, client, db_session, auth_headers):
        cust_user = _make_user(db_session, UserRole.CUSTOMER)
        db_session.commit()
        try:
            resp = client.get(
                "/api/dashboard/summary", headers=auth_headers(cust_user)
            )
            assert resp.status_code == 403
        finally:
            db_session.query(User).filter(User.id == cust_user.id).delete()
            db_session.commit()

    def test_staff_can_access_all_dashboard_endpoints(
        self, client, db_session, auth_headers
    ):
        staff = _make_user(db_session, UserRole.STAFF)
        db_session.commit()
        try:
            for path in (
                "/api/dashboard/summary",
                "/api/dashboard/revenue-trend",
                "/api/dashboard/upcoming-renewals",
                "/api/dashboard/invoice-breakdown",
                "/api/dashboard/payment-outcomes",
            ):
                resp = client.get(path, headers=auth_headers(staff))
                assert resp.status_code == 200, f"{path} returned {resp.status_code}"
        finally:
            db_session.query(User).filter(User.id == staff.id).delete()
            db_session.commit()


# ---------------------------------------------------------------------------
# Summary metrics
# ---------------------------------------------------------------------------

class TestSummaryMetrics:
    def test_summary_structure(self, client, db_session, auth_headers):
        staff = _make_user(db_session, UserRole.STAFF)
        db_session.commit()
        try:
            resp = client.get("/api/dashboard/summary", headers=auth_headers(staff))
            assert resp.status_code == 200, resp.text
            data = resp.json()
            required_keys = {
                "total_active_customers",
                "subscriptions_by_status",
                "mrr",
                "arr",
                "total_invoiced",
                "total_collected",
                "total_outstanding",
                "overdue_invoices_count",
                "overdue_invoices_amount",
                "mrr_assumption",
            }
            assert required_keys.issubset(data.keys())
            # All monetary values should be Decimal-parseable strings
            for key in ("mrr", "arr", "total_invoiced", "total_collected", "total_outstanding", "overdue_invoices_amount"):
                Decimal(data[key])  # Should not raise
        finally:
            db_session.query(User).filter(User.id == staff.id).delete()
            db_session.commit()

    def test_summary_counts_active_subscription(self, client, db_session, auth_headers):
        staff = _make_user(db_session, UserRole.STAFF)
        customer = _make_customer(db_session, staff)
        product = _make_product(db_session)
        plan = _make_plan(db_session, product, price="120.00")
        sub = _make_active_subscription(db_session, customer, plan)
        db_session.commit()

        try:
            resp = client.get("/api/dashboard/summary", headers=auth_headers(staff))
            assert resp.status_code == 200
            data = resp.json()
            # At least 1 active subscription
            assert data["subscriptions_by_status"].get("active", 0) >= 1
            # MRR >= 120.00 (the plan we added)
            assert Decimal(data["mrr"]) >= Decimal("120.00")
        finally:
            _cleanup_all(
                db_session, [staff.id], [customer.id], [plan.id], [product.id], [sub.id]
            )

    def test_summary_overdue_invoice_counted(self, client, db_session, auth_headers):
        staff = _make_user(db_session, UserRole.STAFF)
        customer = _make_customer(db_session, staff)
        inv = _make_overdue_invoice(db_session, customer, amount="150.00")
        db_session.commit()

        try:
            resp = client.get("/api/dashboard/summary", headers=auth_headers(staff))
            assert resp.status_code == 200
            data = resp.json()
            assert data["overdue_invoices_count"] >= 1
            assert Decimal(data["overdue_invoices_amount"]) >= Decimal("150.00")
        finally:
            _cleanup_all(db_session, [staff.id], [customer.id])

    def test_summary_total_collected_includes_payment(
        self, client, db_session, auth_headers
    ):
        staff = _make_user(db_session, UserRole.STAFF)
        customer = _make_customer(db_session, staff)
        inv = _make_open_invoice(db_session, customer, amount="300.00")
        _make_payment(db_session, inv, amount="300.00", status=PaymentStatus.SUCCEEDED)
        db_session.commit()

        try:
            resp = client.get("/api/dashboard/summary", headers=auth_headers(staff))
            assert resp.status_code == 200
            assert Decimal(resp.json()["total_collected"]) >= Decimal("300.00")
        finally:
            _cleanup_all(db_session, [staff.id], [customer.id])


# ---------------------------------------------------------------------------
# Revenue trend
# ---------------------------------------------------------------------------

class TestRevenueTrend:
    def test_revenue_trend_returns_correct_number_of_months(
        self, client, db_session, auth_headers
    ):
        staff = _make_user(db_session, UserRole.STAFF)
        db_session.commit()
        try:
            resp = client.get(
                "/api/dashboard/revenue-trend?months=6", headers=auth_headers(staff)
            )
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 6
            for entry in data:
                assert "year" in entry
                assert "month" in entry
                assert "gross_revenue" in entry
                assert "net_revenue" in entry
        finally:
            db_session.query(User).filter(User.id == staff.id).delete()
            db_session.commit()

    def test_revenue_trend_reflects_current_month_payment(
        self, client, db_session, auth_headers
    ):
        staff = _make_user(db_session, UserRole.STAFF)
        customer = _make_customer(db_session, staff)
        inv = _make_open_invoice(db_session, customer, amount="500.00")
        _make_payment(db_session, inv, amount="500.00", status=PaymentStatus.SUCCEEDED)
        db_session.commit()

        try:
            resp = client.get(
                "/api/dashboard/revenue-trend?months=1", headers=auth_headers(staff)
            )
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 1
            assert Decimal(data[0]["gross_revenue"]) >= Decimal("500.00")
        finally:
            _cleanup_all(db_session, [staff.id], [customer.id])


# ---------------------------------------------------------------------------
# Upcoming renewals
# ---------------------------------------------------------------------------

class TestUpcomingRenewals:
    def test_upcoming_renewals_includes_soon_renewing_subscription(
        self, client, db_session, auth_headers
    ):
        staff = _make_user(db_session, UserRole.STAFF)
        customer = _make_customer(db_session, staff)
        product = _make_product(db_session)
        plan = _make_plan(db_session, product)
        sub = _make_active_subscription(db_session, customer, plan)
        db_session.commit()

        try:
            resp = client.get(
                "/api/dashboard/upcoming-renewals?days=31", headers=auth_headers(staff)
            )
            assert resp.status_code == 200
            ids = [r["subscription_id"] for r in resp.json()]
            assert str(sub.id) in ids
        finally:
            _cleanup_all(
                db_session, [staff.id], [customer.id], [plan.id], [product.id], [sub.id]
            )

    def test_upcoming_renewals_excludes_canceled_subscription(
        self, client, db_session, auth_headers
    ):
        staff = _make_user(db_session, UserRole.STAFF)
        customer = _make_customer(db_session, staff)
        product = _make_product(db_session)
        plan = _make_plan(db_session, product)
        sub = _make_active_subscription(db_session, customer, plan)
        sub.status = SubscriptionStatus.CANCELED
        db_session.commit()

        try:
            resp = client.get(
                "/api/dashboard/upcoming-renewals?days=31", headers=auth_headers(staff)
            )
            assert resp.status_code == 200
            ids = [r["subscription_id"] for r in resp.json()]
            assert str(sub.id) not in ids
        finally:
            _cleanup_all(
                db_session, [staff.id], [customer.id], [plan.id], [product.id], [sub.id]
            )


# ---------------------------------------------------------------------------
# Invoice breakdown
# ---------------------------------------------------------------------------

class TestInvoiceBreakdown:
    def test_invoice_breakdown_structure(self, client, db_session, auth_headers):
        staff = _make_user(db_session, UserRole.STAFF)
        customer = _make_customer(db_session, staff)
        _make_open_invoice(db_session, customer, "100.00")
        _make_overdue_invoice(db_session, customer, "50.00")
        db_session.commit()

        try:
            resp = client.get(
                "/api/dashboard/invoice-breakdown", headers=auth_headers(staff)
            )
            assert resp.status_code == 200
            data = resp.json()
            statuses = {entry["status"] for entry in data}
            assert "open" in statuses or "overdue" in statuses
            for entry in data:
                assert "count" in entry
                assert "total_amount" in entry
                assert "amount_due" in entry
        finally:
            _cleanup_all(db_session, [staff.id], [customer.id])


# ---------------------------------------------------------------------------
# Payment outcomes
# ---------------------------------------------------------------------------

class TestPaymentOutcomes:
    def test_payment_outcomes_structure(self, client, db_session, auth_headers):
        staff = _make_user(db_session, UserRole.STAFF)
        customer = _make_customer(db_session, staff)
        inv = _make_open_invoice(db_session, customer, "200.00")
        _make_payment(db_session, inv, "200.00", PaymentStatus.SUCCEEDED)
        _make_payment(db_session, inv, "50.00", PaymentStatus.FAILED)
        db_session.commit()

        try:
            resp = client.get(
                "/api/dashboard/payment-outcomes", headers=auth_headers(staff)
            )
            assert resp.status_code == 200
            data = resp.json()
            statuses = {entry["status"] for entry in data}
            assert "succeeded" in statuses
            assert "failed" in statuses
            for entry in data:
                assert "count" in entry
                assert "total_amount" in entry
        finally:
            _cleanup_all(db_session, [staff.id], [customer.id])
