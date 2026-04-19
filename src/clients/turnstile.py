"""
turnstile.py — Cloudflare Turnstile verification helper.
"""

import os

import httpx
from fastapi import HTTPException

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
        raise HTTPException(status_code=400, detail="Captcha verification failed")
