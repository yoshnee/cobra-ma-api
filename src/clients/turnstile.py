"""
turnstile.py — Cloudflare Turnstile verification helper.
"""

import logging
import os

import httpx
from fastapi import HTTPException

logger = logging.getLogger(__name__)

TURNSTILE_SECRET_KEY = os.environ.get("TURNSTILE_SECRET_KEY", "")
SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


async def verify_turnstile(token: str) -> None:
    if not TURNSTILE_SECRET_KEY:
        return  # skip verification if not configured (local dev)

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            SITEVERIFY_URL,
            data={"secret": TURNSTILE_SECRET_KEY, "response": token},
        )

    result = resp.json()
    if not result.get("success"):
        error_codes = result.get("error-codes", [])
        logger.warning("Turnstile verification failed: %s", error_codes)
        raise HTTPException(
            status_code=400,
            detail=f"Captcha verification failed: {', '.join(error_codes) or 'unknown'}",
        )
