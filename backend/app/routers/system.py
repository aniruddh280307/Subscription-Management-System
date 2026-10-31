from fastapi import APIRouter, Depends

from app.database import settings
from app.dependencies import require_staff_or_admin
from app.models import User
from app.services.scheduler_service import automation_state

router = APIRouter(prefix="/api/system", tags=["System Status"])


@router.get("/readiness")
def readiness(_: User = Depends(require_staff_or_admin)):
    sender = settings.smtp_sender_email or settings.smtp_username
    return {
        "email": {
            "configured": bool(settings.smtp_host and sender),
            "host_configured": bool(settings.smtp_host),
            "sender_configured": bool(sender),
            "authentication_configured": bool(settings.smtp_username and settings.smtp_password),
            "reset_url": settings.frontend_reset_url,
        },
        "automation": dict(automation_state),
    }
