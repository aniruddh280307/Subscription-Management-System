import asyncio
from contextlib import suppress
from datetime import date, datetime, timezone

from app.database import SessionLocal, settings
from app.models import User, UserRole
from app.services.commercial_service import run_billing
from app.services.guardian_service import sync_guardian_findings


automation_state = {
    "enabled": settings.automation_enabled,
    "billing_interval_minutes": settings.billing_interval_minutes,
    "guardian_interval_minutes": settings.guardian_interval_minutes,
    "last_billing_run": None,
    "last_guardian_run": None,
    "last_billing_result": None,
    "last_guardian_findings": None,
    "last_error": None,
}


def _billing_tick():
    db = SessionLocal()
    try:
        actor = db.query(User).filter(User.role == UserRole.ADMIN, User.is_active.is_(True)).first()
        if not actor:
            automation_state["last_error"] = "Automated billing needs one active Administrator account."
            return
        result = run_billing(db, actor, date.today())
        automation_state["last_billing_result"] = result
        automation_state["last_billing_run"] = datetime.now(timezone.utc).isoformat()
        automation_state["last_error"] = None
    except Exception as exc:
        automation_state["last_error"] = f"Billing automation: {exc}"
    finally:
        db.close()


def _guardian_tick():
    db = SessionLocal()
    try:
        findings = sync_guardian_findings(db)
        automation_state["last_guardian_findings"] = len(findings)
        automation_state["last_guardian_run"] = datetime.now(timezone.utc).isoformat()
        automation_state["last_error"] = None
    except Exception as exc:
        automation_state["last_error"] = f"Guardian automation: {exc}"
    finally:
        db.close()


async def _loop(interval_minutes, callback):
    while True:
        await asyncio.sleep(max(interval_minutes, 1) * 60)
        await asyncio.to_thread(callback)


async def start_automation():
    if not settings.automation_enabled:
        return []
    return [
        asyncio.create_task(_loop(settings.billing_interval_minutes, _billing_tick)),
        asyncio.create_task(_loop(settings.guardian_interval_minutes, _guardian_tick)),
    ]


async def stop_automation(tasks):
    for task in tasks:
        task.cancel()
    for task in tasks:
        with suppress(asyncio.CancelledError):
            await task
