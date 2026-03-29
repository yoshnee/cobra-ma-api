"""
plans.py — GET /plans endpoint.

Finds Health Connector plans that are cheaper or comparable to a user's COBRA premium.
"""

import json

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from db import get_engine
from llm import get_client, MODEL, ANTHROPIC_API_KEY
from turnstile import verify_turnstile
from zip_to_rating_area import get_rating_area

router = APIRouter()

METAL_ORDER = ["catastrophic", "bronze", "silver", "gold", "platinum"]

KEY_SERVICES = [
    "Primary Care Visit to Treat an Injury or Illness",
    "Specialist Visit",
    "Emergency Room Services",
    "Generic Drugs",
    "Inpatient Hospital Services (e.g., Hospital Stay)",
]


def _adjacent_tiers(metal_level: str):
    """Return (tier_below, tier_above) or None for each."""
    idx = METAL_ORDER.index(metal_level)
    below = METAL_ORDER[idx - 1] if idx > 0 else None
    above = METAL_ORDER[idx + 1] if idx < len(METAL_ORDER) - 1 else None
    return below, above


PLAN_RATE_SQL = text("""
    SELECT p.plan_id, p.plan_name, p.metal_level, p.plan_type,
           c.short_name AS carrier, c.phone AS carrier_phone, c.website AS carrier_website,
           r.monthly_premium
    FROM ma_plans p
    JOIN carriers c ON c.issuer_id = p.issuer_id
    JOIN ma_rates r ON r.plan_id = p.plan_id
    WHERE r.age_min <= :age AND r.age_max >= :age
      AND r.rating_area = :rating_area
      AND p.metal_level = :metal_level
    ORDER BY r.monthly_premium ASC
""")

BENEFITS_SQL = text("""
    SELECT service_name, copay_amount, coinsurance_pct, after_deductible,
           cost_sharing_text, deductible_individual, oop_max_individual
    FROM ma_benefits
    WHERE plan_id = :plan_id
      AND service_name = ANY(:services)
    ORDER BY service_name
""")


def _fetch_benefits(conn, plan_id: str) -> dict:
    rows = conn.execute(BENEFITS_SQL, {"plan_id": plan_id, "services": KEY_SERVICES}).mappings().all()
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


def _build_result(row, tag: str, benefits: dict, cobra_plan_type: str | None) -> dict:
    return {
        "plan_id": row["plan_id"],
        "plan_name": row["plan_name"],
        "metal_level": row["metal_level"],
        "plan_type": row["plan_type"],
        "same_plan_type": row["plan_type"] == cobra_plan_type if cobra_plan_type else None,
        "carrier": row["carrier"],
        "carrier_phone": row["carrier_phone"],
        "carrier_website": row["carrier_website"],
        "monthly_premium": float(row["monthly_premium"]),
        "tag": tag,
        **benefits,
    }


@router.get("/plans")
async def get_plans(
    age: int = Query(..., ge=0, le=99),
    zip_code: str = Query(..., pattern=r"^\d{5}$"),
    monthly_premium: float = Query(..., gt=0),
    turnstile_token: str = Query(...),
    metal_level: str | None = Query(None),
    plan_type: str | None = Query(None, pattern="^(HMO|PPO|EPO)$"),
    cobra_deductible: float | None = Query(None, ge=0),
    cobra_oop_max: float | None = Query(None, ge=0),
    cobra_primary_care_copay: float | None = Query(None, ge=0),
    cobra_specialist_copay: float | None = Query(None, ge=0),
    cobra_er_copay: float | None = Query(None, ge=0),
    cobra_generic_drug_copay: float | None = Query(None, ge=0),
    cobra_inpatient_copay: float | None = Query(None, ge=0),
):
    await verify_turnstile(turnstile_token)

    if not metal_level or metal_level not in METAL_ORDER:
        metal_level = "silver"

    rating_area = get_rating_area(zip_code)
    if rating_area is None:
        raise HTTPException(status_code=400, detail=f"Zip code {zip_code} is not a valid Massachusetts zip code")

    engine = get_engine()
    results = []

    with engine.connect() as conn:
        # Same-tier: cheaper plans
        same_rows = conn.execute(
            PLAN_RATE_SQL, {"age": age, "rating_area": rating_area, "metal_level": metal_level}
        ).mappings().all()

        for row in same_rows:
            if float(row["monthly_premium"]) >= monthly_premium + 20:
                continue
            benefits = _fetch_benefits(conn, row["plan_id"])
            results.append(_build_result(row, "same_tier_cheaper", benefits, plan_type))

        # Adjacent tiers
        tier_below, tier_above = _adjacent_tiers(metal_level)
        premium_low = monthly_premium * 0.8
        premium_high = monthly_premium * 1.2

        for adj_tier, tag in [(tier_above, "better_coverage"), (tier_below, "lower_cost_tradeoff")]:
            if adj_tier is None:
                continue
            adj_rows = conn.execute(
                PLAN_RATE_SQL, {"age": age, "rating_area": rating_area, "metal_level": adj_tier}
            ).mappings().all()

            for row in adj_rows:
                prem = float(row["monthly_premium"])
                if prem < premium_low or prem > premium_high:
                    continue
                benefits = _fetch_benefits(conn, row["plan_id"])
                results.append(_build_result(row, tag, benefits, plan_type))

    # Sort by premium asc, return top 5
    results.sort(key=lambda r: r["monthly_premium"])
    top_results = results[:5]

    cobra_benefits = {
        "deductible": cobra_deductible,
        "oop_max": cobra_oop_max,
        "primary_care_copay": cobra_primary_care_copay,
        "specialist_copay": cobra_specialist_copay,
        "er_copay": cobra_er_copay,
        "generic_drug_copay": cobra_generic_drug_copay,
        "inpatient_copay": cobra_inpatient_copay,
    }
    # Strip None values so the prompt only shows what we actually know
    cobra_benefits = {k: v for k, v in cobra_benefits.items() if v is not None}

    summary = None
    if top_results and ANTHROPIC_API_KEY:
        summary = _generate_summary(monthly_premium, metal_level, plan_type, cobra_benefits, top_results)

    return {"plans": top_results, "summary": summary}


SUMMARY_PROMPT = """\
You are helping a Massachusetts resident understand their health insurance options. \
They currently have COBRA coverage at ${monthly_premium}/month ({metal_level} tier, {plan_type} plan).

{cobra_benefits_section}

Below are alternative Health Connector plans found for them. Write a brief, neutral summary \
(3-5 sentences) that highlights the key differences between these options and their current \
COBRA plan — premiums, deductibles, out-of-pocket maximums, copays, and network type differences.

Be factual and objective. Do NOT recommend a specific plan or tell the user what to do. \
Just lay out the tradeoffs clearly so they can make their own informed decision.

CRITICAL: Only compare fields where you have actual numbers for BOTH the user's current plan \
AND the alternative. If a value is missing or was not provided, do NOT guess, estimate, or \
make up numbers. Simply omit that comparison.

Plans found:
{plans_json}"""


def _generate_summary(
    monthly_premium: float,
    metal_level: str,
    plan_type: str | None,
    cobra_benefits: dict,
    plans: list[dict],
) -> str | None:
    if cobra_benefits:
        benefit_lines = ["Their current COBRA plan benefits (as extracted from their notice):"]
        label_map = {
            "deductible": "Deductible",
            "oop_max": "Out-of-pocket max",
            "primary_care_copay": "Primary care copay",
            "specialist_copay": "Specialist copay",
            "er_copay": "ER copay",
            "generic_drug_copay": "Generic drug copay",
            "inpatient_copay": "Inpatient copay",
        }
        for key, val in cobra_benefits.items():
            label = label_map.get(key, key)
            benefit_lines.append(f"  - {label}: ${val:.2f}")
        cobra_benefits_section = "\n".join(benefit_lines)
    else:
        cobra_benefits_section = (
            "No benefit details were available from their COBRA notice. "
            "Do NOT guess or infer what their current copays or deductibles might be."
        )

    slim_plans = []
    for p in plans:
        slim_plans.append({
            "plan_name": p["plan_name"],
            "carrier": p["carrier"],
            "metal_level": p["metal_level"],
            "plan_type": p["plan_type"],
            "monthly_premium": p["monthly_premium"],
            "deductible": p.get("deductible"),
            "oop_max": p.get("oop_max"),
            "tag": p["tag"],
            "services": p.get("services", []),
        })

    prompt = SUMMARY_PROMPT.format(
        monthly_premium=monthly_premium,
        metal_level=metal_level,
        plan_type=plan_type or "unknown",
        cobra_benefits_section=cobra_benefits_section,
        plans_json=json.dumps(slim_plans, indent=2),
    )

    try:
        message = get_client().messages.create(
            model=MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip()
    except Exception:
        return None
