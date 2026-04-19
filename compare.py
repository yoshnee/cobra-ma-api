"""
compare.py — POST /compare endpoint.

Takes combined user input from all 3 wizard pages, queries Postgres for
candidate plans, and uses Claude to rank and compare the top 3 suggestions.
"""

import json

import anthropic
from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from db import get_engine
from llm import get_instructor_client, MODEL, ANTHROPIC_API_KEY
from schemas import CompareRequest, CompareResponse, InsuranceCardExtraction
from turnstile import verify_turnstile
from zip_to_rating_area import get_rating_area

router = APIRouter()

# ---------------------------------------------------------------------------
# SQL queries (reused from plans.py / dental_plans.py with minor tweaks)
# ---------------------------------------------------------------------------

METAL_ORDER = ["catastrophic", "bronze", "silver", "gold", "platinum"]
DENTAL_LEVELS = ["low", "high"]

MEDICAL_CANDIDATES_SQL = text("""
    SELECT p.plan_id, p.plan_name, p.metal_level, p.plan_type,
           c.short_name AS carrier, c.phone AS carrier_phone,
           c.website AS carrier_website,
           r.monthly_premium
    FROM ma_plans p
    JOIN carriers c ON c.issuer_id = p.issuer_id
    JOIN ma_rates r ON r.plan_id = p.plan_id
    WHERE r.age_min <= :age AND r.age_max >= :age
      AND r.rating_area = :rating_area
      AND p.metal_level = ANY(:metal_levels)
    ORDER BY r.monthly_premium ASC
""")

MEDICAL_BENEFITS_SQL = text("""
    SELECT service_name, copay_amount, coinsurance_pct, after_deductible,
           cost_sharing_text, deductible_individual, oop_max_individual
    FROM ma_benefits
    WHERE plan_id = :plan_id
    ORDER BY service_name
""")

DENTAL_CANDIDATES_SQL = text("""
    SELECT p.plan_id, p.plan_name, p.dental_level, p.plan_type,
           c.short_name AS carrier, c.phone AS carrier_phone,
           c.website AS carrier_website,
           r.monthly_premium
    FROM dental_plans p
    JOIN carriers c ON c.issuer_id = p.issuer_id
    JOIN dental_rates r ON r.plan_id = p.plan_id
    WHERE r.age_min <= :age AND r.age_max >= :age
      AND r.rating_area = :rating_area
    ORDER BY r.monthly_premium ASC
""")

DENTAL_BENEFITS_SQL = text("""
    SELECT service_category, service_name,
           copay_amount, coinsurance_pct, after_deductible,
           cost_sharing_text, waiting_period_months,
           deductible_individual, annual_max_individual, ortho_lifetime_max
    FROM dental_benefits
    WHERE plan_id = :plan_id
    ORDER BY service_category, service_name
""")

# ---------------------------------------------------------------------------
# Benefit fetchers
# ---------------------------------------------------------------------------

def _fetch_medical_benefits(conn, plan_id: str) -> dict:
    rows = conn.execute(MEDICAL_BENEFITS_SQL, {"plan_id": plan_id}).mappings().all()
    if not rows:
        return {"deductible": None, "oop_max": None, "services": []}

    first = rows[0]
    services = []
    for r in rows:
        services.append({
            "service_name": r["service_name"],
            "copay_amount": float(r["copay_amount"]) if r["copay_amount"] is not None else None,
            "coinsurance_pct": float(r["coinsurance_pct"]) if r["coinsurance_pct"] is not None else None,
            "after_deductible": r["after_deductible"],
            "cost_sharing_text": r["cost_sharing_text"],
        })

    return {
        "deductible": float(first["deductible_individual"]) if first["deductible_individual"] is not None else None,
        "oop_max": float(first["oop_max_individual"]) if first["oop_max_individual"] is not None else None,
        "services": services,
    }


def _fetch_dental_benefits(conn, plan_id: str) -> dict:
    rows = conn.execute(DENTAL_BENEFITS_SQL, {"plan_id": plan_id}).mappings().all()
    if not rows:
        return {"deductible": None, "annual_max": None, "ortho_lifetime_max": None, "services": []}

    first = rows[0]
    services = []
    for r in rows:
        services.append({
            "service_category": r["service_category"],
            "service_name": r["service_name"],
            "copay_amount": float(r["copay_amount"]) if r["copay_amount"] is not None else None,
            "coinsurance_pct": float(r["coinsurance_pct"]) if r["coinsurance_pct"] is not None else None,
            "after_deductible": r["after_deductible"],
            "cost_sharing_text": r["cost_sharing_text"],
            "waiting_period_months": r["waiting_period_months"],
        })

    return {
        "deductible": float(first["deductible_individual"]) if first["deductible_individual"] is not None else None,
        "annual_max": float(first["annual_max_individual"]) if first["annual_max_individual"] is not None else None,
        "ortho_lifetime_max": float(first["ortho_lifetime_max"]) if first["ortho_lifetime_max"] is not None else None,
        "services": services,
    }


# ---------------------------------------------------------------------------
# Candidate selection helpers
# ---------------------------------------------------------------------------

def _infer_metal_level(premium: float) -> str:
    """Rough heuristic: infer metal tier from monthly premium."""
    if premium < 200:
        return "bronze"
    if premium < 400:
        return "silver"
    if premium < 600:
        return "gold"
    return "platinum"


def _adjacent_tiers(metal_level: str) -> list[str]:
    """Return the tier itself plus +/- 1 adjacent tiers."""
    idx = METAL_ORDER.index(metal_level)
    tiers = [metal_level]
    if idx > 0:
        tiers.append(METAL_ORDER[idx - 1])
    if idx < len(METAL_ORDER) - 1:
        tiers.append(METAL_ORDER[idx + 1])
    return tiers


def _get_medical_candidates(conn, age: int, rating_area: int, premium: float) -> list[dict]:
    """Pull ~15 medical candidate plans from Postgres."""
    metal_level = _infer_metal_level(premium)
    tiers = _adjacent_tiers(metal_level)

    rows = conn.execute(
        MEDICAL_CANDIDATES_SQL,
        {"age": age, "rating_area": rating_area, "metal_levels": tiers},
    ).mappings().all()

    candidates = []
    for row in rows:
        benefits = _fetch_medical_benefits(conn, row["plan_id"])
        candidates.append({
            "plan_id": row["plan_id"],
            "plan_name": row["plan_name"],
            "metal_level": row["metal_level"],
            "plan_type": row["plan_type"],
            "carrier": row["carrier"],
            "carrier_phone": row["carrier_phone"],
            "carrier_website": row["carrier_website"],
            "monthly_premium": float(row["monthly_premium"]),
            **benefits,
        })
        if len(candidates) >= 15:
            break

    return candidates


def _get_dental_candidates(conn, age: int, rating_area: int) -> list[dict]:
    """Pull ~10 dental candidate plans from Postgres."""
    rows = conn.execute(
        DENTAL_CANDIDATES_SQL,
        {"age": age, "rating_area": rating_area},
    ).mappings().all()

    candidates = []
    for row in rows:
        benefits = _fetch_dental_benefits(conn, row["plan_id"])
        candidates.append({
            "plan_id": row["plan_id"],
            "plan_name": row["plan_name"],
            "dental_level": row["dental_level"],
            "plan_type": row["plan_type"],
            "carrier": row["carrier"],
            "carrier_phone": row["carrier_phone"],
            "carrier_website": row["carrier_website"],
            "monthly_premium": float(row["monthly_premium"]),
            **benefits,
        })
        if len(candidates) >= 10:
            break

    return candidates


# ---------------------------------------------------------------------------
# LLM prompt builder
# ---------------------------------------------------------------------------

def _build_cobra_summary(req: CompareRequest) -> dict:
    """Build a dict summarizing what we know about the user's current plan."""
    summary: dict = {}

    if req.medical_plan_name:
        summary["medical_plan_name"] = req.medical_plan_name
    if req.medical_carrier:
        summary["medical_carrier"] = req.medical_carrier
    if req.medical_monthly_premium is not None:
        summary["medical_monthly_premium"] = req.medical_monthly_premium
    if req.dental_plan_name:
        summary["dental_plan_name"] = req.dental_plan_name
    if req.dental_carrier:
        summary["dental_carrier"] = req.dental_carrier
    if req.dental_monthly_premium is not None:
        summary["dental_monthly_premium"] = req.dental_monthly_premium

    if req.card_data:
        card = req.card_data.model_dump(exclude_none=True)
        if card:
            summary["card_benefits"] = card

    return summary


def _build_compare_prompt(req: CompareRequest, medical_candidates: list[dict], dental_candidates: list[dict]) -> str:
    cobra_summary = _build_cobra_summary(req)

    card_section = "Not provided (user skipped insurance card upload)"
    if req.card_data:
        card_dict = req.card_data.model_dump(exclude_none=True)
        if card_dict:
            card_section = json.dumps(card_dict, indent=2)

    medical_notes = req.medical_notes or "None"
    dental_notes = req.dental_notes or "None"

    prompt = f"""\
[CONTEXT]
You are a benefits administrator at a Massachusetts health insurance agency \
helping a resident evaluate whether to keep their COBRA continuation coverage \
or switch to a MA Health Connector marketplace plan. The user is likely paying \
high COBRA premiums and looking for cheaper alternatives that preserve the \
benefits they care about most. Your role is to objectively compare plans and \
present the tradeoffs — you do NOT recommend or tell the user what to do.

[INSTRUCTIONS]
Given the user's current COBRA plan details and a set of candidate MA Health \
Connector plans from our database, select the 3 best alternative medical plans. \
For each selected plan, produce a side-by-side benefit comparison against the \
user's current coverage and explain why you selected it. Write a brief overall \
summary (3-5 sentences) highlighting key tradeoffs across all suggestions.

Selection priorities (in order):
1. SAVINGS FIRST: Prioritize plans with the greatest monthly premium savings \
vs the user's current COBRA cost. The whole point of this tool is to show \
users they can save money.
2. PROTECT KEY BENEFITS: Among plans with good savings, favor those that \
preserve the user's existing benefit levels — especially copays, deductible, \
and OOP max. If a plan saves $200/month but doubles the deductible, flag that \
tradeoff clearly. Do not suggest a plan that eliminates a benefit the user \
explicitly values (see freeform notes below).
3. RESPECT FREEFORM NOTES: The user's freeform notes describe benefits they \
specifically care about — for example "$0 copay for therapy", "$15 for generic \
prescriptions", or "2 cleanings per year covered." Treat these as hard \
constraints: look for candidate plans whose benefits match or come close. \
If no candidate matches a noted benefit, say so in the reasoning — do not \
silently ignore it.

Rules:
- Be factual and neutral — do NOT recommend or tell the user what to choose.
- Only compare fields where you have data for BOTH plans. Never guess or fabricate values.
- For the verdict field, use: "better" if the alternative is better for the user, \
"worse" if worse, "similar" if roughly equal, "unknown" if data is missing for either side.
- monthly_savings = user's COBRA premium minus the alternative's premium (can be negative).
- In the reasoning field, always cite specific dollar amounts (e.g. "saves $180/month \
but your PCP copay rises from $20 to $35"). Do not write vague statements like \
"offers competitive pricing."
- If the user's insurance card data was not provided, note in the overall_summary \
that the comparison is based on premium only and may not reflect full benefit differences.

[INPUT DATA]
Current COBRA Plan:
- Medical Plan: {req.medical_plan_name or "Unknown"} by {req.medical_carrier or "Unknown"}
- Medical Monthly Premium: ${req.medical_monthly_premium or "Unknown"}
- Benefits from insurance card: {card_section}
- User's medical notes: {medical_notes}
- User's dental notes: {dental_notes}

Candidate MA Health Connector Medical Plans (pick the best 3):
{json.dumps(medical_candidates, indent=2)}

[OUTPUT]
Return the structured CompareResponse with cobra_summary, exactly 3 suggestions, \
and an overall_summary."""

    return prompt


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/compare")
async def compare_plans(req: CompareRequest):
    await verify_turnstile(req.turnstile_token)

    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")

    if req.medical_monthly_premium is None:
        raise HTTPException(status_code=400, detail="Medical monthly premium is required")

    rating_area = get_rating_area(req.zip_code)
    if rating_area is None:
        raise HTTPException(
            status_code=400,
            detail=f"Zip code {req.zip_code} is not a valid Massachusetts zip code",
        )

    engine = get_engine()

    with engine.connect() as conn:
        medical_candidates = _get_medical_candidates(
            conn, req.age, rating_area, req.medical_monthly_premium
        )
        dental_candidates = _get_dental_candidates(conn, req.age, rating_area)

    if not medical_candidates:
        raise HTTPException(
            status_code=404,
            detail="No Health Connector plans found for your age and location",
        )

    prompt = _build_compare_prompt(req, medical_candidates, dental_candidates)

    try:
        result = get_instructor_client().messages.create(
            model=MODEL,
            max_tokens=4096,
            max_retries=2,
            response_model=CompareResponse,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIError as e:
        raise HTTPException(status_code=502, detail=f"Anthropic API error: {e}")

    # Inject the cobra_summary we built (don't rely on LLM to echo it correctly)
    result.cobra_summary = _build_cobra_summary(req)

    return result.model_dump()
