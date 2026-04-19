"""
test_extract_card.py — Tests for the POST /extract-card endpoint.
"""

from io import BytesIO
from unittest.mock import patch

import pytest


class TestExtractCardEndpoint:
    def test_rejects_non_image_front(self, client, mock_turnstile):
        response = client.post(
            "/extract-card",
            files={"front": ("doc.pdf", BytesIO(b"pdf-data"), "application/pdf")},
            data={"turnstile_token": "test-token"},
        )
        assert response.status_code == 400
        assert "Front image" in response.json()["detail"]

    def test_rejects_non_image_back(self, client, mock_turnstile):
        response = client.post(
            "/extract-card",
            files={
                "front": ("front.jpg", BytesIO(b"front-data"), "image/jpeg"),
                "back": ("doc.pdf", BytesIO(b"pdf-data"), "application/pdf"),
            },
            data={"turnstile_token": "test-token"},
        )
        assert response.status_code == 400
        assert "Back image" in response.json()["detail"]

    def test_rejects_empty_front(self, client, mock_turnstile):
        response = client.post(
            "/extract-card",
            files={"front": ("front.jpg", BytesIO(b""), "image/jpeg")},
            data={"turnstile_token": "test-token"},
        )
        assert response.status_code == 400
        assert "empty" in response.json()["detail"].lower()

    def test_successful_front_only(self, client, mock_turnstile, mock_card_extraction):
        with patch("src.routes.extract_card.ANTHROPIC_API_KEY", "test-key"), \
             patch("src.routes.extract_card.get_instructor_client") as mock_client:
            mock_client.return_value.messages.create.return_value = mock_card_extraction

            response = client.post(
                "/extract-card",
                files={"front": ("front.jpg", BytesIO(b"front-image"), "image/jpeg")},
                data={"turnstile_token": "test-token"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["carrier"] == "Aetna"
        assert data["pcp_copay"] == 30.0
        assert data["deductible_individual"] == 2000.0

    def test_successful_front_and_back(self, client, mock_turnstile, mock_card_extraction):
        with patch("src.routes.extract_card.ANTHROPIC_API_KEY", "test-key"), \
             patch("src.routes.extract_card.get_instructor_client") as mock_client:
            mock_client.return_value.messages.create.return_value = mock_card_extraction

            response = client.post(
                "/extract-card",
                files={
                    "front": ("front.jpg", BytesIO(b"front-image"), "image/jpeg"),
                    "back": ("back.png", BytesIO(b"back-image"), "image/png"),
                },
                data={"turnstile_token": "test-token"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["rx_tier1_copay"] == 10.0
        assert data["oop_max_family"] == 14000.0

        # Verify both images were sent in a single LLM call
        call_args = mock_client.return_value.messages.create.call_args
        content = call_args.kwargs["messages"][0]["content"]
        image_blocks = [b for b in content if b.get("type") == "image"]
        assert len(image_blocks) == 2

    def test_rejects_when_no_api_key(self, client, mock_turnstile):
        with patch("src.routes.extract_card.ANTHROPIC_API_KEY", ""):
            response = client.post(
                "/extract-card",
                files={"front": ("front.jpg", BytesIO(b"front"), "image/jpeg")},
                data={"turnstile_token": "test-token"},
            )
        assert response.status_code == 500
        assert "ANTHROPIC_API_KEY" in response.json()["detail"]
