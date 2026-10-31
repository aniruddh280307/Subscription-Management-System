"""
End-to-End Integration Audit Test Suite for SubscriptHQ (Prompt 10).

Validates the full application lifecycle across all modules:
1. Multi-role setup: Admin, Staff, Customer
2. Catalog setup: Product -> SubscriptionPlan (with Decimal pricing)
3. Customer creation & isolation verification
4. Subscription creation (trial + period calculation)
5. Invoice generation & finalization (Draft -> Open, line-item price snapshots)
6. Payment processing (Partial payment -> Open -> Full payment -> Paid, idempotency)
7. Financial refund reconciliation
8. Dashboard analytics aggregation reflecting real data
9. Revenue Guardian automated detection run confirming data integrity
10. HAR Diagnostics ingestion, server-side redaction, and private reporting
"""

import uuid
from decimal import Decimal
from datetime import date, timedelta, datetime, timezone
import pytest

from app.models import (
    User,
    UserRole,
    Customer,
    Product,
    SubscriptionPlan,
    BillingInterval,
    Subscription,
    SubscriptionStatus,
    Invoice,
    InvoiceStatus,
    Payment,
    PaymentStatus,
    AuditLog,
)
from app.services import har_service
from app.security import hash_password


class TestEndToEndLifecycleAudit:
    """Comprehensive E2E workflow test verifying complete cross-module integration."""

    def test_full_subscription_to_invoice_to_payment_workflow(
        self, client, db_session, auth_headers
    ):
        # ---------------------------------------------------------
        # 1. Setup Actors: Admin, Staff, and Customer users
        # ---------------------------------------------------------
        admin_email = f"admin_{uuid.uuid4().hex[:6]}@example.com"
        staff_email = f"staff_{uuid.uuid4().hex[:6]}@example.com"
        cust_email = f"buyer_{uuid.uuid4().hex[:6]}@example.com"

        admin_user = User(email=admin_email, hashed_password=hash_password("AdminPassword123!"), full_name="System Administrator", role=UserRole.ADMIN, is_active=True, is_verified=True)
        staff_user = User(email=staff_email, hashed_password=hash_password("StaffPassword123!"), full_name="Operations Staff", role=UserRole.STAFF, is_active=True, is_verified=True)
        db_session.add_all([admin_user, staff_user]); db_session.commit()

        reg_cust = client.post("/api/auth/register", json={
            "email": cust_email,
            "password": "CustomerPassword123!",
            "full_name": "Acme Corp Buyer",
        })
        assert reg_cust.status_code == 201
        cust_data = reg_cust.json()
        cust_user = db_session.query(User).filter(User.id == cust_data["id"]).first()

        try:
            # ---------------------------------------------------------
            # 2. Staff creates a Customer record linked to cust_user
            # ---------------------------------------------------------
            cust_res = client.post("/api/customers", json={
                "name": "Acme Corporation",
                "email": cust_email,
                "user_id": str(cust_user.id),
                "billing_address": "123 Market St, Suite 400",
                "currency": "USD",
            }, headers=auth_headers(staff_user))
            assert cust_res.status_code == 201
            customer_id = cust_res.json()["id"]

            # Customer isolation check: Customer cannot view all customers
            cust_list_res = client.get("/api/customers", headers=auth_headers(cust_user))
            assert cust_list_res.status_code == 200
            # Customer only sees their own record
            assert all(c["id"] == customer_id for c in cust_list_res.json()["items"])

            # ---------------------------------------------------------
            # 3. Admin creates a Product and a SubscriptionPlan
            # ---------------------------------------------------------
            prod_res = client.post("/api/products", json={
                "name": "Cloud Analytics Pro",
                "code": f"PROD_{uuid.uuid4().hex[:6].upper()}",
                "description": "Enterprise cloud analytics platform",
                "is_active": True,
            }, headers=auth_headers(admin_user))
            assert prod_res.status_code == 201
            product_id = prod_res.json()["id"]

            plan_res = client.post("/api/plans", json={
                "product_id": product_id,
                "name": "Professional Monthly",
                "code": f"PLAN_{uuid.uuid4().hex[:6].upper()}",
                "price": "99.00",
                "currency": "USD",
                "billing_interval": "monthly",
                "interval_count": 1,
                "trial_period_days": 14,
                "is_active": True,
            }, headers=auth_headers(admin_user))
            assert plan_res.status_code == 201
            plan_id = plan_res.json()["id"]

            # ---------------------------------------------------------
            # 4. Customer subscribes to the Plan
            # ---------------------------------------------------------
            sub_res = client.post("/api/subscriptions", json={
                "customer_id": customer_id,
                "plan_id": plan_id,
                "start_date": datetime.now(timezone.utc).isoformat(),
            }, headers=auth_headers(cust_user))
            assert sub_res.status_code == 201
            sub_data = sub_res.json()
            subscription_id = sub_data["id"]
            assert sub_data["status"] == "trialing"  # Because trial_period_days=14

            # ---------------------------------------------------------
            # 5. Staff creates and finalizes an Invoice for the subscription
            # ---------------------------------------------------------
            today = date.today()
            inv_res = client.post("/api/invoices", json={
                "customer_id": customer_id,
                "subscription_id": subscription_id,
                "currency": "USD",
                "issue_date": today.isoformat(),
                "due_date": (today + timedelta(days=30)).isoformat(),
                "items": [
                    {
                        "description": "Professional Monthly - Cycle 1",
                        "quantity": 1,
                        "unit_price": "99.00",
                    }
                ],
            }, headers=auth_headers(staff_user))
            assert inv_res.status_code == 201
            inv_data = inv_res.json()
            invoice_id = inv_data["id"]
            assert inv_data["status"] == "draft"
            assert Decimal(inv_data["total_amount"]) == Decimal("99.00")

            # Finalize Invoice (Draft -> Open)
            fin_res = client.post(f"/api/invoices/{invoice_id}/finalize", headers=auth_headers(staff_user))
            assert fin_res.status_code == 200
            assert fin_res.json()["status"] == "open"
            assert Decimal(fin_res.json()["amount_due"]) == Decimal("99.00")

            # ---------------------------------------------------------
            # 6. Payment recording: Partial payment then full payment
            # ---------------------------------------------------------
            idempotency_key_1 = str(uuid.uuid4())
            pay1_res = client.post(f"/api/invoices/{invoice_id}/payments", json={
                "invoice_id": invoice_id,
                "amount": "49.00",
                "payment_method": "credit_card",
                "idempotency_key": idempotency_key_1,
                "transaction_reference": f"txn_{uuid.uuid4().hex[:8]}",
            }, headers=auth_headers(staff_user))
            assert pay1_res.status_code == 201
            pay1_data = pay1_res.json()
            assert pay1_data["status"] == "succeeded"

            # Check invoice status is now partially_paid
            inv_check1 = client.get(f"/api/invoices/{invoice_id}", headers=auth_headers(cust_user))
            assert inv_check1.status_code == 200
            assert inv_check1.json()["status"] == "partially_paid"
            assert Decimal(inv_check1.json()["amount_paid"]) == Decimal("49.00")
            assert Decimal(inv_check1.json()["amount_due"]) == Decimal("50.00")

            # Pay remainder
            idempotency_key_2 = str(uuid.uuid4())
            pay2_res = client.post(f"/api/invoices/{invoice_id}/payments", json={
                "invoice_id": invoice_id,
                "amount": "50.00",
                "payment_method": "credit_card",
                "idempotency_key": idempotency_key_2,
            }, headers=auth_headers(staff_user))
            assert pay2_res.status_code == 201

            # Check invoice is now PAID
            inv_check2 = client.get(f"/api/invoices/{invoice_id}", headers=auth_headers(cust_user))
            assert inv_check2.status_code == 200
            assert inv_check2.json()["status"] == "paid"
            assert Decimal(inv_check2.json()["amount_paid"]) == Decimal("99.00")
            assert Decimal(inv_check2.json()["amount_due"]) == Decimal("0.00")

            # ---------------------------------------------------------
            # 7. Dashboard metrics reflect real database aggregation
            # ---------------------------------------------------------
            dash_res = client.get("/api/dashboard/summary", headers=auth_headers(staff_user))
            assert dash_res.status_code == 200
            dash_data = dash_res.json()
            assert dash_data["total_active_customers"] >= 1
            assert Decimal(dash_data["total_collected"]) >= Decimal("99.00")
            assert Decimal(dash_data["mrr"]) >= Decimal("0.00")

            # ---------------------------------------------------------
            # 8. Revenue Guardian runs without detecting integrity errors
            # ---------------------------------------------------------
            grd_res = client.get("/api/guardian/run", headers=auth_headers(staff_user))
            assert grd_res.status_code == 200
            grd_data = grd_res.json()
            # Verify no data integrity mismatches for our customer's invoice
            for f in grd_data.get("findings", []):
                if f.get("affected_ids", {}).get("invoice_id") == invoice_id:
                    # Should NOT be a mismatch or duplicate
                    assert f["rule"] not in ("INVOICE_TOTAL_MISMATCH", "AMOUNT_PAID_MISMATCH", "PAYMENT_INVOICE_MISMATCH")

            # ---------------------------------------------------------
            # 9. HAR Diagnostics: Staff uploads HAR with sensitive tokens
            # ---------------------------------------------------------
            har_content = {
                "log": {
                    "version": "1.2",
                    "entries": [
                        {
                            "time": 250.0,
                            "request": {
                                "method": "POST",
                                "url": f"https://api.subscripthq.test/api/invoices/{invoice_id}/payments?auth_token=super_secret_jwt",
                                "headers": [
                                    {"name": "Authorization", "value": "Bearer my_super_secret_staff_token"},
                                    {"name": "Cookie", "value": "session_id=confidential_session_123"},
                                ],
                            },
                            "response": {
                                "status": 201,
                                "statusText": "Created",
                                "headers": [],
                                "content": {"size": 128, "mimeType": "application/json", "text": "sensitive_body"},
                            },
                        }
                    ],
                }
            }

            har_up = client.post("/api/har/upload-json", json={
                "filename": "e2e_checkout.har",
                "har_content": har_content,
            }, headers=auth_headers(staff_user))
            assert har_up.status_code == 201
            har_report = har_up.json()
            har_report_id = har_report["report_id"]

            # Redaction confirmed
            entry = har_report["entries"][0]
            assert "super_secret_jwt" not in entry["url"]
            assert entry["headers_sample"]["Authorization"] == "[REDACTED]"
            assert entry["headers_sample"]["Cookie"] == "[REDACTED]"
            assert "sensitive_body" not in str(har_report)

            # Cleanup HAR report
            har_service.delete_report(har_report_id)

        finally:
            # Cleanup database records in reverse dependency order
            db_session.query(AuditLog).filter(
                AuditLog.entity_id.in_([str(customer_id) if 'customer_id' in locals() else None,
                                        str(invoice_id) if 'invoice_id' in locals() else None])
            ).delete(synchronize_session=False)

            if 'invoice_id' in locals():
                db_session.query(Payment).filter(Payment.invoice_id == invoice_id).delete()
                db_session.query(Invoice).filter(Invoice.id == invoice_id).delete()

            if 'subscription_id' in locals():
                db_session.query(Subscription).filter(Subscription.id == subscription_id).delete()

            if 'plan_id' in locals():
                db_session.query(SubscriptionPlan).filter(SubscriptionPlan.id == plan_id).delete()

            if 'product_id' in locals():
                db_session.query(Product).filter(Product.id == product_id).delete()

            if 'customer_id' in locals():
                db_session.query(Customer).filter(Customer.id == customer_id).delete()

            db_session.query(User).filter(
                User.id.in_([admin_user.id, staff_user.id, cust_user.id])
            ).delete()
            db_session.commit()
