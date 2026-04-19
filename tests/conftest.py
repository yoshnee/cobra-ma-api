"""
conftest.py — Shared test fixtures.

Mocks the LLM client, DB engine, and Turnstile verification so tests
run without credentials.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.schemas import (
    CobraElectionExtraction,
    CompareResponse,
    InsuranceCardExtraction,
    BenefitComparison,
    SuggestedPlan,
    SuggestedDentalPlan,
)


@pytest.fixture
def mock_turnstile():
    """Skip Turnstile verification in all tests.

    Must patch at each import site, not where it's defined.
    """
    with patch("src.routes.extract_cobra.verify_turnstile", new_callable=AsyncMock) as m1, \
         patch("src.routes.extract_card.verify_turnstile", new_callable=AsyncMock) as m2, \
         patch("src.routes.compare.verify_turnstile", new_callable=AsyncMock) as m3:
        yield m1


@pytest.fixture
def mock_cobra_extraction():
    """Return a sample CobraElectionExtraction from the LLM."""
    return CobraElectionExtraction(
        medical_plan_name="Aetna Choice POS II",
        medical_carrier="Aetna",
        medical_monthly_premium=650.00,
        dental_plan_name="Delta Dental PPO",
        dental_carrier="Delta Dental",
        dental_monthly_premium=45.00,
    )


@pytest.fixture
def mock_card_extraction():
    """Return a sample InsuranceCardExtraction from the LLM."""
    return InsuranceCardExtraction(
        carrier="Aetna",
        plan_type="POS",
        member_id="W123456789",
        group_number="GRP001",
        pcp_copay=30.0,
        specialist_copay=60.0,
        er_copay=250.0,
        urgent_care_copay=75.0,
        deductible_individual=2000.0,
        deductible_family=4000.0,
        oop_max_individual=7000.0,
        oop_max_family=14000.0,
        rx_tier1_copay=10.0,
        rx_tier2_copay=35.0,
        rx_tier3_copay=60.0,
        rx_tier4_copay=100.0,
        inpatient_copay=500.0,
        coinsurance_pct=20.0,
    )


@pytest.fixture
def sample_medical_candidates():
    """Sample candidate plans as returned by _get_medical_candidates."""
    return [
        {
            "plan_id": "plan_001",
            "plan_name": "BMC HealthNet Silver",
            "metal_level": "silver",
            "plan_type": "HMO",
            "carrier": "BMC HealthNet",
            "carrier_phone": "1-800-555-0001",
            "carrier_website": "https://bmchealthnet.example.com",
            "monthly_premium": 420.0,
            "deductible": 2000.0,
            "oop_max": 7500.0,
            "services": [
                {
                    "service_name": "Primary Care Visit",
                    "copay_amount": 25.0,
                    "coinsurance_pct": None,
                    "after_deductible": False,
                    "cost_sharing_text": "$25 copay",
                },
            ],
        },
        {
            "plan_id": "plan_002",
            "plan_name": "Tufts Health Silver PPO",
            "metal_level": "silver",
            "plan_type": "PPO",
            "carrier": "Tufts Health",
            "carrier_phone": "1-800-555-0002",
            "carrier_website": "https://tuftshealth.example.com",
            "monthly_premium": 480.0,
            "deductible": 1500.0,
            "oop_max": 6500.0,
            "services": [
                {
                    "service_name": "Primary Care Visit",
                    "copay_amount": 30.0,
                    "coinsurance_pct": None,
                    "after_deductible": False,
                    "cost_sharing_text": "$30 copay",
                },
            ],
        },
        {
            "plan_id": "plan_003",
            "plan_name": "Harvard Pilgrim Gold HMO",
            "metal_level": "gold",
            "plan_type": "HMO",
            "carrier": "Harvard Pilgrim",
            "carrier_phone": "1-800-555-0003",
            "carrier_website": "https://harvardpilgrim.example.com",
            "monthly_premium": 550.0,
            "deductible": 500.0,
            "oop_max": 4000.0,
            "services": [
                {
                    "service_name": "Primary Care Visit",
                    "copay_amount": 15.0,
                    "coinsurance_pct": None,
                    "after_deductible": False,
                    "cost_sharing_text": "$15 copay",
                },
            ],
        },
    ]


@pytest.fixture
def sample_compare_response():
    """Sample CompareResponse as returned by the LLM."""
    return CompareResponse(
        cobra_summary={
            "medical_plan_name": "Aetna Choice POS II",
            "medical_monthly_premium": 650.0,
        },
        medical_suggestions=[
            SuggestedPlan(
                plan_id="plan_001",
                plan_name="BMC HealthNet Silver",
                carrier="BMC HealthNet",
                carrier_phone="1-800-555-0001",
                carrier_website="https://bmchealthnet.example.com",
                plan_type="HMO",
                metal_level="silver",
                monthly_premium=420.0,
                deductible=2000.0,
                oop_max=7500.0,
                monthly_savings=230.0,
                comparison=[
                    BenefitComparison(
                        service="PCP Visit",
                        cobra_value="$30 copay",
                        alternative_value="$25 copay",
                        verdict="better",
                    ),
                ],
                reasoning="Saves $230/month with a lower PCP copay of $25 vs $30.",
            ),
            SuggestedPlan(
                plan_id="plan_002",
                plan_name="Tufts Health Silver PPO",
                carrier="Tufts Health",
                plan_type="PPO",
                metal_level="silver",
                monthly_premium=480.0,
                deductible=1500.0,
                oop_max=6500.0,
                monthly_savings=170.0,
                comparison=[
                    BenefitComparison(
                        service="Deductible",
                        cobra_value="$2,000",
                        alternative_value="$1,500",
                        verdict="better",
                    ),
                ],
                reasoning="Saves $170/month with a lower deductible of $1,500 vs $2,000.",
            ),
            SuggestedPlan(
                plan_id="plan_003",
                plan_name="Harvard Pilgrim Gold HMO",
                carrier="Harvard Pilgrim",
                plan_type="HMO",
                metal_level="gold",
                monthly_premium=550.0,
                deductible=500.0,
                oop_max=4000.0,
                monthly_savings=100.0,
                comparison=[
                    BenefitComparison(
                        service="Deductible",
                        cobra_value="$2,000",
                        alternative_value="$500",
                        verdict="better",
                    ),
                ],
                reasoning="Saves $100/month with significantly lower deductible and OOP max.",
            ),
        ],
        dental_suggestions=[],
        overall_summary="All three alternatives offer monthly savings ranging from $100 to $230.",
    )


@pytest.fixture
def mock_instructor_client(sample_compare_response):
    """Mock the instructor client to return canned responses."""
    client = MagicMock()
    client.messages.create.return_value = sample_compare_response
    return client


@pytest.fixture
def app(mock_turnstile):
    """Create a test FastAPI app with mocked dependencies."""
    from src.main import app as fastapi_app
    return fastapi_app


@pytest.fixture
def client(app):
    """TestClient for the FastAPI app."""
    return TestClient(app)
