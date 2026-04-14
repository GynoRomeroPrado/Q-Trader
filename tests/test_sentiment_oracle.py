"""Tests for Sentiment Oracle — RSS parsing + Keyword circuit breaker + LLM interface."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.sentiment_oracle import (
    ClaudeProvider,
    GeminiProvider,
    RSSFeedFetcher,
    SentimentOracle,
    SentimentResult,
)


class TestKeywordCircuitBreaker:
    """Layer 1: Deterministic keyword detection."""

    def setup_method(self):
        self.oracle = SentimentOracle(polling_interval=9999)

    def test_detects_hack_keyword(self):
        headlines = ["Binance exchange hacked, millions stolen"]
        result = self.oracle._check_lethal_keywords(headlines)
        assert result is not None
        assert "hacked" in result.lower() or "hack" in result.lower()

    def test_detects_sec_keyword(self):
        headlines = ["SEC sues major crypto exchange"]
        result = self.oracle._check_lethal_keywords(headlines)
        assert result is not None

    def test_detects_crash_keyword(self):
        headlines = ["Bitcoin crash wipes out billions"]
        result = self.oracle._check_lethal_keywords(headlines)
        assert result is not None
        assert "crash" in result.lower()

    def test_detects_ban_keyword(self):
        headlines = ["China issues new crypto ban"]
        result = self.oracle._check_lethal_keywords(headlines)
        assert result is not None

    def test_no_false_positive_on_safe_headlines(self):
        headlines = [
            "Bitcoin reaches new all-time high",
            "Ethereum upgrade successful",
            "DeFi adoption grows in Latin America",
        ]
        result = self.oracle._check_lethal_keywords(headlines)
        assert result is None

    def test_empty_headlines_safe(self):
        result = self.oracle._check_lethal_keywords([])
        assert result is None


class TestLLMProviders:
    """Layer 2: LLM provider interface tests."""

    def test_gemini_without_key_returns_neutral(self):
        provider = GeminiProvider(api_key="")
        result = asyncio.run(provider.analyze(["Bitcoin up 5%"]))
        assert isinstance(result, SentimentResult)
        assert result.sentiment_score == 0.0
        assert result.provider == "gemini-2.0-flash"

    def test_claude_without_key_returns_neutral(self):
        provider = ClaudeProvider(api_key="")
        result = asyncio.run(provider.analyze(["Ethereum staking grows"]))
        assert isinstance(result, SentimentResult)
        assert result.sentiment_score == 0.0
        assert result.provider == "claude-sonnet"

    def test_sentiment_result_structure(self):
        result = SentimentResult(
            sentiment_score=-0.7,
            justification="Market crash detected",
            headlines_analyzed=5,
            provider="test",
        )
        d = result.to_dict()
        assert "sentiment_score" in d
        assert "justification" in d
        assert d["headlines_analyzed"] == 5


class TestSentimentOracle:
    """Integration tests for the oracle state machine."""

    def test_is_market_safe_default(self):
        oracle = SentimentOracle(polling_interval=9999)
        result = asyncio.run(oracle.is_market_safe())
        assert result is True

    def test_get_status_structure(self):
        oracle = SentimentOracle(polling_interval=300)
        status = oracle.get_status()
        assert "market_panic" in status
        assert "panic_reason" in status
        assert "polling_interval" in status
        assert status["polling_interval"] == 300

    def test_panic_threshold_configurable(self):
        oracle = SentimentOracle(
            polling_interval=9999,
            panic_threshold=-0.3,
        )
        assert oracle._panic_threshold == -0.3


class TestRSSFeedFetcher:
    """RSS feed parsing tests (mocked network)."""

    def test_default_feeds_configured(self):
        fetcher = RSSFeedFetcher()
        assert len(fetcher._feeds) == 2
        feed_names = [name for name, _ in fetcher._feeds]
        assert "CoinDesk" in feed_names
        assert "Cointelegraph" in feed_names

    def test_custom_feeds(self):
        feeds = [("TestFeed", "https://example.com/rss")]
        fetcher = RSSFeedFetcher(feeds=feeds)
        assert len(fetcher._feeds) == 1
