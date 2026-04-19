"""
test_schemas.py — Pydantic model validation tests.
"""

import pytest
from pydantic import ValidationError

from src.schemas import (
    BenefitComparison,
    CobraElectionExtraction,
    CompareRequest,
    InsuranceCardExtraction,
    SuggestedPlan,
)


# ---------------------------------------------------------------------------
# CobraElectionExtraction
# ---------------------------------------------------------------------------

class TestCobraElectionExtraction:
    def test_all_fields_populated(self):
        e = CobraElectionExtraction(
            medical_plan_name="Aetna Choice POS II",
            medical_carrier="Aetna",
            medical_monthly_premium=650.0,
            dental_plan_name="Delta PPO",
            dental_carrier="Delta Dental",
            dental_monthly_premium=45.0,
        )
        assert e.medical_monthly_premium == 650.0

    def test_all_fields_null(self):
        e = CobraElectionExtraction()
        assert e.medical_plan_name is None
        assert e.dental_monthly_premium is None

    def test_partial_fields(self):
        e = CobraElectionExtraction(
            medical_plan_name="Test Plan",
            medical_monthly_premium=500.0,
        )
        assert e.medical_carrier is None
        assert e.dental_plan_name is None


# ---------------------------------------------------------------------------
# InsuranceCardExtraction
# ---------------------------------------------------------------------------

class TestInsuranceCardExtraction:
    def test_all_null(self):
        c = InsuranceCardExtraction()
        assert c.carrier is None
        assert c.pcp_copay is None

    def test_with_copays(self):
        c = InsuranceCardExtraction(
            pcp_copay=30.0,
            specialist_copay=60.0,
            er_copay=250.0,
        )
        assert c.pcp_copay == 30.0
        assert c.deductible_individual is None


# ---------------------------------------------------------------------------
# CompareRequest
# ---------------------------------------------------------------------------

class TestCompareRequest:
    def test_valid_request(self):
        r = CompareRequest(
            age=35,
            zip_code="02101",
            medical_plan_name="Test Plan",
            medical_monthly_premium=650.0,
            turnstile_token="test-token",
        )
        assert r.age == 35
        assert r.card_data is None

    def test_rejects_invalid_zip(self):
        with pytest.raises(ValidationError) as exc_info:
            CompareRequest(
                age=35,
                zip_code="abc",
                turnstile_token="test",
            )
        assert "zip_code" in str(exc_info.value)

    def test_rejects_too_short_zip(self):
        with pytest.raises(ValidationError):
            CompareRequest(
                age=35,
                zip_code="0210",
                turnstile_token="test",
            )

    def test_rejects_age_out_of_range(self):
        with pytest.raises(ValidationError):
            CompareRequest(
                age=0,
                zip_code="02101",
                turnstile_token="test",
            )

        with pytest.raises(ValidationError):
            CompareRequest(
                age=121,
                zip_code="02101",
                turnstile_token="test",
            )

    def test_accepts_nested_card_data(self):
        card = InsuranceCardExtraction(pcp_copay=30.0, specialist_copay=60.0)
        r = CompareRequest(
            age=45,
            zip_code="02101",
            card_data=card,
            turnstile_token="test",
        )
        assert r.card_data.pcp_copay == 30.0

    def test_optional_fields_default_none(self):
        r = CompareRequest(
            age=30,
            zip_code="02101",
            turnstile_token="test",
        )
        assert r.medical_plan_name is None
        assert r.dental_plan_name is None
        assert r.medical_notes is None
        assert r.dental_notes is None
        assert r.card_data is None


# ---------------------------------------------------------------------------
# BenefitComparison
# ---------------------------------------------------------------------------

class TestBenefitComparison:
    def test_valid_verdicts(self):
        for verdict in ["better", "worse", "similar", "unknown"]:
            b = BenefitComparison(
                service="PCP Visit",
                cobra_value="$30",
                alternative_value="$25",
                verdict=verdict,
            )
            assert b.verdict == verdict

    def test_rejects_invalid_verdict(self):
        with pytest.raises(ValidationError):
            BenefitComparison(
                service="PCP Visit",
                cobra_value="$30",
                alternative_value="$25",
                verdict="great",
            )


# ---------------------------------------------------------------------------
# SuggestedPlan
# ---------------------------------------------------------------------------

class TestSuggestedPlan:
    def test_monthly_savings_can_be_negative(self):
        p = SuggestedPlan(
            plan_id="p1",
            plan_name="Test",
            carrier="Test Carrier",
            plan_type="HMO",
            metal_level="gold",
            monthly_premium=700.0,
            monthly_savings=-50.0,
            comparison=[],
            reasoning="More expensive but better coverage.",
        )
        assert p.monthly_savings == -50.0

    def test_optional_fields(self):
        p = SuggestedPlan(
            plan_id="p1",
            plan_name="Test",
            carrier="Test Carrier",
            plan_type="HMO",
            metal_level="silver",
            monthly_premium=400.0,
            monthly_savings=200.0,
            comparison=[],
            reasoning="Cheapest option.",
        )
        assert p.carrier_phone is None
        assert p.carrier_website is None
        assert p.deductible is None
        assert p.oop_max is None
