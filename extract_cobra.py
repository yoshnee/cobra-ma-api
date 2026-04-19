"""
extract_cobra.py — POST /extract-cobra endpoint.

Accepts a photo/PDF of the COBRA election/benefits table page and extracts
plan name + premium for medical and dental lines.
"""

import base64

import anthropic
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from llm import get_instructor_client, MODEL, ANTHROPIC_API_KEY
from schemas import CobraElectionExtraction
from turnstile import verify_turnstile

router = APIRouter()

EXTRACTION_PROMPT = """\
[CONTEXT]
You are a benefits administrator at a Massachusetts health insurance agency. \
You specialize in reading COBRA continuation coverage notices and identifying \
the key plan details that employees need to compare against MA Health Connector \
marketplace plans.

[INSTRUCTIONS]
Extract the medical and dental plan information from this COBRA election/benefits \
table page. For each benefit line (medical, dental), identify the plan name, \
insurance carrier, and the expected ongoing monthly premium. Only extract values \
that are explicitly visible in the document. If a field is not present, use null.

[INPUT DATA]
See the attached document image.

[OUTPUT]
Return a structured response with: medical_plan_name, medical_carrier, \
medical_monthly_premium, dental_plan_name, dental_carrier, dental_monthly_premium."""

# Media types we accept and how to send them to Claude
_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_PDF_TYPE = "application/pdf"


def _build_content_block(data_b64: str, media_type: str) -> dict:
    """Build the appropriate Claude content block for images vs PDFs."""
    if media_type in _IMAGE_TYPES:
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": data_b64},
        }
    # PDF
    return {
        "type": "document",
        "source": {"type": "base64", "media_type": _PDF_TYPE, "data": data_b64},
    }


@router.post("/extract-cobra")
async def extract_cobra_election(
    file: UploadFile = File(...),
    turnstile_token: str = Form(...),
):
    await verify_turnstile(turnstile_token)

    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")

    content_type = file.content_type or ""
    if content_type not in _IMAGE_TYPES and content_type != _PDF_TYPE:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {content_type}. Upload a JPEG, PNG, or PDF.",
        )

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Empty file")

    b64 = base64.standard_b64encode(file_bytes).decode("utf-8")

    try:
        result = get_instructor_client().messages.create(
            model=MODEL,
            max_tokens=1024,
            max_retries=2,
            response_model=CobraElectionExtraction,
            messages=[
                {
                    "role": "user",
                    "content": [
                        _build_content_block(b64, content_type),
                        {"type": "text", "text": EXTRACTION_PROMPT},
                    ],
                }
            ],
        )
    except anthropic.APIError as e:
        raise HTTPException(status_code=502, detail=f"Anthropic API error: {e}")

    return result.model_dump()
