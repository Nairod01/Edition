"""
Tests for FastAPI endpoints in backend/routers/jobs.py.
Uses TestClient with mocked SQLAlchemy sessions — no real DB.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime

# Pre-mock optional heavy dependencies that may not be installed in test env
import unittest.mock as _mock
_docx_mock = _mock.MagicMock()
sys.modules.setdefault("docx", _docx_mock)
sys.modules.setdefault("docx.document", _docx_mock)
sys.modules.setdefault("docx.enum", _docx_mock)
sys.modules.setdefault("docx.enum.text", _docx_mock)
sys.modules.setdefault("docx.oxml", _docx_mock)
sys.modules.setdefault("docx.oxml.ns", _docx_mock)
sys.modules.setdefault("docx.shared", _docx_mock)

from fastapi.testclient import TestClient


# ── App fixture ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    """
    Create a TestClient for the FastAPI app with DB init suppressed.
    init_db is a no-op in tests to avoid touching the real SQLite file.
    Auth is bypassed with a fake admin user (les routes exigent un JWT
    depuis l'ajout du multi-utilisateurs — admin passe tous les checks
    d'ownership).
    """
    with patch("backend.database.init_db", return_value=None):
        from backend.main import app
        from backend.auth import get_current_user, get_admin_user

        fake_admin = MagicMock()
        fake_admin.id = "test-user-id"
        fake_admin.email = "test@example.com"
        fake_admin.role = "admin"
        fake_admin.is_active = True
        fake_admin.monthly_limit_usd = 0.0
        fake_admin.current_month_spend_usd = 0.0

        app.dependency_overrides[get_current_user] = lambda: fake_admin
        app.dependency_overrides[get_admin_user] = lambda: fake_admin

        # Délégué DB : FastAPI a capturé la référence originale de get_db au
        # moment du Depends(), donc `patch("backend.routers.jobs.get_db")` ne
        # prend jamais. Ce délégué relit l'attribut du module À CHAQUE requête,
        # rendant les patchs des tests effectifs — et sans patch, on refuse de
        # toucher la vraie base locale.
        import backend.routers.jobs as _jobs_module
        from backend.database import get_db as _real_get_db

        def _delegating_db():
            source = _jobs_module.get_db
            if source is _real_get_db:
                raise RuntimeError(
                    "Test sans mock DB : patcher backend.routers.jobs.get_db "
                    "pour éviter de toucher la base SQLite locale."
                )
            return next(source())

        app.dependency_overrides[_real_get_db] = _delegating_db
        yield TestClient(app, raise_server_exceptions=False)
        app.dependency_overrides.clear()


# ── Mock Job builder ───────────────────────────────────────────────────────────

def make_mock_job(
    job_id="test-job-123",
    filename="test.pdf",
    status="done",
    progress=100,
    progress_label="Terminé",
    pages_count=10,
    word_count=500,
    estimated_cost_usd=0.05,
    corrections_count=3,
    error_message=None,
    doc_type="autre",
    annotated_count=3,
    h_not_annotated_count=0,
    output_pdf_path=None,
    created_at=None,
):
    job = MagicMock()
    job.id = job_id
    job.filename = filename
    job.status = status
    job.progress = progress
    job.progress_label = progress_label
    job.pages_count = pages_count
    job.word_count = word_count
    job.estimated_cost_usd = estimated_cost_usd
    job.corrections_count = corrections_count
    job.error_message = error_message
    job.doc_type = doc_type
    job.annotated_count = annotated_count
    job.h_not_annotated_count = h_not_annotated_count
    job.output_pdf_path = output_pdf_path
    job.created_at = created_at or datetime(2026, 1, 1, 12, 0, 0)
    return job


def make_mock_correction(
    cid="corr-1",
    job_id="test-job-123",
    page_number=0,
    category="A",
    original_text="original",
    corrected_text="corrected",
    description="desc",
    explanation="expl",
    source="",
    annotation_type="StrikeOut",
    confidence="Certain",
):
    c = MagicMock()
    c.id = cid
    c.job_id = job_id
    c.page_number = page_number
    c.category = category
    c.original_text = original_text
    c.corrected_text = corrected_text
    c.description = description
    c.explanation = explanation
    c.source = source
    c.annotation_type = annotation_type
    c.confidence = confidence
    return c


# ── GET /api/jobs/{job_id} ─────────────────────────────────────────────────────

class TestGetJob:

    def test_returns_200_for_existing_job(self, client):
        mock_job = make_mock_job()
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_job
        # Mock the category count query
        mock_db.query.return_value.filter.return_value.group_by.return_value.all.return_value = []

        with patch("backend.routers.jobs.get_db", return_value=iter([mock_db])):
            response = client.get("/api/jobs/test-job-123")

        assert response.status_code == 200

    def test_returns_404_for_missing_job(self, client):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        with patch("backend.routers.jobs.get_db", return_value=iter([mock_db])):
            response = client.get("/api/jobs/nonexistent-id")

        assert response.status_code == 404
        assert "introuvable" in response.json()["detail"]

    def test_response_contains_job_fields(self, client):
        mock_job = make_mock_job(job_id="abc-123", status="done", progress=100)
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_job
        mock_db.query.return_value.filter.return_value.group_by.return_value.all.return_value = [
            ("A", 2), ("B", 1)
        ]

        with patch("backend.routers.jobs.get_db", return_value=iter([mock_db])):
            response = client.get("/api/jobs/abc-123")

        data = response.json()
        assert data["id"] == "abc-123"
        assert data["status"] == "done"
        assert data["progress"] == 100
        assert "corrections_by_category" in data

    def test_corrections_count_summed_from_categories(self, client):
        mock_job = make_mock_job(corrections_count=0)
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_job
        mock_db.query.return_value.filter.return_value.group_by.return_value.all.return_value = [
            ("A", 3), ("B", 2)
        ]

        with patch("backend.routers.jobs.get_db", return_value=iter([mock_db])):
            response = client.get("/api/jobs/test-job-123")

        data = response.json()
        assert data["corrections_count"] == 5

    def test_job_status_pending(self, client):
        mock_job = make_mock_job(status="pending", progress=0)
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_job
        mock_db.query.return_value.filter.return_value.group_by.return_value.all.return_value = []

        with patch("backend.routers.jobs.get_db", return_value=iter([mock_db])):
            response = client.get("/api/jobs/test-job-123")

        assert response.status_code == 200
        assert response.json()["status"] == "pending"

    def test_doc_type_defaults_to_autre(self, client):
        mock_job = make_mock_job(doc_type=None)
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_job
        mock_db.query.return_value.filter.return_value.group_by.return_value.all.return_value = []

        with patch("backend.routers.jobs.get_db", return_value=iter([mock_db])):
            response = client.get("/api/jobs/test-job-123")

        assert response.json()["doc_type"] == "autre"


# ── GET /api/jobs/{job_id}/corrections ────────────────────────────────────────

class TestGetCorrections:

    def test_returns_200_for_existing_job(self, client):
        mock_job = make_mock_job()
        mock_corr = make_mock_correction()
        mock_db = MagicMock()

        # First call: job lookup
        # Second call: corrections query chain
        job_query = MagicMock()
        job_query.filter.return_value.first.return_value = mock_job
        corr_query = MagicMock()
        corr_query.filter.return_value.filter.return_value.order_by.return_value.all.return_value = [mock_corr]
        corr_query.filter.return_value.order_by.return_value.all.return_value = [mock_corr]

        call_count = {"n": 0}

        def side_effect(model):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return job_query
            return corr_query

        mock_db.query.side_effect = side_effect

        with patch("backend.routers.jobs.get_db", return_value=iter([mock_db])):
            response = client.get("/api/jobs/test-job-123/corrections")

        assert response.status_code == 200

    def test_returns_404_for_missing_job(self, client):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        with patch("backend.routers.jobs.get_db", return_value=iter([mock_db])):
            response = client.get("/api/jobs/nonexistent-id/corrections")

        assert response.status_code == 404

    def test_response_structure(self, client):
        mock_job = make_mock_job()
        mock_corr = make_mock_correction(
            cid="c1",
            page_number=0,
            category="A",
            original_text="ancien",
            corrected_text="nouveau",
        )
        mock_db = MagicMock()

        call_count = {"n": 0}
        job_query = MagicMock()
        job_query.filter.return_value.first.return_value = mock_job
        corr_query = MagicMock()
        corr_query.filter.return_value.order_by.return_value.all.return_value = [mock_corr]

        def side_effect(model):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return job_query
            return corr_query

        mock_db.query.side_effect = side_effect

        with patch("backend.routers.jobs.get_db", return_value=iter([mock_db])):
            response = client.get("/api/jobs/test-job-123/corrections")

        data = response.json()
        assert "total" in data
        assert "corrections" in data

    def test_correction_page_is_one_indexed(self, client):
        mock_job = make_mock_job()
        mock_corr = make_mock_correction(page_number=0)
        mock_db = MagicMock()

        call_count = {"n": 0}
        job_query = MagicMock()
        job_query.filter.return_value.first.return_value = mock_job
        corr_query = MagicMock()
        corr_query.filter.return_value.order_by.return_value.all.return_value = [mock_corr]

        def side_effect(model):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return job_query
            return corr_query

        mock_db.query.side_effect = side_effect

        with patch("backend.routers.jobs.get_db", return_value=iter([mock_db])):
            response = client.get("/api/jobs/test-job-123/corrections")

        data = response.json()
        if data["corrections"]:
            # page_number=0 in DB → "page": 1 in response
            assert data["corrections"][0]["page"] == 1

    def test_empty_corrections(self, client):
        mock_job = make_mock_job()
        mock_db = MagicMock()

        call_count = {"n": 0}
        job_query = MagicMock()
        job_query.filter.return_value.first.return_value = mock_job
        corr_query = MagicMock()
        corr_query.filter.return_value.order_by.return_value.all.return_value = []

        def side_effect(model):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return job_query
            return corr_query

        mock_db.query.side_effect = side_effect

        with patch("backend.routers.jobs.get_db", return_value=iter([mock_db])):
            response = client.get("/api/jobs/test-job-123/corrections")

        data = response.json()
        assert data["total"] == 0
        assert data["corrections"] == []


# ── GET /api/jobs ──────────────────────────────────────────────────────────────

class TestListJobs:

    def test_returns_200(self, client):
        mock_db = MagicMock()
        mock_job = make_mock_job()
        mock_db.query.return_value.order_by.return_value.limit.return_value.all.return_value = [mock_job]

        with patch("backend.routers.jobs.get_db", return_value=iter([mock_db])):
            response = client.get("/api/jobs")

        assert response.status_code == 200

    def test_response_has_jobs_key(self, client):
        mock_db = MagicMock()
        mock_db.query.return_value.order_by.return_value.limit.return_value.all.return_value = []

        with patch("backend.routers.jobs.get_db", return_value=iter([mock_db])):
            response = client.get("/api/jobs")

        data = response.json()
        assert "jobs" in data

    def test_empty_db_returns_empty_list(self, client):
        mock_db = MagicMock()
        mock_db.query.return_value.order_by.return_value.limit.return_value.all.return_value = []

        with patch("backend.routers.jobs.get_db", return_value=iter([mock_db])):
            response = client.get("/api/jobs")

        data = response.json()
        assert data["jobs"] == []


# ── GET /health ────────────────────────────────────────────────────────────────

class TestHealthEndpoint:

    def test_health_returns_200(self, client):
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_returns_ok(self, client):
        response = client.get("/health")
        assert response.json() == {"status": "ok"}


# ── GET /api/jobs/{job_id}/download — 404 scenarios ──────────────────────────

class TestDownloadEndpoint:

    def test_404_for_missing_job(self, client):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        with patch("backend.routers.jobs.get_db", return_value=iter([mock_db])):
            response = client.get("/api/jobs/nonexistent/download")

        assert response.status_code == 404

    def test_409_when_job_not_done(self, client):
        mock_job = make_mock_job(status="processing")
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_job

        with patch("backend.routers.jobs.get_db", return_value=iter([mock_db])):
            response = client.get("/api/jobs/test-job-123/download")

        assert response.status_code == 409

    def test_404_when_output_file_missing(self, client):
        mock_job = make_mock_job(status="done", output_pdf_path="/nonexistent/path.pdf")
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_job

        with patch("backend.routers.jobs.get_db", return_value=iter([mock_db])):
            response = client.get("/api/jobs/test-job-123/download")

        assert response.status_code == 404
