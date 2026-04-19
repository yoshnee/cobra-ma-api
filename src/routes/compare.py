"""
compare.py — POST /compare endpoint.

Takes combined user input from all 3 wizard pages, queries Postgres for
candidate plans, and uses Claude to rank and compare the top 3 medical
suggestions + up to 3 dental suggestions.
"""

import json

import anthropic
from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from src.clients.db import get_engine
from src.clients.llm import get_instructor_client, MODEL, ANTHROPIC_API_KEY
from src.schemas import CompareRequest, CompareResponse
from src.clients.turnstile import verify_turnstile
from src.zip_to_rating_area import get_rating_area

router = APIRouter()

# ---------------------------------------------------------------------------
# SQL queries
# ---------------------------------------------------------------------------

METAL_ORDER = ["catastrophic", "bronze", "silver", "gold", "platinum"]

# Same plan type — pull across ALL metal tiers
MEDICAL_SAME_TYPE_SQL = text("""
    SELECT p.plan_id, p.plan_name, p.metal_level, p.plan_type,
           c.short_name AS carrier, c.phone AS carrier_phone,
           c.website AS carrier_website,
           r.monthly_premium
    FROM ma_plans p
    JOIN carriers c ON c.issuer_id = p.issuer_id
    JOIN ma_rates r ON r.plan_id = p.plan_id
    WHERE r.age_min <= :age AND r.age_max >= :age
      AND r.rating_area = :rating_area
      AND p.plan_type = :plan_type
    ORDER BY r.monthly_premium ASC
""")

# Other plan types — pull from adjacent metal tiers
MEDICAL_OTHER_TYPE_SQL = text("""
    SELECT p.plan_id, p.plan_name, p.metal_level, p.plan_type,
           c.short_name AS carrier, c.phone AS carrier_phone,
           c.website AS carrier_website,
           r.monthly_premium
    FROM ma_plans p
    JOIN carriers c ON c.issuer_id = p.issuer_id
    JOIN ma_rates r ON r.plan_id = p.plan_id
    WHERE r.age_min <= :age AND r.age_max >= :age
      AND r.rating_area = :rating_area
      AND p.plan_type != :plan_type
      AND p.metal_level = ANY(:metal_levels)
    ORDER BY r.monthly_premium ASC
""")

# All medical plans (when no plan type is known)
MEDICAL_ALL_SQL = text("""
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


def _build_candidate(row, benefits: dict) -> dict:
    return {
        "plan_id": row["plan_id"],
        "plan_name": row["plan_name"],
        "metal_level": row["metal_level"],
        "plan_type": row["plan_type"],
        "carrier": row["carrier"],
        "carrier_phone": row["carrier_phone"],
        "carrier_website": row["carrier_website"],
        "monthly_premium": float(row["monthly_premium"]),
        **benefits,
    }


def _add_cost_scenarios(
    candidates: list[dict], cobra_premium: float, cobra_oop: float | None
) -> list[dict]:
    """Enrich each candidate with pre-computed annual cost scenarios."""
    cobra_annual_low = round(cobra_premium * 12)
    cobra_annual_max = round(cobra_premium * 12 + (cobra_oop or 0))

    for c in candidates:
        prem = c["monthly_premium"]
        ded = c.get("deductible")
        oop = c.get("oop_max")

        c["annual_cost_low"] = round(prem * 12)
        c["annual_cost_mid"] = round(prem * 12 + (ded or 0))
        c["annual_cost_max"] = round(prem * 12 + (oop or 0))
        c["cobra_annual_cost_low"] = cobra_annual_low
        c["cobra_annual_cost_max"] = cobra_annual_max

    return candidates


def _get_medical_candidates(
    conn, age: int, rating_area: int, premium: float, plan_type: str | None
) -> list[dict]:
    """Pull medical candidates: same plan type first, then backfill with others."""
    metal_level = _infer_metal_level(premium)
    tiers = _adjacent_tiers(metal_level)
    seen_ids: set[str] = set()
    candidates: list[dict] = []

    if plan_type and plan_type in ("HMO", "PPO", "EPO"):
        # Pass 1: same plan type, ALL metal tiers
        same_rows = conn.execute(
            MEDICAL_SAME_TYPE_SQL,
            {"age": age, "rating_area": rating_area, "plan_type": plan_type},
        ).mappings().all()

        for row in same_rows:
            if len(candidates) >= 10:
                break
            benefits = _fetch_medical_benefits(conn, row["plan_id"])
            candidates.append(_build_candidate(row, benefits))
            seen_ids.add(row["plan_id"])

        # Pass 2: other plan types, adjacent metal tiers
        other_rows = conn.execute(
            MEDICAL_OTHER_TYPE_SQL,
            {"age": age, "rating_area": rating_area, "plan_type": plan_type, "metal_levels": tiers},
        ).mappings().all()

        for row in other_rows:
            if len(candidates) >= 20:
                break
            if row["plan_id"] in seen_ids:
                continue
            benefits = _fetch_medical_benefits(conn, row["plan_id"])
            candidates.append(_build_candidate(row, benefits))
    else:
        # No plan type known — pull all types from adjacent tiers
        rows = conn.execute(
            MEDICAL_ALL_SQL,
            {"age": age, "rating_area": rating_area, "metal_levels": tiers},
        ).mappings().all()

        for row in rows:
            if len(candidates) >= 20:
                break
            benefits = _fetch_medical_benefits(conn, row["plan_id"])
            candidates.append(_build_candidate(row, benefits))

    return candidates


def _get_dental_candidates(conn, age: int, rating_area: int) -> list[dict]:
    """Pull all dental candidate plans."""
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
        if len(candidates) >= 15:
            break

    return candidates


# ---------------------------------------------------------------------------
# Resolve user's plan type from available sources
# ---------------------------------------------------------------------------

def _resolve_plan_type(req: CompareRequest) -> str | None:
    """Get the user's plan type from card data (most reliable) or infer from plan name."""
    if req.card_data and req.card_data.plan_type:
        pt = req.card_data.plan_type.upper().strip()
        if pt in ("HMO", "PPO", "EPO", "POS"):
            # POS maps to PPO for MA Connector matching purposes
            return "PPO" if pt == "POS" else pt
    # Try to infer from plan name
    if req.medical_plan_name:
        name_upper = req.medical_plan_name.upper()
        for pt in ("PPO", "EPO", "HMO"):
            if pt in name_upper:
                return pt
    return None


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


def _build_compare_prompt(
    req: CompareRequest,
    medical_candidates: list[dict],
    dental_candidates: list[dict],
    user_plan_type: str | None,
) -> str:
    card_section = "Not provided (user skipped insurance card upload)"
    if req.card_data:
        card_dict = req.card_data.model_dump(exclude_none=True)
        if card_dict:
            card_section = json.dumps(card_dict, indent=2)

    medical_notes = req.medical_notes or "None"
    dental_notes = req.dental_notes or "None"
    plan_type_str = user_plan_type or "Unknown"

    # Dental section
    has_dental = req.dental_monthly_premium is not None and req.dental_monthly_premium > 0
    if has_dental and dental_candidates:
        dental_section = f"""
Dental — the user also has COBRA dental coverage at ${req.dental_monthly_premium}/month.
Candidate MA Health Connector Dental Plans (pick the best 3):
{json.dumps(dental_candidates, indent=2)}"""
    else:
        dental_section = "\nNo dental comparison requested (user did not provide a dental premium)."

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
{"Also select up to 3 dental plan alternatives." if has_dental and dental_candidates else ""} \
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

Plan type guidance:
- The user's current plan type is: {plan_type_str}.
- Prioritize suggesting plans of the SAME type when available.
- If you suggest a plan of a DIFFERENT type (e.g., HMO when user has PPO), \
you MUST note this prominently in the reasoning and explain what it means \
for the user (e.g., "This is an HMO — unlike your current PPO, you will need \
a primary care referral for specialists and must use in-network providers only. \
Your current doctors may not be in-network.").
- If few or no same-type plans exist among the candidates, explain this in \
the overall_summary (e.g., "The MA Health Connector currently offers only \
2 PPO plans; most alternatives are HMO-based.").

Data completeness:
- For every medical plan suggestion, you MUST include rows for Deductible and \
Out-of-Pocket Maximum in the comparison table, even if the value is missing. \
If the value is NULL or missing in the candidate data, use "Not listed in \
plan filing" as the value and set verdict to "unknown".
- Never omit Deductible or OOP Max rows from the comparison table.

Cost scenario analysis:
- Each candidate plan includes pre-computed annual cost scenarios:
  - annual_cost_low: premium x 12 (healthy year, minimal care)
  - annual_cost_mid: premium x 12 + deductible (moderate utilization)
  - annual_cost_max: premium x 12 + OOP max (max utilization — pregnancy, surgery)
- The user's COBRA annual costs (cobra_annual_cost_low, cobra_annual_cost_max) are \
also included in each candidate for easy comparison.
- In the reasoning field, ALWAYS mention the max-utilization scenario. \
Example: "Saves $230/month ($2,760/year) in a healthy year, but in a \
max-utilization year (pregnancy, major surgery), total cost is $13,000 vs \
COBRA's $12,100 — COBRA wins by $900 in that scenario."
- If the user mentioned pregnancy, ongoing treatment, or high utilization in \
their freeform notes, lead with the max-utilization comparison — it is the \
most relevant number for them.

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
- Plan Type: {plan_type_str}
- Benefits from insurance card: {card_section}
- User's medical notes: {medical_notes}
- User's dental notes: {dental_notes}

Candidate MA Health Connector Medical Plans (pick the best 3):
{json.dumps(medical_candidates, indent=2)}
{dental_section}

[OUTPUT]
Return the structured CompareResponse with cobra_summary, exactly 3 \
medical_suggestions, {"up to 3 dental_suggestions," if has_dental and dental_candidates else "an empty dental_suggestions list,"} \
and an overall_summary."""

    return prompt


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/compare")
async def compare_plans(req: CompareRequest):
    await verify_turnstile(req.turnstile_token)

    if req.medical_monthly_premium is None:
        raise HTTPException(status_code=400, detail="Medical monthly premium is required")

    rating_area = get_rating_area(req.zip_code)
    if rating_area is None:
        raise HTTPException(
            status_code=400,
            detail=f"Zip code {req.zip_code} is not a valid Massachusetts zip code",
        )

    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")

    user_plan_type = _resolve_plan_type(req)

    engine = get_engine()

    with engine.connect() as conn:
        medical_candidates = _get_medical_candidates(
            conn, req.age, rating_area, req.medical_monthly_premium, user_plan_type
        )
        dental_candidates = _get_dental_candidates(conn, req.age, rating_area)

    if not medical_candidates:
        raise HTTPException(
            status_code=404,
            detail="No Health Connector plans found for your age and location",
        )

    # Enrich candidates with annual cost scenarios
    cobra_oop = req.card_data.oop_max_individual if req.card_data else None
    _add_cost_scenarios(medical_candidates, req.medical_monthly_premium, cobra_oop)

    prompt = _build_compare_prompt(req, medical_candidates, dental_candidates, user_plan_type)

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
