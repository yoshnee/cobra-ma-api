"""
llm.py — Shared Anthropic client and instructor-wrapped client.
"""

import os

import anthropic
import instructor

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
MODEL = "claude-haiku-4-5-20251001"

_client = None
_instructor_client = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def get_instructor_client() -> instructor.Instructor:
    """Return an instructor-patched Anthropic client for structured output."""
    global _instructor_client
    if _instructor_client is None:
        _instructor_client = instructor.from_anthropic(get_client())
    return _instructor_client
