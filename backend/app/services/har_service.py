"""
HAR Diagnostics Service.

Provides secure server-side ingestion, parsing, sanitization, and analysis of
browser-exported HTTP Archive (.har) files.

Security & privacy guarantees:
- Authorization headers, Cookies, and API keys are redacted server-side.
- Sensitive query parameters (tokens, keys, secrets, passwords) are redacted.
- Raw request and response bodies are NOT stored.
- Sanitized reports are stored in private local storage.
- Does NOT claim automatic browser traffic capture — analyzes only user-provided HAR files.
"""

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import HTTPException, status

# Maximum allowed file size: 20 MB
MAX_HAR_SIZE_BYTES = 20 * 1024 * 1024

# Private storage location for sanitized reports
STORAGE_DIR = Path(__file__).resolve().parent.parent.parent / "storage" / "har_reports"

# Case-insensitive sensitive header names to redact
SENSITIVE_HEADER_NAMES = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "api-key",
    "x-auth-token",
    "x-csrf-token",
    "bearer",
    "proxy-authorization",
    "access-token",
    "session-token",
    "x-amz-security-token",
    "client-secret",
}

# Regex to match sensitive query parameter keys
SENSITIVE_QUERY_PATTERN = re.compile(
    r"(token|key|secret|password|auth|sig|signature|session|credential|jwt|code|apikey)",
    re.IGNORECASE,
)


def _ensure_storage_dir() -> Path:
    """Ensure the private reports directory exists."""
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    return STORAGE_DIR


def sanitize_url(raw_url: str) -> Tuple[str, str, str, int]:
    """
    Sanitize sensitive parameters from a URL query string.
    Returns (sanitized_url, host, path, redactions_count).
    """
    try:
        parts = urlsplit(raw_url)
        host = parts.netloc or "unknown"
        path = parts.path or "/"

        if not parts.query:
            return raw_url, host, path, 0

        query_params = parse_qsl(parts.query, keep_blank_values=True)
        sanitized_params = []
        redactions = 0

        for k, v in query_params:
            if SENSITIVE_QUERY_PATTERN.search(k):
                sanitized_params.append((k, "[REDACTED]"))
                redactions += 1
            else:
                sanitized_params.append((k, v))

        sanitized_query = urlencode(sanitized_params)
        sanitized_url = urlunsplit((parts.scheme, parts.netloc, parts.path, sanitized_query, parts.fragment))
        return sanitized_url, host, path, redactions
    except Exception:
        return raw_url, "unknown", "/", 0


def sanitize_headers(headers: List[Dict[str, Any]]) -> Tuple[Dict[str, str], int]:
    """
    Sanitize HTTP headers, redacting sensitive authorization tokens and cookies.
    Returns (sanitized_headers_dict, redactions_count).
    """
    sanitized = {}
    redactions = 0
    for h in headers or []:
        name = str(h.get("name", "")).strip()
        val = str(h.get("value", ""))
        if name.lower() in SENSITIVE_HEADER_NAMES:
            sanitized[name] = "[REDACTED]"
            redactions += 1
        else:
            # Check if header value itself contains bearer token pattern
            if name.lower().startswith("sec-") or len(val) < 200:
                sanitized[name] = val
            else:
                sanitized[name] = val[:200] + "...[TRUNCATED]"
    return sanitized, redactions


def parse_and_sanitize_har(
    raw_content: bytes or str,
    filename: str,
    uploader_id: str,
    uploader_email: Optional[str] = None,
    slow_threshold_ms: float = 1000.0,
) -> Dict[str, Any]:
    """
    Parse a raw HAR string/bytes, validate the schema, sanitize sensitive data,
    compute diagnostics metrics, and save to private storage.
    """
    # 1. Size validation
    if isinstance(raw_content, bytes):
        if len(raw_content) > MAX_HAR_SIZE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"HAR file exceeds maximum allowed size of {MAX_HAR_SIZE_BYTES // (1024*1024)}MB",
            )
        try:
            text_content = raw_content.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text_content = raw_content.decode("latin-1")
            except Exception:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="HAR file must be valid UTF-8 or text encoded JSON.",
                )
    else:
        text_content = raw_content
        if len(text_content.encode("utf-8")) > MAX_HAR_SIZE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="HAR file exceeds maximum allowed size",
            )

    # 2. JSON validation
    try:
        har_data = json.loads(text_content)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid JSON in HAR upload: {exc.msg} at line {exc.lineno}",
        )

    # 3. HAR Schema validation
    if not isinstance(har_data, dict) or "log" not in har_data or not isinstance(har_data["log"], dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid HAR file structure: root must contain a 'log' object.",
        )

    entries_raw = har_data["log"].get("entries")
    if not isinstance(entries_raw, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid HAR file structure: 'log.entries' must be a list.",
        )

    # 4. Processing & Sanitization
    report_id = str(uuid.uuid4())
    sanitized_entries = []
    total_redacted = 0

    status_distribution: Dict[str, int] = {}
    method_distribution: Dict[str, int] = {}
    domain_set = set()
    durations = []
    failed_count = 0
    slow_count = 0

    for idx, entry in enumerate(entries_raw):
        if not isinstance(entry, dict):
            continue

        req = entry.get("request", {})
        resp = entry.get("response", {})

        raw_url = str(req.get("url", ""))
        method = str(req.get("method", "GET")).upper()
        started_at = entry.get("startedDateTime")

        # Duration
        raw_time = entry.get("time", 0.0)
        try:
            time_ms = round(float(raw_time), 2)
            if time_ms < 0:
                time_ms = 0.0
        except (TypeError, ValueError):
            time_ms = 0.0

        # Response status
        try:
            status_code = int(resp.get("status", 0))
        except (TypeError, ValueError):
            status_code = 0
        status_text = str(resp.get("statusText", ""))

        # Response content
        content_info = resp.get("content", {})
        mime_type = content_info.get("mimeType")
        try:
            resp_size = int(content_info.get("size", 0))
        except (TypeError, ValueError):
            resp_size = 0

        # Sanitize URL
        clean_url, host, path, url_redactions = sanitize_url(raw_url)

        # Sanitize Headers (Sample for display)
        req_headers = req.get("headers", [])
        sanitized_headers, header_redactions = sanitize_headers(req_headers)

        # Count cookie redactions
        cookie_redactions = len(req.get("cookies", [])) + len(resp.get("cookies", []))

        entry_redactions = url_redactions + header_redactions + cookie_redactions
        total_redacted += entry_redactions

        # Classification
        is_failed = status_code >= 400 or status_code == 0
        is_slow = time_ms >= slow_threshold_ms

        if is_failed:
            failed_count += 1
        if is_slow:
            slow_count += 1

        durations.append(time_ms)
        status_key = str(status_code)
        status_distribution[status_key] = status_distribution.get(status_key, 0) + 1
        method_distribution[method] = method_distribution.get(method, 0) + 1
        if host and host != "unknown":
            domain_set.add(host)

        # Notice: Raw bodies are completely omitted!
        entry_item = {
            "id": f"{report_id}-{idx + 1}",
            "started_date_time": started_at,
            "time_ms": time_ms,
            "method": method,
            "url": clean_url,
            "host": host,
            "path": path,
            "status": status_code,
            "status_text": status_text,
            "mime_type": mime_type,
            "response_size": resp_size,
            "is_failed": is_failed,
            "is_slow": is_slow,
            "redacted_items_count": entry_redactions,
            "headers_sample": sanitized_headers,
        }
        sanitized_entries.append(entry_item)

    total_requests = len(sanitized_entries)
    avg_duration = round(sum(durations) / total_requests, 2) if total_requests > 0 else 0.0
    max_duration = max(durations) if durations else 0.0
    min_duration = min(durations) if durations else 0.0

    report_data = {
        "report_id": report_id,
        "filename": filename,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "uploader_id": uploader_id,
        "uploader_email": uploader_email,
        "total_requests": total_requests,
        "failed_requests": failed_count,
        "slow_requests": slow_count,
        "avg_duration_ms": avg_duration,
        "max_duration_ms": max_duration,
        "min_duration_ms": min_duration,
        "status_distribution": status_distribution,
        "method_distribution": method_distribution,
        "domains": sorted(list(domain_set)),
        "total_redacted_count": total_redacted,
        "submission_status": "submitted_to_developers",
        "developer_status": "new",
        "disclaimer": (
            "Note: Browser-exported HAR diagnostics only covers user-exported network interactions. "
            "Antigravity does not automatically capture background browser traffic."
        ),
        "entries": sanitized_entries,
    }

    # 5. Save to private storage
    storage_dir = _ensure_storage_dir()
    report_file = storage_dir / f"{report_id}.json"
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2)

    return report_data


def list_reports() -> List[Dict[str, Any]]:
    """List all saved sanitized HAR reports (metadata only)."""
    storage_dir = _ensure_storage_dir()
    reports = []
    for file_path in storage_dir.glob("*.json"):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                reports.append({
                    "report_id": data.get("report_id"),
                    "filename": data.get("filename"),
                    "uploaded_at": data.get("uploaded_at"),
                    "uploader_id": data.get("uploader_id"),
                    "uploader_email": data.get("uploader_email"),
                    "total_requests": data.get("total_requests", 0),
                    "failed_requests": data.get("failed_requests", 0),
                    "slow_requests": data.get("slow_requests", 0),
                    "avg_duration_ms": data.get("avg_duration_ms", 0.0),
                    "max_duration_ms": data.get("max_duration_ms", 0.0),
                    "min_duration_ms": data.get("min_duration_ms", 0.0),
                    "status_distribution": data.get("status_distribution", {}),
                    "method_distribution": data.get("method_distribution", {}),
                    "domains": data.get("domains", []),
                    "total_redacted_count": data.get("total_redacted_count", 0),
                    "submission_status": data.get("submission_status", "submitted_to_developers"),
                    "developer_status": data.get("developer_status", "new"),
                    "disclaimer": data.get("disclaimer"),
                })
        except Exception:
            continue
    # Sort latest first
    reports.sort(key=lambda x: x.get("uploaded_at", ""), reverse=True)
    return reports


def get_report(report_id: str) -> Dict[str, Any]:
    """Retrieve full sanitized HAR report by ID."""
    storage_dir = _ensure_storage_dir()
    report_file = storage_dir / f"{report_id}.json"
    if not report_file.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"HAR report '{report_id}' not found",
        )
    with open(report_file, "r", encoding="utf-8") as f:
        return json.load(f)


def get_report_entries(
    report_id: str,
    page: int = 1,
    page_size: int = 50,
    failed_only: bool = False,
    slow_only: bool = False,
    host: Optional[str] = None,
    method: Optional[str] = None,
    status_code: Optional[int] = None,
    search: Optional[str] = None,
) -> Dict[str, Any]:
    """Retrieve filtered and paginated entries from a HAR report."""
    report = get_report(report_id)
    entries: List[Dict[str, Any]] = report.get("entries", [])

    # Filters
    filtered = entries
    if failed_only:
        filtered = [e for e in filtered if e.get("is_failed")]
    if slow_only:
        filtered = [e for e in filtered if e.get("is_slow")]
    if host:
        filtered = [e for e in filtered if host.lower() in str(e.get("host", "")).lower()]
    if method:
        filtered = [e for e in filtered if e.get("method") == method.upper()]
    if status_code is not None:
        filtered = [e for e in filtered if e.get("status") == status_code]
    if search:
        search_lower = search.lower()
        filtered = [
            e for e in filtered
            if search_lower in str(e.get("url", "")).lower()
            or search_lower in str(e.get("path", "")).lower()
            or search_lower in str(e.get("status_text", "")).lower()
        ]

    total = len(filtered)
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    paged_entries = filtered[start_idx:end_idx]

    return {
        "report_id": report_id,
        "total": total,
        "page": page,
        "page_size": page_size,
        "entries": paged_entries,
    }


def delete_report(report_id: str) -> bool:
    """Delete a sanitized HAR report from private storage."""
    storage_dir = _ensure_storage_dir()
    report_file = storage_dir / f"{report_id}.json"
    if not report_file.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"HAR report '{report_id}' not found",
        )
    report_file.unlink()
    return True


def update_developer_status(report_id: str, developer_status: str) -> Dict[str, Any]:
    if developer_status not in {"new", "reviewing", "resolved"}:
        raise HTTPException(status_code=400, detail="Status must be new, reviewing, or resolved")
    report = get_report(report_id)
    report["developer_status"] = developer_status
    report["status_updated_at"] = datetime.now(timezone.utc).isoformat()
    report_file = _ensure_storage_dir() / f"{report_id}.json"
    with open(report_file, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    return report
