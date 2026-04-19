"""
extract_card.py — POST /extract-card endpoint.

Accepts front (required) and back (optional) images of an insurance card and
extracts benefit details — copays, deductibles, OOP max, Rx tiers, etc.
"""

import base64

import anthropic
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from src.clients.llm import get_instructor_client, MODEL, ANTHROPIC_API_KEY
from src.schemas import InsuranceCardExtraction
from src.clients.turnstile import verify_turnstile

router = APIRouter()

EXTRACTION_PROMPT = """\
[CONTEXT]
You are a benefits administrator at a Massachusetts health insurance agency. \
You are reviewing a member's insurance card to catalog their current benefit \
levels — copays, deductibles, out-of-pocket maximums, and prescription drug \
tiers. This information will be used to compare their COBRA coverage against \
MA Health Connector marketplace alternatives.

[INSTRUCTIONS]
Extract all visible benefit information from the front and back of this \
insurance card. The front typically shows: carrier name, plan type \
(HMO/PPO/EPO/POS), PCP copay, specialist copay, member ID, and group number. \
The back typically shows: deductible amounts (individual/family, \
in-network/out-of-network), out-of-pocket maximums, ER and urgent care copays, \
and prescription drug tier copays. Only extract values that are explicitly \
printed on the card. If a field is not visible, use null.

[INPUT DATA]
See the attached card image(s). If two images are provided, the first is the \
front and the second is the back.

[OUTPUT]
Return a structured response with: carrier, plan_type, member_id, \
group_number, pcp_copay, specialist_copay, er_copay, urgent_care_copay, \
deductible_individual, deductible_family, oop_max_individual, oop_max_family, \
rx_tier1_copay, rx_tier2_copay, rx_tier3_copay, rx_tier4_copay, \
inpatient_copay, coinsurance_pct."""

_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


def _validate_image(upload: UploadFile, label: str) -> None:
    content_type = upload.content_type or ""
    if content_type not in _IMAGE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"{label}: unsupported type {content_type}. Upload JPEG or PNG.",
        )


@router.post("/extract-card")
async def extract_insurance_card(
    front: UploadFile = File(...),
    turnstile_token: str = Form(...),
    back: UploadFile | None = File(None),
):
    await verify_turnstile(turnstile_token)

    _validate_image(front, "Front image")
    if back is not None:
        _validate_image(back, "Back image")

    front_bytes = await front.read()
    if not front_bytes:
        raise HTTPException(status_code=400, detail="Front image is empty")

    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")

    # Build image content blocks — both in a single LLM call
    content: list[dict] = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": front.content_type,
                "data": base64.standard_b64encode(front_bytes).decode("utf-8"),
            },
        },
    ]

    if back is not None:
        back_bytes = await back.read()
        if back_bytes:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": back.content_type,
                        "data": base64.standard_b64encode(back_bytes).decode("utf-8"),
                    },
                }
            )

    content.append({"type": "text", "text": EXTRACTION_PROMPT})

    try:
        result = get_instructor_client().messages.create(
            model=MODEL,
            max_tokens=1024,
            max_retries=2,
            response_model=InsuranceCardExtraction,
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.APIError as e:
        raise HTTPException(status_code=502, detail=f"Anthropic API error: {e}")

    return result.model_dump()
