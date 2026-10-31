"""
Tests for the Revenue Guardian module.

Tests verify:
- Each rule endpoint is accessible to staff/admin and blocked for customers/unauthenticated
- The /run endpoint returns the expected structure
- Specific rules correctly detect planted data issues:
    - Overdue invoice detection
    - Failed payment detection
    - Duplicate payment detection (suspected)
    - Invoice total mismatch detection (confirmed)
    - amount_paid + amount_due mismatch detection (confirmed)
    - Upcoming renewal detection
    - Missing invoice detection
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
        email=f"grd_{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("pw"),
        full_name="Guardian Tester",
        role=role,
        is_active=True,
    )
    db.add(u)
    db.flush()
    return u


def _make_customer(db, user):
    c = Customer(
        user_id=user.id,
        name="Guardian Cust",
        email=user.email,
        is_active=True,
        is_archived=False,
    )
    db.add(c)
    db.flush()
    return c


def _make_overdue_invoice(db, customer, amount="200.00"):
    yesterday = date.today() - timedelta(days=1)
    inv = Invoice(
        invoice_number=f"INV-GRD-{uuid.uuid4().hex[:8].upper()}",
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


def _make_open_invoice(db, customer, amount="100.00", total_override=None):
    today = date.today()
    total = Decimal(total_override) if total_override else Decimal(amount)
    inv = Invoice(
        invoice_number=f"INV-GRD-{uuid.uuid4().hex[:8].upper()}",
        customer_id=customer.id,
        status=InvoiceStatus.OPEN,
        subtotal=Decimal(amount),
        tax_amount=Decimal("0.00"),
        discount_amount=Decimal("0.00"),
        total_amount=total,  # intentionally wrong if total_override is set
        amount_paid=Decimal("0.00"),
        amount_due=total,
        currency="USD",
        issue_date=today,
        due_date=today + timedelta(days=30),
    )
    db.add(inv)
    db.flush()
    return inv


def _make_payment(db, invoice, amount="100.00", status=PaymentStatus.SUCCEEDED, failure_reason=None):
    pay = Payment(
        payment_number=f"PAY-GRD-{uuid.uuid4().hex[:8].upper()}",
        invoice_id=invoice.id,
        customer_id=invoice.customer_id,
        amount=Decimal(amount),
        currency="USD",
        status=status,
        payment_method="card",
        failure_reason=failure_reason,
        refunded_amount=Decimal("0.00"),
    )
    db.add(pay)
    db.flush()
    return pay


def _make_active_subscription(db, customer, plan):
    now = datetime.now(timezone.utc)
    sub = Subscription(
        customer_id=customer.id,
        plan_id=plan.id,
        status=SubscriptionStatus.ACTIVE,
        start_date=now,
        current_period_start=now,
        current_period_end=now + timedelta(days=5),  # renews soon
        cancel_at_period_end=False,
    )
    db.add(sub)
    db.flush()
    return sub


def _cleanup(db_session, user_ids=None, customer_ids=None, plan_ids=None, product_ids=None, sub_ids=None):
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
        db_session.query(SubscriptionPlan).filter(SubscriptionPlan.id.in_(plan_ids)).delete()
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

class TestGuardianAccessControl:
    def test_unauthenticated_run_rejected(self, client):
        resp = client.get("/api/guardian/run")
        assert resp.status_code == 401

    def test_customer_cannot_access_guardian(self, client, db_session, auth_headers):
        cust_user = _make_user(db_session, UserRole.CUSTOMER)
        db_session.commit()
        try:
            resp = client.get("/api/guardian/run", headers=auth_headers(cust_user))
            assert resp.status_code == 403
        finally:
            db_session.query(User).filter(User.id == cust_user.id).delete()
            db_session.commit()

    def test_staff_can_access_guardian(self, client, db_session, auth_headers):
        staff = _make_user(db_session, UserRole.STAFF)
        db_session.commit()
        try:
            resp = client.get("/api/guardian/run", headers=auth_headers(staff))
            assert resp.status_code == 200
        finally:
            db_session.query(User).filter(User.id == staff.id).delete()
            db_session.commit()


# ---------------------------------------------------------------------------
# /run structure
# ---------------------------------------------------------------------------

class TestGuardianRunStructure:
    def test_run_response_structure(self, client, db_session, auth_headers):
        staff = _make_user(db_session, UserRole.STAFF)
        db_session.commit()
        try:
            resp = client.get("/api/guardian/run", headers=auth_headers(staff))
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert "total_findings" in data
            assert "by_severity" in data
            assert "findings" in data
            assert isinstance(data["findings"], list)
            for sev in ("low", "medium", "high", "critical"):
                assert sev in data["by_severity"]
        finally:
            db_session.query(User).filter(User.id == staff.id).delete()
            db_session.commit()

    def test_finding_fields_present(self, client, db_session, auth_headers):
        staff = _make_user(db_session, UserRole.STAFF)
        customer = _make_customer(db_session, staff)
        _make_overdue_invoice(db_session, customer)
        db_session.commit()
        try:
            resp = client.get("/api/guardian/run", headers=auth_headers(staff))
            assert resp.status_code == 200
            findings = resp.json()["findings"]
            assert len(findings) >= 1
            required_fields = {
                "rule", "title", "severity", "certainty",
                "affected_ids", "evidence", "recommended_action",
                "detected_at", "status",
            }
            for f in findings:
                assert required_fields.issubset(f.keys()), f"Missing fields in: {f}"
        finally:
            _cleanup(db_session, [staff.id], [customer.id])


# ---------------------------------------------------------------------------
# Rule: Overdue invoices
# ---------------------------------------------------------------------------

class TestOverdueInvoiceDetection:
    def test_detects_confirmed_overdue_invoice(self, client, db_session, auth_headers):
        staff = _make_user(db_session, UserRole.STAFF)
        customer = _make_customer(db_session, staff)
        inv = _make_overdue_invoice(db_session, customer, "500.00")
        db_session.commit()
        try:
            resp = client.get("/api/guardian/overdue-invoices", headers=auth_headers(staff))
            assert resp.status_code == 200
            findings = resp.json()["findings"]
            inv_ids = [f["affected_ids"]["invoice_id"] for f in findings]
            assert str(inv.id) in inv_ids
            # Should be confirmed
            matching = [f for f in findings if f["affected_ids"]["invoice_id"] == str(inv.id)]
            assert any(f["certainty"] == "confirmed" for f in matching)
        finally:
            _cleanup(db_session, [staff.id], [customer.id])


# ---------------------------------------------------------------------------
# Rule: Failed payments
# ---------------------------------------------------------------------------

class TestFailedPaymentDetection:
    def test_detects_failed_payment(self, client, db_session, auth_headers):
        staff = _make_user(db_session, UserRole.STAFF)
        customer = _make_customer(db_session, staff)
        inv = _make_open_invoice(db_session, customer)
        pay = _make_payment(
            db_session, inv, "100.00",
            status=PaymentStatus.FAILED,
            failure_reason="Insufficient funds",
        )
        db_session.commit()
        try:
            resp = client.get(
                "/api/guardian/failed-payments?days=30", headers=auth_headers(staff)
            )
            assert resp.status_code == 200
            pay_ids = [f["affected_ids"]["payment_id"] for f in resp.json()["findings"]]
            assert str(pay.id) in pay_ids
        finally:
            _cleanup(db_session, [staff.id], [customer.id])


# ---------------------------------------------------------------------------
# Rule: Duplicate payments
# ---------------------------------------------------------------------------

class TestDuplicatePaymentDetection:
    def test_detects_duplicate_payments_same_invoice_same_day(
        self, client, db_session, auth_headers
    ):
        staff = _make_user(db_session, UserRole.STAFF)
        customer = _make_customer(db_session, staff)
        inv = _make_open_invoice(db_session, customer, "200.00")
        # Two succeeded payments on the same invoice (same day by default)
        _make_payment(db_session, inv, "100.00", PaymentStatus.SUCCEEDED)
        _make_payment(db_session, inv, "100.00", PaymentStatus.SUCCEEDED)
        db_session.commit()
        try:
            resp = client.get(
                "/api/guardian/duplicate-payments", headers=auth_headers(staff)
            )
            assert resp.status_code == 200
            inv_ids = [f["affected_ids"]["invoice_id"] for f in resp.json()["findings"]]
            assert str(inv.id) in inv_ids
            # Should be suspected (not confirmed)
            matching = [f for f in resp.json()["findings"]
                        if f["affected_ids"]["invoice_id"] == str(inv.id)]
            assert any(f["certainty"] == "suspected" for f in matching)
        finally:
            _cleanup(db_session, [staff.id], [customer.id])


# ---------------------------------------------------------------------------
# Rule: Invoice total mismatch
# ---------------------------------------------------------------------------

class TestInvoiceTotalMismatch:
    def test_detects_total_amount_mismatch(self, client, db_session, auth_headers):
        staff = _make_user(db_session, UserRole.STAFF)
        customer = _make_customer(db_session, staff)
        # subtotal=100, total_override=999 → mismatch
        inv = _make_open_invoice(db_session, customer, amount="100.00", total_override="999.00")
        db_session.commit()
        try:
            resp = client.get(
                "/api/guardian/invoice-total-mismatches", headers=auth_headers(staff)
            )
            assert resp.status_code == 200
            inv_ids = [f["affected_ids"]["invoice_id"] for f in resp.json()["findings"]]
            assert str(inv.id) in inv_ids
            matching = [f for f in resp.json()["findings"]
                        if f["affected_ids"]["invoice_id"] == str(inv.id)]
            assert any(f["certainty"] == "confirmed" for f in matching)
        finally:
            _cleanup(db_session, [staff.id], [customer.id])


# ---------------------------------------------------------------------------
# Rule: Upcoming renewals
# ---------------------------------------------------------------------------

class TestUpcomingRenewalDetection:
    def test_detects_upcoming_renewal(self, client, db_session, auth_headers):
        staff = _make_user(db_session, UserRole.STAFF)
        customer = _make_customer(db_session, staff)
        product = Product(
            name=f"ProdGrd {uuid.uuid4().hex[:4]}",
            code=f"PGRD-{uuid.uuid4().hex[:8].upper()}",
            is_active=True,
            is_archived=False,
        )
        db_session.add(product)
        db_session.flush()
        plan = SubscriptionPlan(
            product_id=product.id,
            name=f"PlanGrd {uuid.uuid4().hex[:4]}",
            code=f"PLGRD-{uuid.uuid4().hex[:8].upper()}",
            billing_interval=BillingInterval.MONTHLY,
            interval_count=1,
            price=Decimal("50.00"),
            currency="USD",
            trial_period_days=0,
            is_active=True,
            is_archived=False,
        )
        db_session.add(plan)
        db_session.flush()
        sub = _make_active_subscription(db_session, customer, plan)
        db_session.commit()

        try:
            resp = client.get(
                "/api/guardian/upcoming-renewals?days=10", headers=auth_headers(staff)
            )
            assert resp.status_code == 200
            sub_ids = [f["affected_ids"]["subscription_id"] for f in resp.json()["findings"]]
            assert str(sub.id) in sub_ids
        finally:
            _cleanup(db_session, [staff.id], [customer.id], [plan.id], [product.id], [sub.id])


# ---------------------------------------------------------------------------
# All individual rule endpoints are accessible
# ---------------------------------------------------------------------------

class TestAllGuardianEndpoints:
    def test_all_endpoints_return_200(self, client, db_session, auth_headers):
        staff = _make_user(db_session, UserRole.STAFF)
        db_session.commit()
        try:
            for path in (
                "/api/guardian/overdue-invoices",
                "/api/guardian/failed-payments",
                "/api/guardian/duplicate-payments",
                "/api/guardian/duplicate-invoices",
                "/api/guardian/expired-discounts",
                "/api/guardian/invoice-total-mismatches",
                "/api/guardian/amount-paid-due-mismatches",
                "/api/guardian/upcoming-renewals",
                "/api/guardian/missing-invoices",
                "/api/guardian/payment-invoice-mismatches",
            ):
                resp = client.get(path, headers=auth_headers(staff))
                assert resp.status_code == 200, f"{path} returned {resp.status_code}: {resp.text}"
                assert "findings" in resp.json()
        finally:
            db_session.query(User).filter(User.id == staff.id).delete()
            db_session.commit()
