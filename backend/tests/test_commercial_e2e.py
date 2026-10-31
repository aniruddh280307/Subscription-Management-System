import uuid
from datetime import date
from decimal import Decimal

from app.models import (
    AuditLog, Customer, DiscountRule, GuardianFinding, Invoice, InvoiceItem,
    Payment, Product, Quotation, QuotationLine, QuotationTemplate,
    QuotationTemplateLine, Subscription, SubscriptionPlan, TaxRule, User, UserRole,
)
from app.security import hash_password


def test_quotation_to_subscription_to_automated_invoice(client, db_session, auth_headers):
    suffix = uuid.uuid4().hex[:8]
    admin = User(email=f"commercial_admin_{suffix}@example.com", hashed_password=hash_password("CommercialAdmin123!"), full_name="Commercial Admin", role=UserRole.ADMIN, is_active=True)
    customer = Customer(name="Commercial Customer", email=f"commercial_customer_{suffix}@example.com", currency="USD", is_active=True, is_archived=False)
    product = Product(name="Commercial Product", code=f"CP-{suffix}", product_type="software", sales_price=Decimal("100.00"), cost_price=Decimal("40.00"), is_active=True, is_archived=False)
    db_session.add_all([admin, customer, product]); db_session.commit(); db_session.refresh(product)
    plan = SubscriptionPlan(product_id=product.id, name="Monthly Commercial", code=f"PLAN-{suffix}", billing_interval="monthly", interval_count=1, price=Decimal("100.00"), currency="USD", trial_period_days=0, minimum_quantity=1, payment_terms_days=7, is_active=True, is_archived=False)
    db_session.add(plan); db_session.commit(); db_session.refresh(plan)
    headers = auth_headers(admin)
    created_ids = {}
    try:
        tax = client.post("/api/tax-rules", headers=headers, json={"name":"Test tax","code":f"TAX-{suffix}","adjustment_type":"percentage","value":"10.00","product_id":str(product.id)}); assert tax.status_code == 201
        created_ids["tax"] = tax.json()["id"]
        discount = client.post("/api/discount-rules", headers=headers, json={"name":"Test discount","code":f"DISC-{suffix}","adjustment_type":"percentage","value":"20.00","product_id":str(product.id),"minimum_purchase":"100.00","minimum_quantity":2,"usage_limit":2}); assert discount.status_code == 201
        created_ids["discount"] = discount.json()["id"]
        quote = client.post("/api/quotations", headers=headers, json={"customer_id":str(customer.id),"payment_terms_days":7,"lines":[{"product_id":str(product.id),"plan_id":str(plan.id),"description":"Two seats","quantity":2,"unit_price":"100.00"}]}); assert quote.status_code == 201
        quote_data = quote.json(); created_ids["quote"] = quote_data["id"]
        expected_total = Decimal(quote_data["total_amount"])
        assert quote_data["status"] == "draft"; assert Decimal(quote_data["tax_amount"]) >= Decimal("20.00"); assert Decimal(quote_data["discount_amount"]) >= Decimal("40.00"); assert expected_total == Decimal(quote_data["subtotal"]) + Decimal(quote_data["tax_amount"]) - Decimal(quote_data["discount_amount"])
        assert client.post(f"/api/quotations/{quote_data['id']}/send", headers=headers).json()["status"] == "quotation"
        assert client.post(f"/api/quotations/{quote_data['id']}/confirm", headers=headers).json()["status"] == "confirmed"
        activated = client.post(f"/api/quotations/{quote_data['id']}/activate", headers=headers); assert activated.status_code == 200; assert activated.json()["status"] == "active"
        sub = db_session.query(Subscription).filter(Subscription.quotation_id == uuid.UUID(quote_data["id"])).first(); assert sub is not None; assert sub.quantity == 2; assert sub.subscription_number.startswith("SUB-")
        # A newly activated subscription is billed at the start of its first period.
        billing = client.post("/api/billing/run", headers=headers, json={"as_of":date.today().isoformat()}); assert billing.status_code == 200; assert billing.json()["created_count"] >= 1
        invoice = db_session.query(Invoice).filter(Invoice.subscription_id == sub.id).first(); assert invoice is not None; assert invoice.status.value == "open"; assert invoice.billing_key; assert invoice.amount_due == expected_total
        repeated = client.post("/api/billing/run", headers=headers, json={"as_of":date.today().isoformat()}); assert repeated.status_code == 200; assert repeated.json()["created_count"] == 0
        finding = GuardianFinding(fingerprint=uuid.uuid4().hex * 2, rule="TEST_RULE", title="Test finding", severity="low", certainty="confirmed", affected_ids={"invoice_id":str(invoice.id)}, evidence="Test evidence", recommended_action="Test action")
        db_session.add(finding); db_session.commit(); db_session.refresh(finding); created_ids["finding"] = finding.id
        resolved = client.patch(f"/api/guardian/findings/{finding.id}", headers=headers, json={"status":"resolved"}); assert resolved.status_code == 200; assert resolved.json()["status"] == "resolved"
    finally:
        if created_ids.get("finding"): db_session.query(GuardianFinding).filter(GuardianFinding.id == created_ids["finding"]).delete()
        quote_ids = [row.id for row in db_session.query(Quotation).filter(Quotation.customer_id == customer.id).all()]
        invoice_ids = [row.id for row in db_session.query(Invoice).filter(Invoice.customer_id == customer.id).all()]
        sub_ids = [row.id for row in db_session.query(Subscription).filter(Subscription.customer_id == customer.id).all()]
        db_session.query(AuditLog).filter(AuditLog.entity_id.in_([str(x) for x in quote_ids + invoice_ids + sub_ids])).delete(synchronize_session=False)
        db_session.query(Payment).filter(Payment.customer_id == customer.id).delete()
        db_session.query(InvoiceItem).filter(InvoiceItem.invoice_id.in_(invoice_ids)).delete(synchronize_session=False)
        db_session.query(Invoice).filter(Invoice.id.in_(invoice_ids)).delete(synchronize_session=False)
        db_session.query(DiscountRule).filter(DiscountRule.code == f"DISC-{suffix}").delete()
        db_session.query(QuotationLine).filter(QuotationLine.quotation_id.in_(quote_ids)).delete(synchronize_session=False)
        db_session.query(Subscription).filter(Subscription.id.in_(sub_ids)).delete(synchronize_session=False)
        db_session.query(Quotation).filter(Quotation.id.in_(quote_ids)).delete(synchronize_session=False)
        db_session.query(TaxRule).filter(TaxRule.code == f"TAX-{suffix}").delete()
        db_session.query(SubscriptionPlan).filter(SubscriptionPlan.id == plan.id).delete()
        db_session.query(Product).filter(Product.id == product.id).delete()
        db_session.query(Customer).filter(Customer.id == customer.id).delete()
        db_session.query(User).filter(User.id == admin.id).delete()
        db_session.commit()
