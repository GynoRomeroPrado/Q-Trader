"""Sentiment Oracle — LLM-Ready Macroeconomic Risk Circuit Breaker (Cerebro 3).

Architecture (2-Layer):
    Layer 1 — Deterministic (Pure Code):
        Keyword circuit breaker scans RSS titles for lethal terms.
        O(1) execution, zero network latency. Always runs first.

    Layer 2 — LLM Sentiment Analysis:
        RSS headlines → structured prompt → LLM Provider → JSON response.
        Providers: GeminiProvider, ClaudeProvider (pluggable via abstract base).
        Fallback: If LLM fails/timeout → safe-by-default (no panic).

Data Sources (100% Free):
    - CoinDesk RSS: https://www.coindesk.com/arc/outboundfeeds/rss/
    - Cointelegraph RSS: https://cointelegraph.com/rss

Polling: Every 5 minutes (300 seconds).
"""

from __future__ import annotations

import abc
import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

import aiohttp
from aiohttp.resolver import ThreadedResolver

try:
    import feedparser
except ImportError:
    feedparser = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from core.audit_logger import AuditLogger

logger = logging.getLogger(__name__)


def _create_session(**kwargs) -> aiohttp.ClientSession:
    """Create an aiohttp session with ThreadedResolver.
    
    Windows fix: aiohttp's default AsyncResolver (c-ares/aiodns) often fails
    with 'Could not contact DNS servers'. ThreadedResolver uses the OS DNS
    resolver via threading, which always works.
    """
    connector = aiohttp.TCPConnector(
        resolver=ThreadedResolver(),
        use_dns_cache=True,
        ttl_dns_cache=300,
    )
    return aiohttp.ClientSession(connector=connector, **kwargs)


# ──────────────────────────────────────────────────────────────
# LLM Sentiment Response Schema
# ──────────────────────────────────────────────────────────────

@dataclass
class SentimentResult:
    """Structured output from LLM sentiment analysis."""
    sentiment_score: float = 0.0        # -1.0 (extreme fear) to +1.0 (extreme greed)
    justification: str = ""             # LLM's reasoning
    headlines_analyzed: int = 0         # Number of headlines processed
    provider: str = "none"              # Which LLM processed this
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    raw_response: str = ""              # Full LLM response for debugging

    def to_dict(self) -> dict[str, Any]:
        return {
            "sentiment_score": self.sentiment_score,
            "justification": self.justification,
            "headlines_analyzed": self.headlines_analyzed,
            "provider": self.provider,
            "timestamp": self.timestamp,
        }


# ──────────────────────────────────────────────────────────────
# Abstract LLM Provider
# ──────────────────────────────────────────────────────────────

class LLMSentimentProvider(abc.ABC):
    """Abstract base for LLM-based sentiment analysis providers.

    Implementations must:
        1. Accept a list of headline strings
        2. Return a SentimentResult with score and justification
        3. Handle their own auth/rate-limiting
    """

    @abc.abstractmethod
    async def analyze(self, headlines: list[str]) -> SentimentResult:
        """Analyze headlines and return structured sentiment."""
        ...

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Provider identifier."""
        ...


class GeminiProvider(LLMSentimentProvider):
    """Google Gemini 2.0 Flash sentiment provider.

    TODO: Implement actual API call when API key is available.
    Currently returns neutral sentiment as placeholder.
    """

    SYSTEM_PROMPT = """You are a quantitative financial sentiment analyst specializing in cryptocurrency markets.

Analyze the following news headlines and return a JSON object with exactly these fields:
{
    "sentiment_score": <float from -1.0 to 1.0>,
    "justification": "<1-2 sentence explanation of your assessment>"
}

Scoring guide:
- -1.0 to -0.7: Extreme fear (regulatory crackdown, major hack, market crash)
- -0.7 to -0.3: Bearish (negative regulation, declining metrics)
- -0.3 to 0.3: Neutral (mixed signals, routine news)
- 0.3 to 0.7: Bullish (adoption news, positive regulation)
- 0.7 to 1.0: Extreme greed (parabolic moves, FOMO indicators)

CRITICAL INSTRUCTION: Return ONLY the raw JSON object. Do not include markdown formatting (like ```json), and NEVER use double quotes (") inside the justification text to avoid breaking the JSON. Use single quotes instead."""

    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key

    @property
    def name(self) -> str:
        return "gemini-2.0-flash"

    async def analyze(self, headlines: list[str]) -> SentimentResult:
        """Analyze via Gemini API. Falls back to neutral if unconfigured."""
        if not self._api_key:
            logger.debug("GeminiProvider: Sin API key, retornando neutral.")
            return SentimentResult(
                sentiment_score=0.0,
                justification="LLM no configurado — modo neutral seguro.",
                headlines_analyzed=len(headlines),
                provider=self.name,
            )

        # ── Future Implementation ──
        # from google import genai
        # client = genai.Client(api_key=self._api_key)
        # prompt = self.SYSTEM_PROMPT + "\n\nHeadlines:\n" + "\n".join(f"- {h}" for h in headlines)
        # response = await client.aio.models.generate_content(
        #     model="gemini-2.0-flash",
        #     contents=prompt,
        # )
        # parsed = json.loads(response.text)
        # return SentimentResult(
        #     sentiment_score=float(parsed["sentiment_score"]),
        #     justification=parsed.get("justification", ""),
        #     headlines_analyzed=len(headlines),
        #     provider=self.name,
        #     raw_response=response.text,
        # )

        try:
            url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
            prompt = self.SYSTEM_PROMPT + "\n\nHeadlines:\n" + "\n".join(
                f"- {h}" for h in headlines
            )

            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.1,
                    "maxOutputTokens": 1024,
                    "responseMimeType": "application/json",
                },
            }

            async with _create_session() as session:
                async with session.post(
                    f"{url}?key={self._api_key}",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.warning(f"Gemini API error {resp.status}: {body[:200]}")
                        return SentimentResult(
                            provider=self.name,
                            justification=f"API error {resp.status}",
                            headlines_analyzed=len(headlines),
                        )

                    data = await resp.json()

            # Parse response safely handling potential markdown fences
            text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            if text.startswith("```json"):
                text = text[7:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
            parsed = json.loads(text)

            return SentimentResult(
                sentiment_score=max(-1.0, min(1.0, float(parsed["sentiment_score"]))),
                justification=parsed.get("justification", ""),
                headlines_analyzed=len(headlines),
                provider=self.name,
                raw_response=text,
            )

        except Exception as e:
            logger.warning(f"GeminiProvider fallback (error: {e})")
            return SentimentResult(
                provider=self.name,
                justification=f"Error: {e}",
                headlines_analyzed=len(headlines),
            )


class ClaudeProvider(LLMSentimentProvider):
    """Anthropic Claude sentiment provider.

    TODO: Implement actual API call when API key is available.
    Currently returns neutral sentiment as placeholder.
    """

    SYSTEM_PROMPT = """Analyze these cryptocurrency news headlines for market sentiment.
Return JSON: {"sentiment_score": <-1.0 to 1.0>, "justification": "<explanation>"}
Only return the JSON object."""

    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key

    @property
    def name(self) -> str:
        return "claude-sonnet"

    async def analyze(self, headlines: list[str]) -> SentimentResult:
        """Analyze via Claude API. Falls back to neutral if unconfigured."""
        if not self._api_key:
            logger.debug("ClaudeProvider: Sin API key, retornando neutral.")
            return SentimentResult(
                sentiment_score=0.0,
                justification="LLM no configurado — modo neutral seguro.",
                headlines_analyzed=len(headlines),
                provider=self.name,
            )

        # ── Future Implementation ──
        # import anthropic
        # client = anthropic.AsyncAnthropic(api_key=self._api_key)
        # message = await client.messages.create(
        #     model="claude-sonnet-4-20250514",
        #     max_tokens=256,
        #     messages=[{"role": "user", "content": prompt}],
        # )
        # ...

        try:
            url = "https://api.anthropic.com/v1/messages"
            prompt = self.SYSTEM_PROMPT + "\n\nHeadlines:\n" + "\n".join(
                f"- {h}" for h in headlines
            )

            headers = {
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
            payload = {
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 256,
                "messages": [{"role": "user", "content": prompt}],
            }

            async with _create_session() as session:
                async with session.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.warning(f"Claude API error {resp.status}: {body[:200]}")
                        return SentimentResult(
                            provider=self.name,
                            justification=f"API error {resp.status}",
                            headlines_analyzed=len(headlines),
                        )

                    data = await resp.json()

            text = data["content"][0]["text"]
            parsed = json.loads(text)

            return SentimentResult(
                sentiment_score=max(-1.0, min(1.0, float(parsed["sentiment_score"]))),
                justification=parsed.get("justification", ""),
                headlines_analyzed=len(headlines),
                provider=self.name,
                raw_response=text,
            )

        except Exception as e:
            logger.warning(f"ClaudeProvider fallback (error: {e})")
            return SentimentResult(
                provider=self.name,
                justification=f"Error: {e}",
                headlines_analyzed=len(headlines),
            )


# ──────────────────────────────────────────────────────────────
# RSS Feed Fetcher
# ──────────────────────────────────────────────────────────────

class RSSFeedFetcher:
    """Async RSS feed reader using aiohttp + feedparser."""

    DEFAULT_FEEDS = [
        ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
        ("Cointelegraph", "https://cointelegraph.com/rss"),
    ]

    def __init__(self, feeds: list[tuple[str, str]] | None = None, audit: AuditLogger | None = None) -> None:
        self._feeds = feeds or self.DEFAULT_FEEDS
        self._audit = audit

    async def fetch_headlines(self, max_per_feed: int = 15) -> list[str]:
        """Fetch latest headlines from all configured RSS feeds."""
        if feedparser is None:
            logger.error("feedparser no instalado. pip install feedparser")
            if self._audit:
                await self._audit.log_action(
                    source="RSSFetcher", action="FEEDPARSER_MISSING",
                    detail={"error": "feedparser not installed"}, level="ERROR",
                )
            return []

        all_headlines: list[str] = []

        async with _create_session() as session:
            for name, url in self._feeds:
                try:
                    async with session.get(
                        url,
                        timeout=aiohttp.ClientTimeout(total=10),
                        headers={"User-Agent": "Q-Trader/1.0 RSS Reader"},
                    ) as resp:
                        if resp.status != 200:
                            logger.warning(f"RSS {name}: HTTP {resp.status}")
                            if self._audit:
                                await self._audit.log_action(
                                    source="RSSFetcher", action="RSS_HTTP_ERROR",
                                    detail={"feed": name, "status": resp.status}, level="WARNING",
                                )
                            continue

                        xml_text = await resp.text()

                    feed = feedparser.parse(xml_text)
                    entries = feed.entries[:max_per_feed]
                    titles = [
                        entry.get("title", "").strip()
                        for entry in entries
                        if entry.get("title", "").strip()
                    ]

                    all_headlines.extend(titles)
                    logger.info(f"RSS {name}: {len(titles)} titulares obtenidos")
                    if self._audit:
                        await self._audit.log_action(
                            source="RSSFetcher", action="RSS_FETCH_OK",
                            detail={"feed": name, "headlines": len(titles)},
                        )

                except asyncio.TimeoutError:
                    logger.warning(f"RSS {name}: Timeout alcanzado")
                    if self._audit:
                        await self._audit.log_action(
                            source="RSSFetcher", action="RSS_TIMEOUT",
                            detail={"feed": name}, level="WARNING",
                        )
                except Exception as e:
                    logger.warning(f"RSS {name}: Error parsing: {e}")
                    if self._audit:
                        await self._audit.log_action(
                            source="RSSFetcher", action="RSS_PARSE_ERROR",
                            detail={"feed": name, "error": str(e)[:100]}, level="ERROR",
                        )

        return all_headlines


# ──────────────────────────────────────────────────────────────
# Sentiment Oracle (Main Class)
# ──────────────────────────────────────────────────────────────

class SentimentOracle:
    """2-Layer Macroeconomic Risk Evaluator.

    Layer 1: Deterministic keyword circuit breaker (O(1), no network).
    Layer 2: LLM-based sentiment analysis (pluggable providers).

    Acts as Circuit Breaker inside TradeExecutor.
    """

    # Keywords that trigger instant panic without LLM analysis
    LETHAL_KEYWORDS: set[str] = {
        "hack", "hacked", "sec", "crash",
        "exploit", "exploited", "bankrupt", "bankruptcy",
        "lawsuit", "delist", "delisting", "subpoena", "fraud",
        "ponzi", "rugpull", "rug pull", "insolvent",
        "ban", "banned",
    }

    def __init__(
        self,
        llm_provider: LLMSentimentProvider | None = None,
        polling_interval: int = 300,   # 5 minutes
        panic_threshold: float = -0.5,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._provider = llm_provider or GeminiProvider()
        self._audit = audit_logger
        self._rss = RSSFeedFetcher(audit=audit_logger)
        self._polling_interval = polling_interval
        self._panic_threshold = panic_threshold

        # State
        self._market_panic: bool = False
        self._panic_reason: str = ""
        self._last_result: SentimentResult | None = None
        self._last_headlines: list[str] = []
        self._last_error: str = ""  # Last error for dashboard diagnostics

        # Gemini dual-model state
        self._analysis_depth: str = "normal"   # "normal" | "elevated" | "critical"
        self._pro_last_called: float = 0.0     # time.monotonic() of last Pro call
        self._model_used_last: str = "flash"

        # Background task
        self._oracle_task: asyncio.Task | None = None
        self._is_running = False

    async def start(self) -> None:
        """Start the background sentiment polling daemon."""
        self._is_running = True
        self._oracle_task = asyncio.create_task(self._poll_loop())
        provider_name = self._provider.name
        has_key = bool(getattr(self._provider, '_api_key', ''))
        logger.info(
            f"🧠 Cerebro 3 (Sentiment Oracle) iniciado | "
            f"Provider: {provider_name} | "
            f"API Key: {'✅ SET' if has_key else '❌ MISSING'} | "
            f"Polling: {self._polling_interval}s | "
            f"Panic Threshold: {self._panic_threshold}"
        )
        if self._audit:
            await self._audit.log_action(
                source="Oracle", action="ORACLE_STARTED",
                detail={
                    "provider": provider_name,
                    "api_key_set": has_key,
                    "polling_s": self._polling_interval,
                    "panic_threshold": self._panic_threshold,
                },
            )

    async def stop(self) -> None:
        """Stop the oracle gracefully."""
        self._is_running = False
        if self._oracle_task:
            self._oracle_task.cancel()
            try:
                await self._oracle_task
            except asyncio.CancelledError:
                pass
        logger.info("🧠 Cerebro 3 detenido")

    @property
    def panic_reason(self) -> str:
        return self._panic_reason

    @property
    def last_result(self) -> SentimentResult | None:
        return self._last_result

    @property
    def last_headlines(self) -> list[str]:
        return self._last_headlines

    # ──────────────────────────────────────────────────────────
    # Layer 1: Deterministic Keyword Scanner
    # ──────────────────────────────────────────────────────────

    def _check_lethal_keywords(self, headlines: list[str]) -> str | None:
        """Scan headlines for catastrophic keywords. O(N*M) but fast for small N."""
        for title in headlines:
            words = set(title.lower().replace(",", "").replace(".", "").split())
            intersection = self.LETHAL_KEYWORDS.intersection(words)
            if intersection:
                keyword = intersection.pop()
                return f"Keyword letal '{keyword}' en: {title[:80]}"
        return None

    # ──────────────────────────────────────────────────────────
    # Layer 2: LLM Sentiment Analysis
    # ──────────────────────────────────────────────────────────

    async def _analyze_with_llm(self, headlines: list[str]) -> SentimentResult:
        """Delegate sentiment analysis to configured LLM provider.

        When a Gemini API key is available, calls gemini_client.call_flash
        directly.  Otherwise falls back to the pluggable provider (which
        returns neutral when unconfigured).
        """
        from config.settings import settings

        api_key = settings.gemini.api_key
        if api_key:
            try:
                from services.gemini_client import call_flash
                prompt = GeminiProvider.SYSTEM_PROMPT + "\n\nHeadlines:\n" + "\n".join(
                    f"- {h}" for h in headlines
                )
                text = await call_flash(
                    prompt=prompt,
                    api_key=api_key,
                    model=settings.gemini.standard_model,
                    timeout=15.0,
                )
                parsed = json.loads(text)
                result = SentimentResult(
                    sentiment_score=max(-1.0, min(1.0, float(parsed["sentiment_score"]))),
                    justification=parsed.get("justification", ""),
                    headlines_analyzed=len(headlines),
                    provider=settings.gemini.standard_model,
                    raw_response=text,
                )
                self._last_result = result
                self._model_used_last = "flash"
                return result
            except Exception as e:
                logger.warning("Gemini Flash fallback: %s", e)
                # Fall through to provider

        try:
            result = await self._provider.analyze(headlines)
            self._last_result = result
            return result
        except Exception as e:
            logger.error(f"LLM analysis failed: {e}")
            return SentimentResult(
                sentiment_score=0.0,
                justification=f"LLM fallback — error: {e}",
                headlines_analyzed=len(headlines),
                provider=self._provider.name,
            )

    # ──────────────────────────────────────────────────────────
    # Gemini Pro — Deep Analysis (Non-Blocking)
    # ──────────────────────────────────────────────────────────

    def notify_market_conditions(self, obi: float, spread_ratio: float) -> None:
        """Called from PERCEPTION — evaluates whether to trigger Pro analysis.

        NEVER blocks. Returns in microseconds.
        """
        from config.settings import settings
        from services.gemini_client import should_use_pro

        if not settings.gemini.api_key:
            return

        needs_pro = should_use_pro(
            obi=obi,
            spread_ratio=spread_ratio,
            obi_threshold=settings.gemini.pro_obi_threshold,
            spread_multiplier=settings.gemini.pro_spread_multiplier,
        )

        if not needs_pro:
            self._analysis_depth = "normal"
            return

        elapsed = time.monotonic() - self._pro_last_called
        if elapsed >= settings.gemini.pro_cooldown_seconds:
            asyncio.create_task(self._run_pro_analysis(obi, spread_ratio))
        else:
            self._analysis_depth = "elevated"

    async def _run_pro_analysis(self, obi: float, spread_ratio: float) -> None:
        """Deep analysis via Gemini Pro (or Flash fallback). Fire-and-forget."""
        from config.settings import settings
        from services.gemini_client import call_pro_with_fallback

        try:
            last_score = self._last_result.sentiment_score if self._last_result else 0.0
            last_reason = self._last_result.justification if self._last_result else "N/A"

            prompt = (
                f"DEEP MARKET STRESS ANALYSIS\n"
                f"OBI: {obi:.4f}\n"
                f"Spread ratio: {spread_ratio:.2f}\n"
                f"Last sentiment score: {last_score:.2f}\n"
                f"Last reason: {last_reason}\n\n"
                f"Evaluate if this constitutes market panic. "
                f'Return JSON: {{"panic": true/false, "severity": "low"|"medium"|"high", '
                f'"justification": "<1 sentence>"}}'
            )

            text, model_used = await call_pro_with_fallback(
                prompt=prompt,
                api_key=settings.gemini.api_key,
                pro_model=settings.gemini.pro_model,
                flash_model=settings.gemini.standard_model,
                timeout=settings.gemini.pro_timeout_seconds,
            )

            self._analysis_depth = "critical"
            self._pro_last_called = time.monotonic()
            self._model_used_last = model_used

            parsed = json.loads(text)
            if parsed.get("panic", False):
                self._market_panic = True
                self._panic_reason = (
                    f"Pro analysis [{model_used}]: {parsed.get('justification', 'stress detected')}"
                )
                logger.warning("Pro analysis triggered panic: %s", self._panic_reason)

        except Exception as e:
            logger.error("Pro analysis failed (non-fatal): %s", e)

    # ──────────────────────────────────────────────────────────
    # Polling Loop
    # ──────────────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        """Background polling loop — RSS → Keywords → LLM → State Update.
        
        First iteration runs IMMEDIATELY (no initial sleep).
        """
        first_run = True
        while self._is_running:
            try:
                if self._audit:
                    await self._audit.log_action(
                        source="Oracle", action="POLL_CYCLE_START",
                        detail={"first_run": first_run},
                    )

                # 1. Fetch RSS headlines
                headlines = await self._rss.fetch_headlines(max_per_feed=15)
                self._last_headlines = headlines

                if not headlines:
                    self._last_error = "RSS feeds returned 0 headlines (DNS/network issue)"
                    logger.warning("Oracle: Sin titulares RSS. Manteniendo estado anterior.")
                    if self._audit:
                        await self._audit.log_action(
                            source="Oracle", action="NO_HEADLINES",
                            detail={"feeds_configured": len(self._rss._feeds)},
                            level="WARNING",
                        )
                    await asyncio.sleep(self._polling_interval)
                    continue

                if self._audit:
                    await self._audit.log_action(
                        source="Oracle", action="HEADLINES_FETCHED",
                        detail={"count": len(headlines), "sample": headlines[0][:60] if headlines else ""},
                    )

                # 2. Layer 1: Deterministic keyword scan (FIRST — no latency)
                lethal_hit = self._check_lethal_keywords(headlines)
                if lethal_hit:
                    if not self._market_panic:
                        logger.error(f"🚨 CIRCUIT BREAKER ACTIVADO (Keyword): {lethal_hit}")
                    self._market_panic = True
                    self._panic_reason = lethal_hit
                    if self._audit:
                        await self._audit.log_action(
                            source="Oracle", action="CIRCUIT_BREAKER_KEYWORD",
                            detail={"reason": lethal_hit}, level="CRITICAL",
                        )
                    await asyncio.sleep(self._polling_interval)
                    continue

                # 3. Layer 2: LLM sentiment analysis
                if self._audit:
                    await self._audit.log_action(
                        source="Oracle", action="LLM_ANALYSIS_START",
                        detail={"provider": self._provider.name, "headlines": len(headlines)},
                    )

                result = await self._analyze_with_llm(headlines)

                if self._audit:
                    await self._audit.log_action(
                        source="Oracle", action="LLM_ANALYSIS_RESULT",
                        detail={
                            "score": result.sentiment_score,
                            "provider": result.provider,
                            "justification": result.justification[:80],
                        },
                    )

                if result.sentiment_score < self._panic_threshold:
                    if not self._market_panic:
                        logger.error(
                            f"🚨 CIRCUIT BREAKER ACTIVADO (LLM Sentiment): "
                            f"{result.sentiment_score:.2f} — {result.justification}"
                        )
                    self._market_panic = True
                    self._panic_reason = (
                        f"LLM [{result.provider}] sentiment: {result.sentiment_score:.2f} "
                        f"— {result.justification}"
                    )
                else:
                    if self._market_panic:
                        logger.info(
                            f"🟢 Pánico disipado. LLM score: {result.sentiment_score:.2f}"
                        )
                    self._market_panic = False
                    self._panic_reason = ""

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Oracle polling error: {e}", exc_info=True)
                if self._audit:
                    await self._audit.log_action(
                        source="Oracle", action="POLL_ERROR",
                        detail={"error": str(e)[:120]}, level="ERROR",
                    )
                self._last_error = str(e)[:120]

            first_run = False
            await asyncio.sleep(self._polling_interval)

    # ──────────────────────────────────────────────────────────
    # Public Interface
    # ──────────────────────────────────────────────────────────

    async def is_market_safe(self) -> bool:
        """Check if market conditions allow trading.

        Called by TradeExecutor before every order.
        """
        return not self._market_panic

    def get_status(self) -> dict[str, Any]:
        """Return current oracle status for dashboard/logging."""
        from config.settings import settings
        return {
            "market_panic": self._market_panic,
            "panic_reason": self._panic_reason,
            "last_score": self._last_result.sentiment_score if self._last_result else None,
            "last_provider": self._last_result.provider if self._last_result else None,
            "headlines_cached": len(self._last_headlines),
            "polling_interval": self._polling_interval,
            "last_error": self._last_error,
            "network_ok": len(self._last_headlines) > 0 or self._last_result is not None,
            "analysis_depth": self._analysis_depth,
            "model_used_last": self._model_used_last,
            "pro_cooldown_remaining": max(
                0,
                settings.gemini.pro_cooldown_seconds
                - (time.monotonic() - self._pro_last_called),
            ),
        }
