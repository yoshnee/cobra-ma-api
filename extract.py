"""
extract.py — POST /extract endpoint.

Accepts a COBRA election notice PDF, uses Claude to extract structured data.
"""

import base64
import json

import anthropic
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from llm import get_client, MODEL, ANTHROPIC_API_KEY
from turnstile import verify_turnstile

router = APIRouter()

EXTRACTION_PROMPT = """\
Extract the following fields from this COBRA election notice PDF. Return ONLY valid JSON with these keys:

- carrier (string): insurance company name
- plan_name (string): plan name
- metal_level (string or null): one of "platinum", "gold", "silver", "bronze", "catastrophic", or null if not stated
- plan_type (string or null): one of "HMO", "PPO", "EPO", or null if not stated
- monthly_premium (number): monthly COBRA premium in dollars
- deductible (number or null): annual individual deductible in dollars
- oop_max (number or null): annual individual out-of-pocket maximum in dollars
- effective_date (string or null): coverage effective date in YYYY-MM-DD format
- rating_area (integer or null): MA rating area 1-7, or null if not stated
- age (integer or null): subscriber age, or null if not stated
- benefits (object or null): if the document includes a Summary of Benefits and Coverage \
or similar cost-sharing details, extract the following. If not present, set to null.
  - primary_care_copay (number or null): copay in dollars for a primary care visit
  - specialist_copay (number or null): copay in dollars for a specialist visit
  - er_copay (number or null): copay in dollars for emergency room services
  - generic_drug_copay (number or null): copay in dollars for generic drugs
  - inpatient_copay (number or null): copay in dollars for inpatient hospital services

IMPORTANT: Only extract values that are explicitly stated in the document. \
If a field is not present in the PDF, you MUST use null. \
Do NOT guess, infer, or make up any values. Return raw JSON only, no markdown fences."""


@router.post("/extract")
async def extract_cobra_pdf(file: UploadFile = File(...), turnstile_token: str = Form(...)):
    await verify_turnstile(turnstile_token)

    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Empty file")

    b64_pdf = base64.standard_b64encode(pdf_bytes).decode("utf-8")

    try:
        message = get_client().messages.create(
            model=MODEL,
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": b64_pdf,
                            },
                        },
                        {"type": "text", "text": EXTRACTION_PROMPT},
                    ],
                }
            ],
        )
    except anthropic.APIError as e:
        raise HTTPException(status_code=502, detail=f"Anthropic API error: {e}")

    raw = message.content[0].text.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw_response": raw, "error": "Failed to parse structured response"}
