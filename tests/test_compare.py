"""
test_compare.py — Tests for comparison logic and POST /compare endpoint.
"""

from unittest.mock import patch, MagicMock

import pytest

from src.routes.compare import (
    _adjacent_tiers,
    _build_cobra_summary,
    _build_compare_prompt,
    _infer_metal_level,
    _resolve_plan_type,
)
from src.schemas import CompareRequest, InsuranceCardExtraction


# ---------------------------------------------------------------------------
# Unit tests — metal tier inference
# ---------------------------------------------------------------------------

class TestInferMetalLevel:
    def test_low_premium_is_bronze(self):
        assert _infer_metal_level(150.0) == "bronze"

    def test_mid_premium_is_silver(self):
        assert _infer_metal_level(350.0) == "silver"

    def test_high_premium_is_gold(self):
        assert _infer_metal_level(500.0) == "gold"

    def test_very_high_premium_is_platinum(self):
        assert _infer_metal_level(800.0) == "platinum"

    def test_boundary_200_is_silver(self):
        assert _infer_metal_level(200.0) == "silver"

    def test_boundary_400_is_gold(self):
        assert _infer_metal_level(400.0) == "gold"

    def test_boundary_600_is_platinum(self):
        assert _infer_metal_level(600.0) == "platinum"


# ---------------------------------------------------------------------------
# Unit tests — adjacent tiers
# ---------------------------------------------------------------------------

class TestAdjacentTiers:
    def test_silver_has_bronze_and_gold(self):
        tiers = _adjacent_tiers("silver")
        assert "silver" in tiers
        assert "bronze" in tiers
        assert "gold" in tiers
        assert len(tiers) == 3

    def test_catastrophic_has_no_lower(self):
        tiers = _adjacent_tiers("catastrophic")
        assert "catastrophic" in tiers
        assert "bronze" in tiers
        assert len(tiers) == 2

    def test_platinum_has_no_higher(self):
        tiers = _adjacent_tiers("platinum")
        assert "platinum" in tiers
        assert "gold" in tiers
        assert len(tiers) == 2


# ---------------------------------------------------------------------------
# Unit tests — cobra summary builder
# ---------------------------------------------------------------------------

class TestBuildCobraSummary:
    def test_includes_medical_fields(self):
        req = CompareRequest(
            age=35,
            zip_code="02101",
            medical_plan_name="Test Plan",
            medical_carrier="Test Carrier",
            medical_monthly_premium=600.0,
            turnstile_token="test",
        )
        summary = _build_cobra_summary(req)
        assert summary["medical_plan_name"] == "Test Plan"
        assert summary["medical_carrier"] == "Test Carrier"
        assert summary["medical_monthly_premium"] == 600.0

    def test_excludes_none_fields(self):
        req = CompareRequest(
            age=35,
            zip_code="02101",
            medical_monthly_premium=600.0,
            turnstile_token="test",
        )
        summary = _build_cobra_summary(req)
        assert "medical_plan_name" not in summary
        assert "dental_plan_name" not in summary

    def test_includes_card_data(self):
        card = InsuranceCardExtraction(pcp_copay=30.0, specialist_copay=60.0)
        req = CompareRequest(
            age=35,
            zip_code="02101",
            medical_monthly_premium=600.0,
            card_data=card,
            turnstile_token="test",
        )
        summary = _build_cobra_summary(req)
        assert "card_benefits" in summary
        assert summary["card_benefits"]["pcp_copay"] == 30.0

    def test_no_card_data_when_skipped(self):
        req = CompareRequest(
            age=35,
            zip_code="02101",
            medical_monthly_premium=600.0,
            turnstile_token="test",
        )
        summary = _build_cobra_summary(req)
        assert "card_benefits" not in summary


# ---------------------------------------------------------------------------
# Unit tests — prompt construction
# ---------------------------------------------------------------------------

class TestBuildComparePrompt:
    def _make_request(self, **overrides):
        defaults = dict(
            age=35,
            zip_code="02101",
            medical_plan_name="Aetna Choice POS II",
            medical_carrier="Aetna",
            medical_monthly_premium=650.0,
            turnstile_token="test",
        )
        defaults.update(overrides)
        return CompareRequest(**defaults)

    def test_includes_plan_name_and_premium(self, sample_medical_candidates):
        req = self._make_request()
        prompt = _build_compare_prompt(req, sample_medical_candidates, [], "PPO")
        assert "Aetna Choice POS II" in prompt
        assert "$650" in prompt

    def test_includes_freeform_notes(self, sample_medical_candidates):
        req = self._make_request(medical_notes="$0 copay for therapy sessions")
        prompt = _build_compare_prompt(req, sample_medical_candidates, [], "PPO")
        assert "$0 copay for therapy sessions" in prompt

    def test_notes_card_skipped(self, sample_medical_candidates):
        req = self._make_request(card_data=None)
        prompt = _build_compare_prompt(req, sample_medical_candidates, [], "PPO")
        assert "skipped" in prompt.lower()

    def test_includes_card_data(self, sample_medical_candidates):
        card = InsuranceCardExtraction(pcp_copay=30.0, deductible_individual=2000.0)
        req = self._make_request(card_data=card)
        prompt = _build_compare_prompt(req, sample_medical_candidates, [], "PPO")
        assert "pcp_copay" in prompt
        assert "2000" in prompt

    def test_includes_candidate_plans(self, sample_medical_candidates):
        req = self._make_request()
        prompt = _build_compare_prompt(req, sample_medical_candidates, [], "PPO")
        assert "BMC HealthNet Silver" in prompt
        assert "Tufts Health Silver PPO" in prompt
        assert "Harvard Pilgrim Gold HMO" in prompt

    def test_follows_four_part_structure(self, sample_medical_candidates):
        req = self._make_request()
        prompt = _build_compare_prompt(req, sample_medical_candidates, [], "PPO")
        assert "[CONTEXT]" in prompt
        assert "[INSTRUCTIONS]" in prompt
        assert "[INPUT DATA]" in prompt
        assert "[OUTPUT]" in prompt

    def test_instructs_savings_priority(self, sample_medical_candidates):
        req = self._make_request()
        prompt = _build_compare_prompt(req, sample_medical_candidates, [], "PPO")
        assert "SAVINGS FIRST" in prompt

    def test_instructs_protect_benefits(self, sample_medical_candidates):
        req = self._make_request()
        prompt = _build_compare_prompt(req, sample_medical_candidates, [], "PPO")
        assert "PROTECT KEY BENEFITS" in prompt

    def test_instructs_respect_freeform(self, sample_medical_candidates):
        req = self._make_request()
        prompt = _build_compare_prompt(req, sample_medical_candidates, [], "PPO")
        assert "RESPECT FREEFORM NOTES" in prompt

    def test_dental_notes_included(self, sample_medical_candidates):
        req = self._make_request(dental_notes="2 cleanings per year covered")
        prompt = _build_compare_prompt(req, sample_medical_candidates, [], "PPO")
        assert "2 cleanings per year covered" in prompt

    def test_includes_plan_type(self, sample_medical_candidates):
        req = self._make_request()
        prompt = _build_compare_prompt(req, sample_medical_candidates, [], "PPO")
        assert "PPO" in prompt
        assert "Plan Type: PPO" in prompt

    def test_includes_dental_candidates_when_premium_provided(self, sample_medical_candidates):
        dental_candidates = [{"plan_id": "d1", "plan_name": "Delta PPO"}]
        req = self._make_request(dental_monthly_premium=45.0)
        prompt = _build_compare_prompt(req, sample_medical_candidates, dental_candidates, "PPO")
        assert "Delta PPO" in prompt
        assert "Dental" in prompt

    def test_no_dental_section_without_premium(self, sample_medical_candidates):
        dental_candidates = [{"plan_id": "d1", "plan_name": "Delta PPO"}]
        req = self._make_request()
        prompt = _build_compare_prompt(req, sample_medical_candidates, dental_candidates, None)
        assert "No dental comparison requested" in prompt


# ---------------------------------------------------------------------------
# Unit tests — plan type resolution
# ---------------------------------------------------------------------------

class TestResolvePlanType:
    def test_from_card_data(self):
        req = CompareRequest(
            age=35, zip_code="02101", turnstile_token="test",
            card_data=InsuranceCardExtraction(plan_type="PPO"),
        )
        assert _resolve_plan_type(req) == "PPO"

    def test_pos_maps_to_ppo(self):
        req = CompareRequest(
            age=35, zip_code="02101", turnstile_token="test",
            card_data=InsuranceCardExtraction(plan_type="POS"),
        )
        assert _resolve_plan_type(req) == "PPO"

    def test_from_plan_name(self):
        req = CompareRequest(
            age=35, zip_code="02101", turnstile_token="test",
            medical_plan_name="Aetna Choice PPO Plus",
        )
        assert _resolve_plan_type(req) == "PPO"

    def test_returns_none_when_unknown(self):
        req = CompareRequest(
            age=35, zip_code="02101", turnstile_token="test",
        )
        assert _resolve_plan_type(req) is None


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------

class TestCompareEndpoint:
    def test_rejects_missing_premium(self, client, mock_turnstile):
        response = client.post(
            "/compare",
            json={
                "age": 35,
                "zip_code": "02101",
                "turnstile_token": "test",
            },
        )
        assert response.status_code == 400

    def test_rejects_invalid_zip(self, client, mock_turnstile):
        response = client.post(
            "/compare",
            json={
                "age": 35,
                "zip_code": "99999",
                "medical_monthly_premium": 650.0,
                "turnstile_token": "test",
            },
        )
        assert response.status_code == 400
        assert "Massachusetts" in response.json()["detail"]

    def test_successful_compare(
        self, client, mock_turnstile, sample_medical_candidates, sample_compare_response
    ):
        with patch("src.routes.compare._get_medical_candidates", return_value=sample_medical_candidates), \
             patch("src.routes.compare._get_dental_candidates", return_value=[]), \
             patch("src.routes.compare.get_engine"), \
             patch("src.routes.compare.ANTHROPIC_API_KEY", "test-key"), \
             patch("src.routes.compare.get_instructor_client") as mock_llm:
            mock_llm.return_value.messages.create.return_value = sample_compare_response

            response = client.post(
                "/compare",
                json={
                    "age": 35,
                    "zip_code": "02101",
                    "medical_plan_name": "Aetna Choice POS II",
                    "medical_carrier": "Aetna",
                    "medical_monthly_premium": 650.0,
                    "turnstile_token": "test",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert len(data["medical_suggestions"]) == 3
        assert data["medical_suggestions"][0]["plan_name"] == "BMC HealthNet Silver"
        assert data["medical_suggestions"][0]["monthly_savings"] == 230.0
        assert "dental_suggestions" in data
        assert data["overall_summary"] is not None

    def test_cobra_summary_is_injected(
        self, client, mock_turnstile, sample_medical_candidates, sample_compare_response
    ):
        """Verify cobra_summary is built server-side, not from LLM output."""
        with patch("src.routes.compare._get_medical_candidates", return_value=sample_medical_candidates), \
             patch("src.routes.compare._get_dental_candidates", return_value=[]), \
             patch("src.routes.compare.get_engine"), \
             patch("src.routes.compare.ANTHROPIC_API_KEY", "test-key"), \
             patch("src.routes.compare.get_instructor_client") as mock_llm:
            mock_llm.return_value.messages.create.return_value = sample_compare_response

            response = client.post(
                "/compare",
                json={
                    "age": 40,
                    "zip_code": "02101",
                    "medical_plan_name": "My COBRA Plan",
                    "medical_monthly_premium": 700.0,
                    "turnstile_token": "test",
                },
            )

        data = response.json()
        # cobra_summary should reflect the request, not the LLM's response
        assert data["cobra_summary"]["medical_plan_name"] == "My COBRA Plan"
        assert data["cobra_summary"]["medical_monthly_premium"] == 700.0
