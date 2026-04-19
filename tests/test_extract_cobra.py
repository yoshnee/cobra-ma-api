"""
test_extract_cobra.py — Tests for the POST /extract-cobra endpoint.
"""

from io import BytesIO
from unittest.mock import patch, MagicMock

import pytest

from src.routes.extract_cobra import _build_content_block


# ---------------------------------------------------------------------------
# Unit tests — content block builder
# ---------------------------------------------------------------------------

class TestBuildContentBlock:
    def test_image_jpeg(self):
        block = _build_content_block("abc123", "image/jpeg")
        assert block["type"] == "image"
        assert block["source"]["media_type"] == "image/jpeg"
        assert block["source"]["data"] == "abc123"

    def test_image_png(self):
        block = _build_content_block("abc123", "image/png")
        assert block["type"] == "image"
        assert block["source"]["media_type"] == "image/png"

    def test_pdf(self):
        block = _build_content_block("abc123", "application/pdf")
        assert block["type"] == "document"
        assert block["source"]["media_type"] == "application/pdf"

    def test_webp(self):
        block = _build_content_block("abc123", "image/webp")
        assert block["type"] == "image"


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------

class TestExtractCobraEndpoint:
    def test_rejects_unsupported_file_type(self, client, mock_turnstile):
        response = client.post(
            "/extract-cobra",
            files={"file": ("test.txt", BytesIO(b"hello"), "text/plain")},
            data={"turnstile_token": "test-token"},
        )
        assert response.status_code == 400
        assert "Unsupported file type" in response.json()["detail"]

    def test_rejects_empty_file(self, client, mock_turnstile):
        response = client.post(
            "/extract-cobra",
            files={"file": ("test.jpg", BytesIO(b""), "image/jpeg")},
            data={"turnstile_token": "test-token"},
        )
        assert response.status_code == 400
        assert "Empty file" in response.json()["detail"]

    def test_successful_extraction(self, client, mock_turnstile, mock_cobra_extraction):
        with patch("src.routes.extract_cobra.ANTHROPIC_API_KEY", "test-key"), \
             patch("src.routes.extract_cobra.get_instructor_client") as mock_client:
            mock_client.return_value.messages.create.return_value = mock_cobra_extraction

            response = client.post(
                "/extract-cobra",
                files={"file": ("cobra.jpg", BytesIO(b"fake-image-data"), "image/jpeg")},
                data={"turnstile_token": "test-token"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["medical_plan_name"] == "Aetna Choice POS II"
        assert data["medical_monthly_premium"] == 650.0
        assert data["dental_plan_name"] == "Delta Dental PPO"

    def test_returns_null_for_missing_fields(self, client, mock_turnstile):
        from src.schemas import CobraElectionExtraction

        partial = CobraElectionExtraction(
            medical_plan_name="Test Plan",
            medical_monthly_premium=500.0,
        )

        with patch("src.routes.extract_cobra.ANTHROPIC_API_KEY", "test-key"), \
             patch("src.routes.extract_cobra.get_instructor_client") as mock_client:
            mock_client.return_value.messages.create.return_value = partial

            response = client.post(
                "/extract-cobra",
                files={"file": ("cobra.pdf", BytesIO(b"fake-pdf"), "application/pdf")},
                data={"turnstile_token": "test-token"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["medical_plan_name"] == "Test Plan"
        assert data["medical_carrier"] is None
        assert data["dental_plan_name"] is None

    def test_rejects_when_no_api_key(self, client, mock_turnstile):
        with patch("src.routes.extract_cobra.ANTHROPIC_API_KEY", ""):
            response = client.post(
                "/extract-cobra",
                files={"file": ("cobra.jpg", BytesIO(b"fake"), "image/jpeg")},
                data={"turnstile_token": "test-token"},
            )
        assert response.status_code == 500
        assert "ANTHROPIC_API_KEY" in response.json()["detail"]
