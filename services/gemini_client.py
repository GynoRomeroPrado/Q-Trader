"""Gemini Client — Dual-model routing (Flash + Pro) for Sentiment Oracle.

Provides:
    - should_use_pro(): Pure function to decide Flash vs Pro
    - call_flash(): Standard model call
    - call_pro_with_fallback(): Pro with automatic Flash fallback on timeout/error
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class GeminiClientError(Exception):
    """Non-fatal error during Gemini API call."""


def should_use_pro(
    obi: float,
    spread_ratio: float,
    obi_threshold: float,
    spread_multiplier: float,
) -> bool:
    """Pure routing decision — no I/O."""
    return abs(obi) > obi_threshold or spread_ratio > spread_multiplier


async def _call_model(
    prompt: str,
    api_key: str,
    model: str,
    timeout: float,
) -> str:
    """Call Gemini REST API via aiohttp. Returns raw text response.

    Uses the REST endpoint directly (no SDK dependency) so the bot
    never requires google-generativeai to be installed.
    """
    import aiohttp
    from core.sentiment_oracle import _create_session

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    payload: dict[str, Any] = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 1024,
            "responseMimeType": "application/json",
        },
    }

    try:
        async with _create_session() as session:
            async with session.post(
                f"{url}?key={api_key}",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise GeminiClientError(
                        f"Gemini {model} HTTP {resp.status}: {body[:200]}"
                    )
                data = await resp.json()

        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.endswith("```"):
            text = text[:-3]
        return text.strip()

    except GeminiClientError:
        raise
    except asyncio.TimeoutError:
        raise GeminiClientError(f"Gemini {model} timeout ({timeout}s)")
    except Exception as e:
        raise GeminiClientError(f"Gemini {model} error: {e}") from e


async def call_flash(
    prompt: str,
    api_key: str,
    model: str,
    timeout: float,
) -> str:
    """Call the standard (Flash) model."""
    return await _call_model(prompt, api_key, model, timeout)


async def call_pro_with_fallback(
    prompt: str,
    api_key: str,
    pro_model: str,
    flash_model: str,
    timeout: float,
) -> tuple[str, str]:
    """Call Pro model; fallback to Flash on timeout or error.

    Returns:
        (response_text, model_used)  where model_used is "pro" or "flash"
    """
    try:
        text = await asyncio.wait_for(
            _call_model(prompt, api_key, pro_model, timeout * 3),
            timeout=timeout,
        )
        return text, "pro"
    except (asyncio.TimeoutError, GeminiClientError) as e:
        logger.warning("Pro fallback to Flash: %s", e)
        text = await _call_model(prompt, api_key, flash_model, timeout)
        return text, "flash"
