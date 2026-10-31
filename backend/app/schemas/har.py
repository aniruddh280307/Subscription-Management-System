"""
Pydantic schemas for HAR Diagnostics module.
"""

from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field, ConfigDict


class HAREntryItem(BaseModel):
    """Sanitized metadata for a single HTTP request/response in a HAR log."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    started_date_time: Optional[str] = None
    time_ms: float = Field(..., description="Request duration in milliseconds")
    method: str = Field(..., description="HTTP method (GET, POST, etc.)")
    url: str = Field(..., description="Sanitized URL with sensitive query params redacted")
    host: str = Field(..., description="Host / domain name extracted from URL")
    path: str = Field(..., description="URL path")
    status: int = Field(..., description="HTTP response status code")
    status_text: str = Field(..., description="HTTP response status text")
    mime_type: Optional[str] = Field(None, description="Response content MIME type")
    response_size: int = Field(0, description="Response content size in bytes")
    is_failed: bool = Field(..., description="True if status >= 400 or status == 0")
    is_slow: bool = Field(..., description="True if duration exceeds slow threshold")
    redacted_items_count: int = Field(0, description="Number of sensitive fields redacted for this entry")
    headers_sample: Dict[str, str] = Field(default_factory=dict, description="Sanitized request headers")


class HARReportSummary(BaseModel):
    """Summary overview of a parsed and sanitized HAR file."""
    model_config = ConfigDict(from_attributes=True)

    report_id: str
    filename: str
    uploaded_at: str
    uploader_id: str
    uploader_email: Optional[str] = None
    total_requests: int
    failed_requests: int
    slow_requests: int
    avg_duration_ms: float
    max_duration_ms: float
    min_duration_ms: float
    status_distribution: Dict[str, int]
    method_distribution: Dict[str, int]
    domains: List[str]
    total_redacted_count: int
    submission_status: str = "submitted_to_developers"
    developer_status: str = "new"
    disclaimer: str = (
        "Note: Browser-exported HAR diagnostics only covers user-exported network interactions. "
        "Antigravity does not automatically capture background browser traffic."
    )


class HARReportDetail(HARReportSummary):
    """Detailed view of a HAR report including all sanitized entries."""
    entries: List[HAREntryItem]


class HAREntriesPage(BaseModel):
    """Paginated list of sanitized HAR entries with applied filters."""
    report_id: str
    total: int
    page: int
    page_size: int
    entries: List[HAREntryItem]


class HARUploadJSONRequest(BaseModel):
    """Alternative JSON body upload for HAR data."""
    filename: Optional[str] = "browser_log.har"
    har_content: Dict[str, Any]
    slow_threshold_ms: Optional[float] = 1000.0
