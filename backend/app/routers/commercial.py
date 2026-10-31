import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_admin, require_staff_or_admin
from app.models import (
    Customer, DiscountRule, Quotation, QuotationStatus, QuotationTemplate,
    TaxRule, User, UserRole,
)
from app.schemas.commercial import (
    BillingRunRequest, DiscountRuleCreate, DiscountRuleResponse, QuotationCreate,
    QuotationResponse, TaxRuleCreate, TaxRuleResponse, TemplateCreate, TemplateResponse,
)
from app.services.commercial_service import (
    create_quotation, create_template, run_billing, transition_quotation,
)

router = APIRouter(prefix="/api", tags=["Commercial Operations"])


def _unique_code(db, model, code):
    normalized = code.strip().upper()
    if db.query(model).filter(model.code == normalized).first():
        raise HTTPException(status_code=400, detail=f"Code '{normalized}' already exists")
    return normalized


@router.post("/tax-rules", response_model=TaxRuleResponse, status_code=201)
def add_tax_rule(request: TaxRuleCreate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    data = request.model_dump(); data["code"] = _unique_code(db, TaxRule, request.code)
    rule = TaxRule(**data); db.add(rule); db.commit(); db.refresh(rule); return rule


@router.get("/tax-rules", response_model=list[TaxRuleResponse])
def list_tax_rules(db: Session = Depends(get_db), _: User = Depends(require_staff_or_admin)):
    return db.query(TaxRule).order_by(TaxRule.created_at.desc()).all()


@router.delete("/tax-rules/{rule_id}", response_model=TaxRuleResponse)
def archive_tax_rule(rule_id: uuid.UUID, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    rule = db.query(TaxRule).filter(TaxRule.id == rule_id).first()
    if not rule: raise HTTPException(status_code=404, detail="Tax rule not found")
    rule.is_active = False; db.commit(); db.refresh(rule); return rule


@router.post("/discount-rules", response_model=DiscountRuleResponse, status_code=201)
def add_discount_rule(request: DiscountRuleCreate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    data = request.model_dump(); data["code"] = _unique_code(db, DiscountRule, request.code)
    rule = DiscountRule(**data); db.add(rule); db.commit(); db.refresh(rule); return rule


@router.get("/discount-rules", response_model=list[DiscountRuleResponse])
def list_discount_rules(db: Session = Depends(get_db), _: User = Depends(require_staff_or_admin)):
    return db.query(DiscountRule).order_by(DiscountRule.created_at.desc()).all()


@router.delete("/discount-rules/{rule_id}", response_model=DiscountRuleResponse)
def archive_discount_rule(rule_id: uuid.UUID, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    rule = db.query(DiscountRule).filter(DiscountRule.id == rule_id).first()
    if not rule: raise HTTPException(status_code=404, detail="Discount rule not found")
    rule.is_active = False; db.commit(); db.refresh(rule); return rule


@router.post("/quotation-templates", response_model=TemplateResponse, status_code=201)
def add_template(request: TemplateCreate, db: Session = Depends(get_db), _: User = Depends(require_staff_or_admin)):
    return create_template(db, request)


@router.get("/quotation-templates", response_model=list[TemplateResponse])
def list_templates(db: Session = Depends(get_db), _: User = Depends(require_staff_or_admin)):
    return db.query(QuotationTemplate).filter(QuotationTemplate.is_active.is_(True)).order_by(QuotationTemplate.created_at.desc()).all()


@router.post("/quotations", response_model=QuotationResponse, status_code=201)
def add_quotation(request: QuotationCreate, db: Session = Depends(get_db), _: User = Depends(require_staff_or_admin)):
    return create_quotation(db, request)


@router.get("/quotations", response_model=list[QuotationResponse])
def list_quotations(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    query = db.query(Quotation)
    if current_user.role == UserRole.CUSTOMER:
        customer = db.query(Customer).filter(Customer.user_id == current_user.id).first()
        if not customer: return []
        query = query.filter(Quotation.customer_id == customer.id)
    return query.order_by(Quotation.created_at.desc()).all()


def _quote_or_404(db, quote_id, user):
    quote = db.query(Quotation).filter(Quotation.id == quote_id).first()
    if not quote: raise HTTPException(status_code=404, detail="Quotation not found")
    if user.role == UserRole.CUSTOMER:
        customer = db.query(Customer).filter(Customer.user_id == user.id).first()
        if not customer or quote.customer_id != customer.id: raise HTTPException(status_code=403, detail="Access denied")
    return quote


@router.get("/quotations/{quote_id}", response_model=QuotationResponse)
def get_quotation(quote_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _quote_or_404(db, quote_id, user)


@router.post("/quotations/{quote_id}/send", response_model=QuotationResponse)
def send_quotation(quote_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require_staff_or_admin)):
    return transition_quotation(db, _quote_or_404(db, quote_id, user), QuotationStatus.QUOTATION, user)


@router.post("/quotations/{quote_id}/confirm", response_model=QuotationResponse)
def confirm_quotation(quote_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require_staff_or_admin)):
    return transition_quotation(db, _quote_or_404(db, quote_id, user), QuotationStatus.CONFIRMED, user)


@router.post("/quotations/{quote_id}/activate", response_model=QuotationResponse)
def activate_quotation(quote_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require_staff_or_admin)):
    return transition_quotation(db, _quote_or_404(db, quote_id, user), QuotationStatus.ACTIVE, user)


@router.post("/quotations/{quote_id}/close", response_model=QuotationResponse)
def close_quotation(quote_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require_staff_or_admin)):
    return transition_quotation(db, _quote_or_404(db, quote_id, user), QuotationStatus.CLOSED, user)


@router.post("/billing/run")
def run_recurring_billing(request: BillingRunRequest, db: Session = Depends(get_db), user: User = Depends(require_staff_or_admin)):
    return run_billing(db, user, request.as_of)
