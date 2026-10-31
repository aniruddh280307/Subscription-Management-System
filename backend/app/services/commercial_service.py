import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models import (
    AdjustmentType, Customer, DiscountRule, Invoice, Product, Quotation,
    QuotationLine, QuotationStatus, QuotationTemplate, QuotationTemplateLine,
    Subscription, SubscriptionStatus, TaxRule, User,
)
from app.services.invoice_service import create_invoice, finalize_invoice
from app.services.subscription_service import calculate_period_end, create_subscription


def _active_on(query, model, on_date: date):
    return query.filter(
        model.is_active.is_(True),
        (model.valid_from.is_(None) | (model.valid_from <= on_date)),
        (model.valid_until.is_(None) | (model.valid_until >= on_date)),
    )


def calculate_adjustments(db: Session, subtotal: Decimal, quantity: int, product_ids: list, subscription_id=None, on_date=None):
    on_date = on_date or date.today()
    taxes = _active_on(db.query(TaxRule), TaxRule, on_date).filter(
        TaxRule.product_id.is_(None) | TaxRule.product_id.in_(product_ids)
    ).all()
    tax_amount = Decimal("0.00")
    for rule in taxes:
        tax_amount += subtotal * rule.value / Decimal("100") if rule.adjustment_type == AdjustmentType.PERCENTAGE else rule.value

    discounts = _active_on(db.query(DiscountRule), DiscountRule, on_date).filter(
        (DiscountRule.product_id.is_(None) | DiscountRule.product_id.in_(product_ids)),
        (DiscountRule.subscription_id.is_(None) | (DiscountRule.subscription_id == subscription_id)),
        DiscountRule.minimum_purchase <= subtotal,
        DiscountRule.minimum_quantity <= quantity,
    ).all()
    eligible = [r for r in discounts if r.usage_limit is None or r.times_redeemed < r.usage_limit]
    amounts = []
    for rule in eligible:
        amount = subtotal * rule.value / Decimal("100") if rule.adjustment_type == AdjustmentType.PERCENTAGE else rule.value
        amounts.append((min(amount, subtotal), rule))
    discount_amount, discount_rule = max(amounts, key=lambda pair: pair[0]) if amounts else (Decimal("0.00"), None)
    return tax_amount.quantize(Decimal("0.01")), discount_amount.quantize(Decimal("0.01")), discount_rule


def _next_quote_number(db: Session) -> str:
    prefix = f"QUO-{datetime.now(timezone.utc).strftime('%Y%m')}-"
    return f"{prefix}{db.query(Quotation).filter(Quotation.quotation_number.like(f'{prefix}%')).count() + 1:06d}"


def create_template(db: Session, request):
    code = request.code.strip().upper()
    if db.query(QuotationTemplate).filter(QuotationTemplate.code == code).first():
        raise HTTPException(status_code=400, detail="Quotation template code already exists")
    template = QuotationTemplate(name=request.name.strip(), code=code, valid_days=request.valid_days, payment_terms_days=request.payment_terms_days, notes=request.notes)
    for line in request.lines:
        template.lines.append(QuotationTemplateLine(product_id=line.product_id, plan_id=line.plan_id, description=line.description, quantity=line.quantity, unit_price=line.unit_price))
    db.add(template); db.commit(); db.refresh(template)
    return template


def create_quotation(db: Session, request):
    customer = db.query(Customer).filter(Customer.id == request.customer_id, Customer.is_archived.is_(False)).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    source_lines = request.lines
    valid_days = request.valid_days
    payment_terms = request.payment_terms_days
    template = None
    if request.template_id:
        template = db.query(QuotationTemplate).filter(QuotationTemplate.id == request.template_id, QuotationTemplate.is_active.is_(True)).first()
        if not template:
            raise HTTPException(status_code=404, detail="Quotation template not found")
        source_lines = template.lines
        valid_days = template.valid_days
        payment_terms = template.payment_terms_days
    if not source_lines:
        raise HTTPException(status_code=400, detail="Quotation requires at least one line")
    lines = []
    subtotal = Decimal("0.00")
    for line in source_lines:
        amount = (Decimal(str(line.unit_price)) * line.quantity).quantize(Decimal("0.01"))
        subtotal += amount
        lines.append(QuotationLine(product_id=line.product_id, plan_id=line.plan_id, description=line.description, quantity=line.quantity, unit_price=line.unit_price, amount=amount))
    tax, discount, _ = calculate_adjustments(db, subtotal, sum(line.quantity for line in source_lines), [line.product_id for line in source_lines])
    quote = Quotation(
        quotation_number=_next_quote_number(db), customer_id=request.customer_id,
        template_id=template.id if template else None, status=QuotationStatus.DRAFT,
        valid_until=date.today() + timedelta(days=valid_days), payment_terms_days=payment_terms,
        currency=request.currency.upper(), subtotal=subtotal, tax_amount=tax, discount_amount=discount,
        total_amount=max(subtotal + tax - discount, Decimal("0.00")), notes=request.notes, lines=lines,
    )
    db.add(quote); db.commit(); db.refresh(quote)
    return quote


def transition_quotation(db: Session, quote: Quotation, target: QuotationStatus, user: User):
    allowed = {
        QuotationStatus.DRAFT: {QuotationStatus.QUOTATION, QuotationStatus.CANCELED},
        QuotationStatus.QUOTATION: {QuotationStatus.CONFIRMED, QuotationStatus.CANCELED, QuotationStatus.EXPIRED},
        QuotationStatus.CONFIRMED: {QuotationStatus.ACTIVE, QuotationStatus.CLOSED},
        QuotationStatus.ACTIVE: {QuotationStatus.CLOSED},
    }
    if target not in allowed.get(quote.status, set()):
        raise HTTPException(status_code=400, detail=f"Cannot move quotation from {quote.status.value} to {target.value}")
    if target == QuotationStatus.CONFIRMED and quote.valid_until < date.today():
        quote.status = QuotationStatus.EXPIRED; db.commit()
        raise HTTPException(status_code=400, detail="Quotation has expired")
    quote.status = target
    if target == QuotationStatus.CONFIRMED:
        quote.confirmed_at = datetime.now(timezone.utc)
    if target == QuotationStatus.ACTIVE:
        for line in quote.lines:
            create_subscription(db, user, quote.customer_id, line.plan_id, quantity=line.quantity, payment_terms_days=quote.payment_terms_days, quotation_id=quote.id)
    if target == QuotationStatus.CLOSED:
        quote.closed_at = datetime.now(timezone.utc)
        for sub in db.query(Subscription).filter(Subscription.quotation_id == quote.id, Subscription.status.in_([SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING, SubscriptionStatus.PAUSED])).all():
            sub.status = SubscriptionStatus.CANCELED; sub.ended_at = quote.closed_at
    db.commit(); db.refresh(quote)
    return quote


def run_billing(db: Session, user: User, as_of: date):
    subscriptions = db.query(Subscription).filter(
        Subscription.status.in_([SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING]),
        # Bill at the beginning of each service period. After invoicing, the
        # period advances, so the next cycle remains ineligible until its start.
        Subscription.current_period_start <= datetime.combine(as_of, datetime.max.time(), tzinfo=timezone.utc),
    ).all()
    created, skipped = [], []
    for sub in subscriptions:
        key = f"{sub.id}:{sub.current_period_start.date().isoformat()}"
        if db.query(Invoice).filter(Invoice.billing_key == key).first():
            skipped.append(key); continue
        plan = sub.plan; product = plan.product
        unit_price = plan.price
        subtotal = (unit_price * sub.quantity).quantize(Decimal("0.01"))
        tax, discount, discount_rule = calculate_adjustments(db, subtotal, sub.quantity, [product.id], sub.id, as_of)
        item = type("BillingItem", (), {"product_id": product.id, "plan_id": plan.id, "description": f"{product.name} — {plan.name}", "quantity": sub.quantity, "unit_price": unit_price})()
        invoice = create_invoice(db, user, sub.customer_id, sub.id, as_of, as_of + timedelta(days=sub.payment_terms_days), plan.currency, tax, discount, [item])
        invoice.billing_key = key
        if discount_rule:
            discount_rule.times_redeemed += 1
        db.commit(); finalize_invoice(db, user, invoice)
        created.append(str(invoice.id))
        sub.current_period_start = sub.current_period_end
        sub.current_period_end = calculate_period_end(sub.current_period_start, plan.billing_interval, plan.interval_count)
        if sub.expiry_date and sub.current_period_end.date() > sub.expiry_date and plan.auto_close_on_end:
            sub.status = SubscriptionStatus.EXPIRED; sub.ended_at = datetime.now(timezone.utc)
        db.commit()
    return {"as_of": as_of.isoformat(), "created": len(created), "skipped": len(skipped), "created_count": len(created), "skipped_count": len(skipped), "invoice_ids": created, "skipped_keys": skipped}
