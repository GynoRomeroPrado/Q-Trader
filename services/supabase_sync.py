"""Supabase Sync — Optional telemetry push to Supabase REST API.

Pushes aggregated bot status and daily PnL snapshots.
All calls are fire-and-forget; errors are logged and swallowed
to never impact the trading loop.

PREREQUISITE (run once in Supabase Dashboard SQL Editor):
    ALTER TABLE daily_pnl
      ADD CONSTRAINT daily_pnl_date_domain_key UNIQUE (date, domain);

If the constraint does not exist, upsert POSTs will fail with
409 Conflict. If it exists, duplicate rows are merged automatically.

Requires: httpx (already a transitive dep via FastAPI/starlette).
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class SupabaseSyncError(Exception):
    """Non-fatal error during Supabase sync."""


# ── Retry buffers (bounded) ────────────────────────────────
_bot_status_buffer: deque[tuple[str, dict]] = deque(maxlen=50)
_daily_pnl_buffer: deque[tuple[str, dict]] = deque(maxlen=50)


def _is_enabled() -> bool:
    """Check if Supabase integration is enabled."""
    from config.settings import settings
    return settings.supabase.enabled


def _headers(*, upsert: bool = False) -> dict[str, str]:
    """Build Supabase REST headers."""
    from config.settings import settings
    prefer = "resolution=merge-duplicates,return=minimal" if upsert else "return=minimal"
    return {
        "apikey": settings.supabase.service_key,
        "Authorization": f"Bearer {settings.supabase.service_key}",
        "Content-Type": "application/json",
        "Prefer": prefer,
    }


def _base_url() -> str:
    from config.settings import settings
    url = settings.supabase.url.rstrip("/")
    return url


async def _drain_buffer(
    buffer: deque[tuple[str, dict]],
    push_fn,
) -> None:
    """Best-effort drain: pop items and re-push. Stop on first failure."""
    while buffer:
        domain, payload = buffer.popleft()
        try:
            await push_fn(domain, payload, _from_drain=True)
        except Exception:
            buffer.appendleft((domain, payload))
            break


async def push_bot_status(
    domain: str, payload: dict[str, Any], *, _from_drain: bool = False,
) -> None:
    """Push a bot status snapshot to Supabase `bot_status` table.

    Args:
        domain: "crypto" or "stocks"
        payload: {running, paused, last_cycle_ts, last_error, ...}
    """
    if not _is_enabled():
        return

    import httpx

    row = {
        "domain": domain,
        "ts": datetime.now(timezone.utc).isoformat(),
        **payload,
    }

    try:
        async with httpx.AsyncClient(base_url=_base_url(), headers=_headers(), timeout=10.0) as client:
            resp = await client.post("/rest/v1/bot_status", json=row)
            if resp.status_code >= 400:
                logger.warning(
                    "Supabase push_bot_status %s failed: %d %s",
                    domain, resp.status_code, resp.text[:200],
                )
                if not _from_drain:
                    _bot_status_buffer.append((domain, payload))
                return
        # Success — drain any buffered items
        if not _from_drain and _bot_status_buffer:
            await _drain_buffer(_bot_status_buffer, push_bot_status)
    except Exception as e:
        logger.warning("Supabase push_bot_status error: %s", e)
        if not _from_drain:
            _bot_status_buffer.append((domain, payload))


async def push_daily_pnl(
    domain: str, payload: dict[str, Any], *, _from_drain: bool = False,
) -> None:
    """Push daily PnL snapshot to Supabase `daily_pnl` table (upsert).

    Uses on_conflict=date,domain so re-running backfill is idempotent.

    Args:
        domain: "crypto" or "stocks"
        payload: {date, total_pnl, total_trades, win_rate, ...}
    """
    if not _is_enabled():
        return

    import httpx

    row = {
        "domain": domain,
        "ts": datetime.now(timezone.utc).isoformat(),
        **payload,
    }

    try:
        async with httpx.AsyncClient(
            base_url=_base_url(),
            headers=_headers(upsert=True),
            timeout=10.0,
        ) as client:
            resp = await client.post(
                "/rest/v1/daily_pnl?on_conflict=date,domain",
                json=row,
            )
            if resp.status_code >= 400:
                logger.warning(
                    "Supabase push_daily_pnl %s failed: %d %s",
                    domain, resp.status_code, resp.text[:200],
                )
                if not _from_drain:
                    _daily_pnl_buffer.append((domain, payload))
                return
        # Success — drain any buffered items
        if not _from_drain and _daily_pnl_buffer:
            await _drain_buffer(_daily_pnl_buffer, push_daily_pnl)
    except Exception as e:
        logger.warning("Supabase push_daily_pnl error: %s", e)
        if not _from_drain:
            _daily_pnl_buffer.append((domain, payload))
