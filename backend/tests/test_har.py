"""
Tests for HAR Diagnostics Module.

Verifies:
- Access control: Staff/Admin allowed, Customer/Unauthenticated blocked.
- Server-side redaction: Authorization, Cookies, API keys, and sensitive query params.
- Security: Raw bodies are NOT stored.
- Validation: Bad JSON, invalid schema, and file size limits.
- Metrics & Diagnostics: Failed/slow request counts, distributions, domains.
- Private storage lifecycle: Upload, list, retrieve, filter entries, delete.
"""

import json
import uuid
import pytest
from app.models import User, UserRole
from app.security import hash_password
from app.services import har_service


# ---------------------------------------------------------------------------
# Test Data Fixtures
# ---------------------------------------------------------------------------

def _make_user(db, role=UserRole.STAFF):
    u = User(
        email=f"har_{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("password123"),
        full_name="HAR Tester",
        role=role,
        is_active=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _sample_har_dict():
    """Generates a sample HAR 1.2 dictionary with sensitive headers and tokens."""
    return {
        "log": {
            "version": "1.2",
            "creator": {"name": "Browser DevTools", "version": "120.0"},
            "entries": [
                {
                    "startedDateTime": "2026-10-31T10:00:00.000Z",
                    "time": 120.5,
                    "request": {
                        "method": "GET",
                        "url": "https://api.example.com/v1/users?token=supersecret123&page=1&apikey=key999",
                        "headers": [
                            {"name": "Host", "value": "api.example.com"},
                            {"name": "Authorization", "value": "Bearer jwt-secret-token-xyz"},
                            {"name": "Cookie", "value": "session=abc12345; auth_user=john"},
                            {"name": "Accept", "value": "application/json"},
                        ],
                        "cookies": [{"name": "session", "value": "abc12345"}],
                    },
                    "response": {
                        "status": 200,
                        "statusText": "OK",
                        "headers": [
                            {"name": "Content-Type", "value": "application/json"},
                            {"name": "Set-Cookie", "value": "session=fresh_session; Path=/"},
                        ],
                        "cookies": [{"name": "session", "value": "fresh_session"}],
                        "content": {
                            "size": 1024,
                            "mimeType": "application/json",
                            "text": '{"raw_secret_data": "THIS_SHOULD_NEVER_BE_STORED"}',
                        },
                    },
                },
                {
                    "startedDateTime": "2026-10-31T10:00:01.000Z",
                    "time": 2500.0,  # Slow request (>1000ms)
                    "request": {
                        "method": "POST",
                        "url": "https://billing.example.com/charges?client_secret=topsecret",
                        "headers": [
                            {"name": "X-Api-Key", "value": "secret-api-key-999"},
                            {"name": "Content-Type", "value": "application/json"},
                        ],
                        "postData": {
                            "mimeType": "application/json",
                            "text": '{"card": "4111222233334444"}',
                        },
                    },
                    "response": {
                        "status": 500,  # Failed request
                        "statusText": "Internal Server Error",
                        "headers": [],
                        "content": {
                            "size": 250,
                            "mimeType": "application/json",
                            "text": '{"error": "Database lock timeout"}',
                        },
                    },
                },
                {
                    "startedDateTime": "2026-10-31T10:00:02.000Z",
                    "time": 85.0,
                    "request": {
                        "method": "GET",
                        "url": "https://cdn.example.com/logo.png",
                        "headers": [
                            {"name": "Accept", "value": "image/png"},
                        ],
                    },
                    "response": {
                        "status": 404,  # Failed request
                        "statusText": "Not Found",
                        "headers": [],
                        "content": {"size": 0, "mimeType": "text/html"},
                    },
                },
            ],
        }
    }


# ---------------------------------------------------------------------------
# Access Control Tests
# ---------------------------------------------------------------------------

class TestHARAccessControl:
    def test_unauthenticated_upload_rejected(self, client):
        resp = client.post("/api/har/upload-json", json={"har_content": _sample_har_dict()})
        assert resp.status_code == 401

    def test_customer_can_submit_to_developer_queue(self, client, db_session, auth_headers):
        cust = _make_user(db_session, UserRole.CUSTOMER)
        report_id = None
        try:
            resp = client.post(
                "/api/har/upload-json",
                json={"har_content": _sample_har_dict()},
                headers=auth_headers(cust),
            )
            assert resp.status_code == 201
            report_id = resp.json()["report_id"]
            assert resp.json()["submission_status"] == "submitted_to_developers"
            assert resp.json()["developer_status"] == "new"
        finally:
            if report_id:
                har_service.delete_report(report_id)
            db_session.query(User).filter(User.id == cust.id).delete()
            db_session.commit()

    def test_customer_list_is_limited_to_own_reports(self, client, db_session, auth_headers):
        cust = _make_user(db_session, UserRole.CUSTOMER)
        report_id = None
        try:
            upload = client.post(
                "/api/har/upload-json",
                json={"har_content": _sample_har_dict()},
                headers=auth_headers(cust),
            )
            report_id = upload.json()["report_id"]
            resp = client.get("/api/har/reports", headers=auth_headers(cust))
            assert resp.status_code == 200
            assert all(item["uploader_id"] == str(cust.id) for item in resp.json())
            assert any(item["report_id"] == report_id for item in resp.json())
        finally:
            if report_id:
                har_service.delete_report(report_id)
            db_session.query(User).filter(User.id == cust.id).delete()
            db_session.commit()


# ---------------------------------------------------------------------------
# Validation Tests
# ---------------------------------------------------------------------------

class TestHARValidation:
    def test_invalid_json_upload_rejected(self, client, db_session, auth_headers):
        staff = _make_user(db_session, UserRole.STAFF)
        try:
            resp = client.post(
                "/api/har/upload",
                files={"file": ("corrupt.har", b"THIS IS NOT VALID JSON", "application/json")},
                headers=auth_headers(staff),
            )
            assert resp.status_code == 400
            assert "Invalid JSON" in resp.json()["detail"]
        finally:
            db_session.query(User).filter(User.id == staff.id).delete()
            db_session.commit()

    def test_missing_log_object_rejected(self, client, db_session, auth_headers):
        staff = _make_user(db_session, UserRole.STAFF)
        try:
            resp = client.post(
                "/api/har/upload-json",
                json={"har_content": {"not_a_har": True}},
                headers=auth_headers(staff),
            )
            assert resp.status_code == 400
            assert "log" in resp.json()["detail"].lower()
        finally:
            db_session.query(User).filter(User.id == staff.id).delete()
            db_session.commit()

    def test_oversized_file_rejected(self, client, db_session, auth_headers, monkeypatch):
        staff = _make_user(db_session, UserRole.STAFF)
        # Patch MAX_HAR_SIZE_BYTES to 100 bytes for fast test
        monkeypatch.setattr(har_service, "MAX_HAR_SIZE_BYTES", 100)
        try:
            large_content = b"x" * 200
            resp = client.post(
                "/api/har/upload",
                files={"file": ("large.har", large_content, "application/json")},
                headers=auth_headers(staff),
            )
            assert resp.status_code == 413
        finally:
            db_session.query(User).filter(User.id == staff.id).delete()
            db_session.commit()


# ---------------------------------------------------------------------------
# Redaction & Security Tests
# ---------------------------------------------------------------------------

class TestHARRedactionAndSecurity:
    def test_sensitive_headers_redacted(self, client, db_session, auth_headers):
        staff = _make_user(db_session, UserRole.STAFF)
        report_id = None
        try:
            resp = client.post(
                "/api/har/upload-json",
                json={"har_content": _sample_har_dict()},
                headers=auth_headers(staff),
            )
            assert resp.status_code == 201
            data = resp.json()
            report_id = data["report_id"]

            assert data["total_redacted_count"] > 0

            # Inspect entry 1
            entry1 = data["entries"][0]
            headers1 = entry1["headers_sample"]
            assert headers1.get("Authorization") == "[REDACTED]"
            assert headers1.get("Cookie") == "[REDACTED]"
            assert "Bearer" not in str(headers1)
            assert "jwt-secret-token" not in str(headers1)

            # Inspect entry 2
            entry2 = data["entries"][1]
            headers2 = entry2["headers_sample"]
            assert headers2.get("X-Api-Key") == "[REDACTED]"
            assert "secret-api-key" not in str(headers2)
        finally:
            if report_id:
                try:
                    har_service.delete_report(report_id)
                except Exception:
                    pass
            db_session.query(User).filter(User.id == staff.id).delete()
            db_session.commit()

    def test_sensitive_query_parameters_redacted(self, client, db_session, auth_headers):
        staff = _make_user(db_session, UserRole.STAFF)
        report_id = None
        try:
            resp = client.post(
                "/api/har/upload-json",
                json={"har_content": _sample_har_dict()},
                headers=auth_headers(staff),
            )
            assert resp.status_code == 201
            data = resp.json()
            report_id = data["report_id"]

            entry1 = data["entries"][0]
            assert "supersecret123" not in entry1["url"]
            assert "key999" not in entry1["url"]
            assert "token=%5BREDACTED%5D" in entry1["url"] or "token=[REDACTED]" in entry1["url"]
            assert "page=1" in entry1["url"]

            entry2 = data["entries"][1]
            assert "topsecret" not in entry2["url"]
            assert "client_secret=%5BREDACTED%5D" in entry2["url"] or "client_secret=[REDACTED]" in entry2["url"]
        finally:
            if report_id:
                try:
                    har_service.delete_report(report_id)
                except Exception:
                    pass
            db_session.query(User).filter(User.id == staff.id).delete()
            db_session.commit()

    def test_raw_bodies_never_stored(self, client, db_session, auth_headers):
        staff = _make_user(db_session, UserRole.STAFF)
        report_id = None
        try:
            resp = client.post(
                "/api/har/upload-json",
                json={"har_content": _sample_har_dict()},
                headers=auth_headers(staff),
            )
            assert resp.status_code == 201
            raw_text = resp.text
            report_id = resp.json()["report_id"]

            # Neither response bodies nor postData bodies should be in output
            assert "THIS_SHOULD_NEVER_BE_STORED" not in raw_text
            assert "4111222233334444" not in raw_text
            assert "Database lock timeout" not in raw_text
        finally:
            if report_id:
                try:
                    har_service.delete_report(report_id)
                except Exception:
                    pass
            db_session.query(User).filter(User.id == staff.id).delete()
            db_session.commit()


# ---------------------------------------------------------------------------
# Diagnostics & Filtering Tests
# ---------------------------------------------------------------------------

class TestHARDiagnosticsAndLifecycle:
    def test_diagnostics_metrics_calculation(self, client, db_session, auth_headers):
        staff = _make_user(db_session, UserRole.STAFF)
        report_id = None
        try:
            har_bytes = json.dumps(_sample_har_dict()).encode("utf-8")
            resp = client.post(
                "/api/har/upload?slow_threshold_ms=1000",
                files={"file": ("network.har", har_bytes, "application/json")},
                headers=auth_headers(staff),
            )
            assert resp.status_code == 201
            data = resp.json()
            report_id = data["report_id"]

            assert data["total_requests"] == 3
            assert data["failed_requests"] == 2  # Status 500 and 404
            assert data["slow_requests"] == 1    # 2500ms > 1000ms
            assert data["status_distribution"] == {"200": 1, "500": 1, "404": 1}
            assert data["method_distribution"] == {"GET": 2, "POST": 1}
            assert "api.example.com" in data["domains"]
            assert "billing.example.com" in data["domains"]
            assert "cdn.example.com" in data["domains"]
            assert "disclaimer" in data
        finally:
            if report_id:
                try:
                    har_service.delete_report(report_id)
                except Exception:
                    pass
            db_session.query(User).filter(User.id == staff.id).delete()
            db_session.commit()

    def test_report_listing_and_filtering(self, client, db_session, auth_headers):
        staff = _make_user(db_session, UserRole.STAFF)
        report_id = None
        try:
            resp_up = client.post(
                "/api/har/upload-json",
                json={"har_content": _sample_har_dict()},
                headers=auth_headers(staff),
            )
            assert resp_up.status_code == 201
            report_id = resp_up.json()["report_id"]

            # List reports
            resp_list = client.get("/api/har/reports", headers=auth_headers(staff))
            assert resp_list.status_code == 200
            reports = resp_list.json()
            matching = [r for r in reports if r["report_id"] == report_id]
            assert len(matching) == 1

            # Get single report detail
            resp_detail = client.get(f"/api/har/reports/{report_id}", headers=auth_headers(staff))
            assert resp_detail.status_code == 200
            assert resp_detail.json()["report_id"] == report_id

            # Filter failed only
            resp_failed = client.get(
                f"/api/har/reports/{report_id}/entries?failed_only=true",
                headers=auth_headers(staff),
            )
            assert resp_failed.status_code == 200
            failed_entries = resp_failed.json()["entries"]
            assert len(failed_entries) == 2
            for fe in failed_entries:
                assert fe["is_failed"] is True

            # Filter slow only
            resp_slow = client.get(
                f"/api/har/reports/{report_id}/entries?slow_only=true",
                headers=auth_headers(staff),
            )
            assert resp_slow.status_code == 200
            slow_entries = resp_slow.json()["entries"]
            assert len(slow_entries) == 1
            assert slow_entries[0]["method"] == "POST"

            # Filter by host
            resp_host = client.get(
                f"/api/har/reports/{report_id}/entries?host=cdn.example.com",
                headers=auth_headers(staff),
            )
            assert resp_host.status_code == 200
            assert len(resp_host.json()["entries"]) == 1

            # Delete report
            resp_del = client.delete(f"/api/har/reports/{report_id}", headers=auth_headers(staff))
            assert resp_del.status_code == 200

            # Verify 404 after deletion
            resp_after = client.get(f"/api/har/reports/{report_id}", headers=auth_headers(staff))
            assert resp_after.status_code == 404
            report_id = None  # Already cleaned up
        finally:
            if report_id:
                try:
                    har_service.delete_report(report_id)
                except Exception:
                    pass
            db_session.query(User).filter(User.id == staff.id).delete()
            db_session.commit()
