"""
schemas.py — Pydantic models for LLM extraction and API request/response contracts.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# LLM extraction models (used with instructor)
# ---------------------------------------------------------------------------

class CobraElectionExtraction(BaseModel):
    """Extracted from a photo of the COBRA election/benefits table page."""

    medical_plan_name: str | None = None
    medical_carrier: str | None = None
    medical_monthly_premium: float | None = None
    dental_plan_name: str | None = None
    dental_carrier: str | None = None
    dental_monthly_premium: float | None = None


class InsuranceCardExtraction(BaseModel):
    """Extracted from front + back of an insurance card."""

    carrier: str | None = None
    plan_type: str | None = None  # HMO, PPO, EPO, POS
    member_id: str | None = None
    group_number: str | None = None
    pcp_copay: float | None = None
    specialist_copay: float | None = None
    er_copay: float | None = None
    urgent_care_copay: float | None = None
    deductible_individual: float | None = None
    deductible_family: float | None = None
    oop_max_individual: float | None = None
    oop_max_family: float | None = None
    rx_tier1_copay: float | None = None   # generic
    rx_tier2_copay: float | None = None   # preferred brand
    rx_tier3_copay: float | None = None   # non-preferred brand
    rx_tier4_copay: float | None = None   # specialty
    inpatient_copay: float | None = None
    coinsurance_pct: float | None = None


# ---------------------------------------------------------------------------
# API request model — POST /compare
# ---------------------------------------------------------------------------

class CompareRequest(BaseModel):
    # Demographics
    age: int = Field(ge=1, le=120)
    zip_code: str = Field(pattern=r"^\d{5}$")

    # Page 1 — COBRA plan info
    medical_plan_name: str | None = None
    medical_carrier: str | None = None
    medical_monthly_premium: float | None = None
    dental_plan_name: str | None = None
    dental_carrier: str | None = None
    dental_monthly_premium: float | None = None

    # Page 2 — Insurance card data (None if skipped)
    card_data: InsuranceCardExtraction | None = None

    # Page 3 — Freeform notes
    medical_notes: str | None = None
    dental_notes: str | None = None

    turnstile_token: str


# ---------------------------------------------------------------------------
# API response models — POST /compare
# ---------------------------------------------------------------------------

class BenefitComparison(BaseModel):
    """Single row in the side-by-side benefit comparison."""

    service: str           # e.g. "PCP Visit", "Deductible"
    cobra_value: str       # e.g. "$30 copay", "$2,000", "Unknown"
    alternative_value: str
    verdict: Literal["better", "worse", "similar", "unknown"]


class SuggestedPlan(BaseModel):
    plan_id: str
    plan_name: str
    carrier: str
    carrier_phone: str | None = None
    carrier_website: str | None = None
    plan_type: str
    metal_level: str
    monthly_premium: float
    deductible: float | None = None
    oop_max: float | None = None
    monthly_savings: float  # cobra_premium - this_premium
    comparison: list[BenefitComparison]
    reasoning: str  # why this plan was selected


class SuggestedDentalPlan(BaseModel):
    plan_id: str
    plan_name: str
    carrier: str
    carrier_phone: str | None = None
    carrier_website: str | None = None
    plan_type: str
    dental_level: str  # "high" or "low"
    monthly_premium: float
    annual_max: float | None = None
    deductible: float | None = None
    monthly_savings: float
    comparison: list[BenefitComparison]
    reasoning: str


class CompareResponse(BaseModel):
    cobra_summary: dict  # echo back understanding of current plan
    medical_suggestions: list[SuggestedPlan]  # exactly 3
    dental_suggestions: list[SuggestedDentalPlan]  # 0-3
    overall_summary: str  # 3-5 sentence neutral summary
