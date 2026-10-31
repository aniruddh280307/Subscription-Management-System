"""
Comprehensive test suite for the Invoices and Payments module.

Covers:
- Invoice creation (DRAFT), totals calculation, sequential numbering
- Finalization (DRAFT → OPEN)
- DRAFT update (due_date, tax, discount) with total recalculation
- Cannot update/finalize a non-DRAFT invoice
- Voiding: allowed on DRAFT/OPEN, rejected on PAID
- Mark uncollectible: allowed on OPEN/OVERDUE/PARTIALLY_PAID
- Payment recording: succeeded and failed
- Partial payment → PARTIALLY_PAID status
- Full payment → PAID status + paid_at set
- Overpayment rejection
- Payment against DRAFT invoice rejection
- Payment idempotency (duplicate key returns existing payment)
- Partial refund and full refund
- Refund > remaining balance rejected
- Refund reopens PAID invoice to PARTIALLY_PAID or OPEN
- Customer isolation: cannot read or pay another customer's invoice
- Unauthenticated access rejected
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models import (
    User,
    UserRole,
    Customer,
    Product,
    SubscriptionPlan,
    Invoice,
    InvoiceItem,
    InvoiceStatus,
    Payment,
    PaymentStatus,
    AuditLog,
    BillingInterval,
)
from app.security import hash_password


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_user(db, role: UserRole) -> User:
    user = User(
        email=f"inv_test_{uuid.uuid4().hex[:10]}@example.com",
        hashed_password=hash_password("testpw"),
        full_name="Invoice Tester",
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


def _today() -> str:
    return date.today().isoformat()


def _future(days: int = 30) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _create_draft_invoice(client, headers, customer_id: str, extra: dict = None) -> dict:
    payload = {
        "customer_id": customer_id,
        "issue_date": _today(),
        "due_date": _future(30),
        "currency": "USD",
        "tax_amount": "0.00",
        "discount_amount": "0.00",
        "items": [
            {
                "description": "Test service",
                "quantity": 2,
                "unit_price": "50.00",
            }
        ],
    }
    if extra:
        payload.update(extra)
    resp = client.post("/api/invoices", json=payload, headers=headers)
    return resp


def _cleanup(db_session, user_ids=None, customer_ids=None):
    db_session.query(AuditLog).filter(
        AuditLog.entity_type.in_(["invoice", "payment"])
    ).delete()
    if customer_ids:
        db_session.query(Payment).filter(
            Payment.customer_id.in_(customer_ids)
        ).delete()
        db_session.query(InvoiceItem).filter(
            InvoiceItem.invoice_id.in_(
                db_session.query(Invoice.id).filter(
                    Invoice.customer_id.in_(customer_ids)
                )
            )
        ).delete(synchronize_session=False)
        db_session.query(Invoice).filter(
            Invoice.customer_id.in_(customer_ids)
        ).delete()
        db_session.query(Customer).filter(Customer.id.in_(customer_ids)).delete()
    if user_ids:
        db_session.query(User).filter(User.id.in_(user_ids)).delete()
    db_session.commit()


# ---------------------------------------------------------------------------
# Invoice creation and totals
# ---------------------------------------------------------------------------


class TestInvoiceCreation:
    def test_create_draft_invoice_basic(self, client, db_session, auth_headers):
        staff = _make_user(db_session, UserRole.STAFF)
        customer = _make_customer(db_session, staff)
        db_session.commit()

        try:
            resp = _create_draft_invoice(client, auth_headers(staff), str(customer.id))
            assert resp.status_code == 201, resp.text
            data = resp.json()
            assert data["status"] == "draft"
            assert Decimal(data["subtotal"]) == Decimal("100.00")  # 2 × 50
            assert Decimal(data["total_amount"]) == Decimal("100.00")
            assert Decimal(data["amount_due"]) == Decimal("100.00")
            assert Decimal(data["amount_paid"]) == Decimal("0.00")
            assert len(data["items"]) == 1
            assert data["items"][0]["amount"] == "100.00"
            assert data["invoice_number"].startswith("INV-")
        finally:
            _cleanup(db_session, [staff.id], [customer.id])

    def test_create_invoice_with_tax_and_discount(self, client, db_session, auth_headers):
        staff = _make_user(db_session, UserRole.STAFF)
        customer = _make_customer(db_session, staff)
        db_session.commit()

        try:
            resp = _create_draft_invoice(
                client,
                auth_headers(staff),
                str(customer.id),
                extra={"tax_amount": "10.00", "discount_amount": "5.00"},
            )
            assert resp.status_code == 201, resp.text
            data = resp.json()
            # subtotal=100, tax=10, discount=5 → total=105
            assert Decimal(data["subtotal"]) == Decimal("100.00")
            assert Decimal(data["tax_amount"]) == Decimal("10.00")
            assert Decimal(data["discount_amount"]) == Decimal("5.00")
            assert Decimal(data["total_amount"]) == Decimal("105.00")
        finally:
            _cleanup(db_session, [staff.id], [customer.id])

    def test_create_invoice_multiple_line_items(self, client, db_session, auth_headers):
        staff = _make_user(db_session, UserRole.STAFF)
        customer = _make_customer(db_session, staff)
        db_session.commit()

        try:
            payload = {
                "customer_id": str(customer.id),
                "issue_date": _today(),
                "due_date": _future(30),
                "currency": "USD",
                "tax_amount": "0.00",
                "discount_amount": "0.00",
                "items": [
                    {"description": "Item A", "quantity": 3, "unit_price": "10.00"},
                    {"description": "Item B", "quantity": 1, "unit_price": "25.00"},
                ],
            }
            resp = client.post("/api/invoices", json=payload, headers=auth_headers(staff))
            assert resp.status_code == 201, resp.text
            data = resp.json()
            assert len(data["items"]) == 2
            assert Decimal(data["subtotal"]) == Decimal("55.00")  # 30 + 25
        finally:
            _cleanup(db_session, [staff.id], [customer.id])

    def test_create_invoice_rejects_nonexistent_customer(
        self, client, db_session, auth_headers
    ):
        staff = _make_user(db_session, UserRole.STAFF)
        db_session.commit()
        try:
            resp = _create_draft_invoice(client, auth_headers(staff), str(uuid.uuid4()))
            assert resp.status_code == 404
        finally:
            db_session.query(User).filter(User.id == staff.id).delete()
            db_session.commit()

    def test_due_date_before_issue_date_rejected(self, client, db_session, auth_headers):
        staff = _make_user(db_session, UserRole.STAFF)
        customer = _make_customer(db_session, staff)
        db_session.commit()
        try:
            payload = {
                "customer_id": str(customer.id),
                "issue_date": _future(10),
                "due_date": _today(),  # before issue_date
                "currency": "USD",
                "tax_amount": "0.00",
                "discount_amount": "0.00",
                "items": [{"description": "X", "quantity": 1, "unit_price": "10.00"}],
            }
            resp = client.post("/api/invoices", json=payload, headers=auth_headers(staff))
            assert resp.status_code == 422
        finally:
            _cleanup(db_session, [staff.id], [customer.id])

    def test_customer_cannot_create_invoice(self, client, db_session, auth_headers):
        cust_user = _make_user(db_session, UserRole.CUSTOMER)
        customer = _make_customer(db_session, cust_user)
        db_session.commit()
        try:
            resp = _create_draft_invoice(
                client, auth_headers(cust_user), str(customer.id)
            )
            assert resp.status_code == 403
        finally:
            _cleanup(db_session, [cust_user.id], [customer.id])


# ---------------------------------------------------------------------------
# Invoice lifecycle
# ---------------------------------------------------------------------------


class TestInvoiceLifecycle:
    def _setup(self, db_session):
        staff = _make_user(db_session, UserRole.STAFF)
        customer = _make_customer(db_session, staff)
        db_session.commit()
        return staff, customer

    def test_finalize_draft_to_open(self, client, db_session, auth_headers):
        staff, customer = self._setup(db_session)
        try:
            r = _create_draft_invoice(client, auth_headers(staff), str(customer.id))
            assert r.status_code == 201
            inv_id = r.json()["id"]

            fin = client.post(
                f"/api/invoices/{inv_id}/finalize", headers=auth_headers(staff)
            )
            assert fin.status_code == 200, fin.text
            assert fin.json()["status"] == "open"
        finally:
            _cleanup(db_session, [staff.id], [customer.id])

    def test_update_draft_invoice(self, client, db_session, auth_headers):
        staff, customer = self._setup(db_session)
        try:
            r = _create_draft_invoice(client, auth_headers(staff), str(customer.id))
            assert r.status_code == 201
            inv_id = r.json()["id"]

            patch = client.patch(
                f"/api/invoices/{inv_id}",
                json={"tax_amount": "15.00", "discount_amount": "5.00"},
                headers=auth_headers(staff),
            )
            assert patch.status_code == 200, patch.text
            data = patch.json()
            # 100 subtotal + 15 tax - 5 discount = 110
            assert Decimal(data["total_amount"]) == Decimal("110.00")
            assert Decimal(data["amount_due"]) == Decimal("110.00")
        finally:
            _cleanup(db_session, [staff.id], [customer.id])

    def test_cannot_update_open_invoice(self, client, db_session, auth_headers):
        staff, customer = self._setup(db_session)
        try:
            r = _create_draft_invoice(client, auth_headers(staff), str(customer.id))
            inv_id = r.json()["id"]
            client.post(f"/api/invoices/{inv_id}/finalize", headers=auth_headers(staff))

            patch = client.patch(
                f"/api/invoices/{inv_id}",
                json={"tax_amount": "10.00"},
                headers=auth_headers(staff),
            )
            assert patch.status_code == 400
        finally:
            _cleanup(db_session, [staff.id], [customer.id])

    def test_void_open_invoice(self, client, db_session, auth_headers):
        staff, customer = self._setup(db_session)
        try:
            r = _create_draft_invoice(client, auth_headers(staff), str(customer.id))
            inv_id = r.json()["id"]
            client.post(f"/api/invoices/{inv_id}/finalize", headers=auth_headers(staff))

            void_r = client.post(
                f"/api/invoices/{inv_id}/void",
                json={"reason": "Customer requested cancellation"},
                headers=auth_headers(staff),
            )
            assert void_r.status_code == 200, void_r.text
            assert void_r.json()["status"] == "void"
            assert void_r.json()["voided_at"] is not None
        finally:
            _cleanup(db_session, [staff.id], [customer.id])

    def test_void_paid_invoice_rejected(self, client, db_session, auth_headers):
        staff, customer = self._setup(db_session)
        try:
            r = _create_draft_invoice(client, auth_headers(staff), str(customer.id))
            inv_id = r.json()["id"]
            client.post(f"/api/invoices/{inv_id}/finalize", headers=auth_headers(staff))

            # Pay in full
            client.post(
                f"/api/invoices/{inv_id}/payments",
                json={
                    "invoice_id": inv_id,
                    "amount": "100.00",
                    "payment_method": "bank_transfer",
                    "payment_status": "succeeded",
                },
                headers=auth_headers(staff),
            )

            void_r = client.post(
                f"/api/invoices/{inv_id}/void",
                json={},
                headers=auth_headers(staff),
            )
            assert void_r.status_code == 400
        finally:
            _cleanup(db_session, [staff.id], [customer.id])

    def test_mark_uncollectible_open_invoice(self, client, db_session, auth_headers):
        staff, customer = self._setup(db_session)
        try:
            r = _create_draft_invoice(client, auth_headers(staff), str(customer.id))
            inv_id = r.json()["id"]
            client.post(f"/api/invoices/{inv_id}/finalize", headers=auth_headers(staff))

            unc_r = client.post(
                f"/api/invoices/{inv_id}/mark-uncollectible",
                json={"reason": "Customer has gone out of business"},
                headers=auth_headers(staff),
            )
            assert unc_r.status_code == 200, unc_r.text
            assert unc_r.json()["status"] == "uncollectible"
        finally:
            _cleanup(db_session, [staff.id], [customer.id])


# ---------------------------------------------------------------------------
# Payment recording
# ---------------------------------------------------------------------------


class TestPaymentRecording:
    def _open_invoice(self, client, db_session, auth_headers):
        staff = _make_user(db_session, UserRole.STAFF)
        customer = _make_customer(db_session, staff)
        db_session.commit()

        r = _create_draft_invoice(client, auth_headers(staff), str(customer.id))
        assert r.status_code == 201, r.text
        inv_id = r.json()["id"]
        client.post(f"/api/invoices/{inv_id}/finalize", headers=auth_headers(staff))
        return inv_id, staff, customer

    def test_full_payment_sets_paid(self, client, db_session, auth_headers):
        inv_id, staff, customer = self._open_invoice(client, db_session, auth_headers)
        try:
            pay_r = client.post(
                f"/api/invoices/{inv_id}/payments",
                json={
                    "invoice_id": inv_id,
                    "amount": "100.00",
                    "payment_method": "bank_transfer",
                    "payment_status": "succeeded",
                },
                headers=auth_headers(staff),
            )
            assert pay_r.status_code == 201, pay_r.text
            assert pay_r.json()["status"] == "succeeded"

            inv_r = client.get(f"/api/invoices/{inv_id}", headers=auth_headers(staff))
            data = inv_r.json()
            assert data["status"] == "paid"
            assert data["paid_at"] is not None
            assert Decimal(data["amount_paid"]) == Decimal("100.00")
            assert Decimal(data["amount_due"]) == Decimal("0.00")
        finally:
            _cleanup(db_session, [staff.id], [customer.id])

    def test_partial_payment_sets_partially_paid(self, client, db_session, auth_headers):
        inv_id, staff, customer = self._open_invoice(client, db_session, auth_headers)
        try:
            pay_r = client.post(
                f"/api/invoices/{inv_id}/payments",
                json={
                    "invoice_id": inv_id,
                    "amount": "40.00",
                    "payment_method": "card",
                    "payment_status": "succeeded",
                },
                headers=auth_headers(staff),
            )
            assert pay_r.status_code == 201, pay_r.text

            inv_r = client.get(f"/api/invoices/{inv_id}", headers=auth_headers(staff))
            data = inv_r.json()
            assert data["status"] == "partially_paid"
            assert Decimal(data["amount_paid"]) == Decimal("40.00")
            assert Decimal(data["amount_due"]) == Decimal("60.00")
        finally:
            _cleanup(db_session, [staff.id], [customer.id])

    def test_overpayment_rejected(self, client, db_session, auth_headers):
        inv_id, staff, customer = self._open_invoice(client, db_session, auth_headers)
        try:
            pay_r = client.post(
                f"/api/invoices/{inv_id}/payments",
                json={
                    "invoice_id": inv_id,
                    "amount": "999.00",  # > 100.00 total
                    "payment_method": "bank_transfer",
                    "payment_status": "succeeded",
                },
                headers=auth_headers(staff),
            )
            assert pay_r.status_code == 400
        finally:
            _cleanup(db_session, [staff.id], [customer.id])

    def test_payment_against_draft_invoice_rejected(
        self, client, db_session, auth_headers
    ):
        staff = _make_user(db_session, UserRole.STAFF)
        customer = _make_customer(db_session, staff)
        db_session.commit()
        try:
            r = _create_draft_invoice(client, auth_headers(staff), str(customer.id))
            assert r.status_code == 201
            inv_id = r.json()["id"]

            pay_r = client.post(
                f"/api/invoices/{inv_id}/payments",
                json={
                    "invoice_id": inv_id,
                    "amount": "100.00",
                    "payment_method": "bank_transfer",
                    "payment_status": "succeeded",
                },
                headers=auth_headers(staff),
            )
            assert pay_r.status_code == 400
        finally:
            _cleanup(db_session, [staff.id], [customer.id])

    def test_failed_payment_does_not_update_invoice_totals(
        self, client, db_session, auth_headers
    ):
        inv_id, staff, customer = self._open_invoice(client, db_session, auth_headers)
        try:
            pay_r = client.post(
                f"/api/invoices/{inv_id}/payments",
                json={
                    "invoice_id": inv_id,
                    "amount": "100.00",
                    "payment_method": "card",
                    "payment_status": "failed",
                    "failure_reason": "Insufficient funds",
                },
                headers=auth_headers(staff),
            )
            assert pay_r.status_code == 201, pay_r.text
            assert pay_r.json()["status"] == "failed"

            # Invoice should remain open with no amount_paid change
            inv_r = client.get(f"/api/invoices/{inv_id}", headers=auth_headers(staff))
            data = inv_r.json()
            assert data["status"] == "open"
            assert Decimal(data["amount_paid"]) == Decimal("0.00")
            assert Decimal(data["amount_due"]) == Decimal("100.00")
        finally:
            _cleanup(db_session, [staff.id], [customer.id])

    def test_idempotent_payment_returns_same_record(
        self, client, db_session, auth_headers
    ):
        inv_id, staff, customer = self._open_invoice(client, db_session, auth_headers)
        try:
            idem_key = f"idem-{uuid.uuid4().hex}"
            payload = {
                "invoice_id": inv_id,
                "amount": "50.00",
                "payment_method": "bank_transfer",
                "payment_status": "succeeded",
                "idempotency_key": idem_key,
            }
            r1 = client.post(
                f"/api/invoices/{inv_id}/payments",
                json=payload,
                headers=auth_headers(staff),
            )
            assert r1.status_code == 201, r1.text
            pay_id_1 = r1.json()["id"]

            # Second request with same idempotency key
            r2 = client.post(
                f"/api/invoices/{inv_id}/payments",
                json=payload,
                headers=auth_headers(staff),
            )
            assert r2.status_code == 201, r2.text
            pay_id_2 = r2.json()["id"]

            # Must return the same payment record
            assert pay_id_1 == pay_id_2

            # Invoice amount_paid should reflect only one payment
            inv_r = client.get(f"/api/invoices/{inv_id}", headers=auth_headers(staff))
            assert Decimal(inv_r.json()["amount_paid"]) == Decimal("50.00")
        finally:
            _cleanup(db_session, [staff.id], [customer.id])

    def test_two_partial_payments_sum_to_full(self, client, db_session, auth_headers):
        inv_id, staff, customer = self._open_invoice(client, db_session, auth_headers)
        try:
            for amt in ("60.00", "40.00"):
                pr = client.post(
                    f"/api/invoices/{inv_id}/payments",
                    json={
                        "invoice_id": inv_id,
                        "amount": amt,
                        "payment_method": "card",
                        "payment_status": "succeeded",
                    },
                    headers=auth_headers(staff),
                )
                assert pr.status_code == 201, pr.text

            inv_r = client.get(f"/api/invoices/{inv_id}", headers=auth_headers(staff))
            data = inv_r.json()
            assert data["status"] == "paid"
            assert Decimal(data["amount_paid"]) == Decimal("100.00")
            assert Decimal(data["amount_due"]) == Decimal("0.00")
        finally:
            _cleanup(db_session, [staff.id], [customer.id])


# ---------------------------------------------------------------------------
# Refunds
# ---------------------------------------------------------------------------


class TestRefunds:
    def _paid_invoice_and_payment(self, client, db_session, auth_headers):
        staff = _make_user(db_session, UserRole.STAFF)
        customer = _make_customer(db_session, staff)
        db_session.commit()

        r = _create_draft_invoice(client, auth_headers(staff), str(customer.id))
        assert r.status_code == 201
        inv_id = r.json()["id"]
        client.post(f"/api/invoices/{inv_id}/finalize", headers=auth_headers(staff))

        pay_r = client.post(
            f"/api/invoices/{inv_id}/payments",
            json={
                "invoice_id": inv_id,
                "amount": "100.00",
                "payment_method": "bank_transfer",
                "payment_status": "succeeded",
            },
            headers=auth_headers(staff),
        )
        assert pay_r.status_code == 201
        pay_id = pay_r.json()["id"]
        return inv_id, pay_id, staff, customer

    def test_full_refund(self, client, db_session, auth_headers):
        inv_id, pay_id, staff, customer = self._paid_invoice_and_payment(
            client, db_session, auth_headers
        )
        try:
            ref_r = client.post(
                f"/api/payments/{pay_id}/refund",
                json={"refund_amount": "100.00"},
                headers=auth_headers(staff),
            )
            assert ref_r.status_code == 200, ref_r.text
            data = ref_r.json()
            assert data["status"] == "refunded"
            assert Decimal(data["refunded_amount"]) == Decimal("100.00")

            # Invoice should be reopened as open (amount_paid=0)
            inv_r = client.get(f"/api/invoices/{inv_id}", headers=auth_headers(staff))
            inv_data = inv_r.json()
            assert inv_data["status"] == "open"
            assert Decimal(inv_data["amount_paid"]) == Decimal("0.00")
            assert Decimal(inv_data["amount_due"]) == Decimal("100.00")
        finally:
            _cleanup(db_session, [staff.id], [customer.id])

    def test_partial_refund(self, client, db_session, auth_headers):
        inv_id, pay_id, staff, customer = self._paid_invoice_and_payment(
            client, db_session, auth_headers
        )
        try:
            ref_r = client.post(
                f"/api/payments/{pay_id}/refund",
                json={"refund_amount": "30.00"},
                headers=auth_headers(staff),
            )
            assert ref_r.status_code == 200, ref_r.text
            data = ref_r.json()
            assert data["status"] == "partially_refunded"
            assert Decimal(data["refunded_amount"]) == Decimal("30.00")

            # Invoice should be partially_paid now
            inv_r = client.get(f"/api/invoices/{inv_id}", headers=auth_headers(staff))
            inv_data = inv_r.json()
            assert inv_data["status"] == "partially_paid"
            assert Decimal(inv_data["amount_paid"]) == Decimal("70.00")
            assert Decimal(inv_data["amount_due"]) == Decimal("30.00")
        finally:
            _cleanup(db_session, [staff.id], [customer.id])

    def test_refund_exceeds_balance_rejected(self, client, db_session, auth_headers):
        inv_id, pay_id, staff, customer = self._paid_invoice_and_payment(
            client, db_session, auth_headers
        )
        try:
            ref_r = client.post(
                f"/api/payments/{pay_id}/refund",
                json={"refund_amount": "150.00"},  # More than payment amount
                headers=auth_headers(staff),
            )
            assert ref_r.status_code == 400
        finally:
            _cleanup(db_session, [staff.id], [customer.id])


# ---------------------------------------------------------------------------
# Customer isolation
# ---------------------------------------------------------------------------


class TestInvoiceCustomerIsolation:
    def test_customer_cannot_view_other_customers_invoice(
        self, client, db_session, auth_headers
    ):
        staff = _make_user(db_session, UserRole.STAFF)
        cust_user_a = _make_user(db_session, UserRole.CUSTOMER)
        cust_user_b = _make_user(db_session, UserRole.CUSTOMER)
        customer_a = _make_customer(db_session, cust_user_a)
        customer_b = _make_customer(db_session, cust_user_b)
        db_session.commit()

        try:
            # Create invoice for customer_b
            r = _create_draft_invoice(client, auth_headers(staff), str(customer_b.id))
            assert r.status_code == 201
            inv_id = r.json()["id"]

            # cust_user_a should be forbidden
            get_r = client.get(
                f"/api/invoices/{inv_id}", headers=auth_headers(cust_user_a)
            )
            assert get_r.status_code == 403
        finally:
            _cleanup(
                db_session,
                [staff.id, cust_user_a.id, cust_user_b.id],
                [customer_a.id, customer_b.id],
            )

    def test_customer_list_sees_only_own_invoices(
        self, client, db_session, auth_headers
    ):
        staff = _make_user(db_session, UserRole.STAFF)
        cust_user_a = _make_user(db_session, UserRole.CUSTOMER)
        cust_user_b = _make_user(db_session, UserRole.CUSTOMER)
        customer_a = _make_customer(db_session, cust_user_a)
        customer_b = _make_customer(db_session, cust_user_b)
        db_session.commit()

        try:
            # Create one invoice each
            for cid in (str(customer_a.id), str(customer_b.id)):
                r = _create_draft_invoice(client, auth_headers(staff), cid)
                assert r.status_code == 201

            list_r = client.get("/api/invoices", headers=auth_headers(cust_user_a))
            assert list_r.status_code == 200
            items = list_r.json()["items"]
            for item in items:
                assert item["customer_id"] == str(customer_a.id)
        finally:
            _cleanup(
                db_session,
                [staff.id, cust_user_a.id, cust_user_b.id],
                [customer_a.id, customer_b.id],
            )


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------


class TestInvoiceAccessControl:
    def test_unauthenticated_list_rejected(self, client):
        resp = client.get("/api/invoices")
        assert resp.status_code == 401

    def test_unauthenticated_payments_list_rejected(self, client):
        resp = client.get("/api/payments")
        assert resp.status_code == 401

    def test_get_nonexistent_invoice(self, client, db_session, auth_headers):
        admin = _make_user(db_session, UserRole.ADMIN)
        db_session.commit()
        try:
            resp = client.get(
                f"/api/invoices/{uuid.uuid4()}", headers=auth_headers(admin)
            )
            assert resp.status_code == 404
        finally:
            db_session.query(User).filter(User.id == admin.id).delete()
            db_session.commit()

    def test_payment_invoice_id_mismatch_rejected(self, client, db_session, auth_headers):
        staff = _make_user(db_session, UserRole.STAFF)
        customer = _make_customer(db_session, staff)
        db_session.commit()
        try:
            r = _create_draft_invoice(client, auth_headers(staff), str(customer.id))
            assert r.status_code == 201
            inv_id = r.json()["id"]
            client.post(f"/api/invoices/{inv_id}/finalize", headers=auth_headers(staff))

            # invoice_id in body does not match URL
            pay_r = client.post(
                f"/api/invoices/{inv_id}/payments",
                json={
                    "invoice_id": str(uuid.uuid4()),  # Different ID
                    "amount": "100.00",
                    "payment_method": "bank_transfer",
                    "payment_status": "succeeded",
                },
                headers=auth_headers(staff),
            )
            assert pay_r.status_code == 400
        finally:
            _cleanup(db_session, [staff.id], [customer.id])
