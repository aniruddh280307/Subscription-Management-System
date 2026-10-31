"""
HAR Diagnostics API endpoints.

Secure staff-only diagnostic tool for ingesting, redacting, and analyzing
browser network archives (.har files).
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status

from app.dependencies import get_current_user, require_staff_or_admin
from app.models import User, UserRole
from app.schemas.har import (
    HAREntriesPage,
    HARReportDetail,
    HARReportSummary,
    HARUploadJSONRequest,
)
from app.services import har_service

router = APIRouter(prefix="/api/har", tags=["HAR Diagnostics"])


@router.post("/upload", response_model=HARReportDetail, status_code=status.HTTP_201_CREATED)
async def upload_har_file(
    file: UploadFile = File(...),
    slow_threshold_ms: float = Query(1000.0, description="Threshold in ms to flag a request as slow"),
    current_user: User = Depends(get_current_user),
):
    """
    Upload and parse a browser-exported HAR file.
    All cookies, authorization tokens, API keys, and sensitive query parameters
    are redacted server-side. Raw request and response bodies are NOT stored.
    """
    filename = file.filename or "uploaded.har"
    content = await file.read()

    report = har_service.parse_and_sanitize_har(
        raw_content=content,
        filename=filename,
        uploader_id=str(current_user.id),
        uploader_email=current_user.email,
        slow_threshold_ms=slow_threshold_ms,
    )
    return report


@router.post("/upload-json", response_model=HARReportDetail, status_code=status.HTTP_201_CREATED)
def upload_har_json(
    payload: HARUploadJSONRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Upload HAR data as a JSON payload.
    Applies the same server-side redactions and validations.
    """
    import json
    raw_str = json.dumps(payload.har_content)

    report = har_service.parse_and_sanitize_har(
        raw_content=raw_str,
        filename=payload.filename or "uploaded.har",
        uploader_id=str(current_user.id),
        uploader_email=current_user.email,
        slow_threshold_ms=payload.slow_threshold_ms or 1000.0,
    )
    return report


@router.get("/reports", response_model=List[HARReportSummary])
def list_har_reports(
    current_user: User = Depends(get_current_user),
):
    """Staff see the developer queue; customers see only their submissions."""
    reports = har_service.list_reports()
    if current_user.role == UserRole.CUSTOMER:
        reports = [item for item in reports if item.get("uploader_id") == str(current_user.id)]
    return reports


def _authorized_report(report_id: str, user: User):
    report = har_service.get_report(report_id)
    if user.role == UserRole.CUSTOMER and report.get("uploader_id") != str(user.id):
        raise HTTPException(status_code=403, detail="You can only access your own diagnostic reports")
    return report


@router.get("/reports/{report_id}", response_model=HARReportDetail)
def get_har_report(
    report_id: str,
    current_user: User = Depends(get_current_user),
):
    """Retrieve full sanitized report details and entries."""
    return _authorized_report(report_id, current_user)


@router.get("/reports/{report_id}/entries", response_model=HAREntriesPage)
def get_har_report_entries(
    report_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    failed_only: bool = Query(False, description="Filter for failed requests (status >= 400 or 0)"),
    slow_only: bool = Query(False, description="Filter for requests exceeding slow threshold"),
    host: Optional[str] = Query(None, description="Filter by host/domain"),
    method: Optional[str] = Query(None, description="Filter by HTTP method"),
    status_code: Optional[int] = Query(None, description="Filter by exact HTTP status code"),
    search: Optional[str] = Query(None, description="Search term for URL, path, or status text"),
    current_user: User = Depends(get_current_user),
):
    """Retrieve paginated, filtered entries for a specific HAR report."""
    _authorized_report(report_id, current_user)
    return har_service.get_report_entries(
        report_id=report_id,
        page=page,
        page_size=page_size,
        failed_only=failed_only,
        slow_only=slow_only,
        host=host,
        method=method,
        status_code=status_code,
        search=search,
    )


@router.delete("/reports/{report_id}", status_code=status.HTTP_200_OK)
def delete_har_report(
    report_id: str,
    current_user: User = Depends(get_current_user),
):
    """Permanently delete a sanitized HAR report from private storage."""
    _authorized_report(report_id, current_user)
    har_service.delete_report(report_id)
    return {"message": f"HAR report '{report_id}' deleted successfully"}


@router.patch("/reports/{report_id}/status", response_model=HARReportDetail)
def set_developer_status(
    report_id: str,
    developer_status: str = Query(...),
    current_user: User = Depends(require_staff_or_admin),
):
    """Update a report as it moves through the private developer queue."""
    return har_service.update_developer_status(report_id, developer_status)
