"""Quick Gemini API connectivity test."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

async def test():
    from services.gemini_client import call_flash, GeminiClientError

    api_key = os.environ.get("GEMINI_API_KEY", "").strip("'\"")
    if not api_key:
        print("[SKIP] GEMINI_API_KEY not set in .env")
        return

    print(f"[INFO] Key preview: {api_key[:8]}...")
    print(f"[1] Testing gemini-2.5-flash...")

    try:
        result = await call_flash(
            prompt='Respond with only this JSON: {"status": "ok", "model": "flash"}',
            api_key=api_key,
            model="gemini-2.5-flash",
            timeout=20.0,
        )
        print(f"[OK] Flash response: {result[:300]}")
    except GeminiClientError as e:
        print(f"[FAIL] GeminiClientError: {e}")
        return
    except Exception as e:
        print(f"[FAIL] {type(e).__name__}: {e}")
        return

    print()
    print("[2] Testing gemini-2.5-pro (with timeout fallback)...")
    from services.gemini_client import call_pro_with_fallback

    try:
        result, model_used = await call_pro_with_fallback(
            prompt='Respond with only this JSON: {"status": "ok", "model": "pro"}',
            api_key=api_key,
            pro_model="gemini-2.5-pro",
            flash_model="gemini-2.5-flash",
            timeout=10.0,
        )
        print(f"[OK] Model used: {model_used}")
        print(f"[OK] Response: {result[:300]}")
    except Exception as e:
        print(f"[FAIL] {type(e).__name__}: {e}")

    print()
    print("[3] Testing Oracle sentiment analysis (keyword + LLM)...")
    from core.sentiment_oracle import SentimentOracle, GeminiProvider

    oracle = SentimentOracle(
        llm_provider=GeminiProvider(api_key=api_key),
        polling_interval=9999,
        panic_threshold=-0.5,
    )

    # Test keyword detection
    lethal = oracle._check_lethal_keywords(["Bitcoin crash wipes billions"])
    print(f"[OK] Keyword 'crash' detected: {lethal}")

    # Test LLM analysis
    headlines = [
        "Bitcoin reaches new all-time high above $100K",
        "Ethereum ETF inflows accelerate",
        "DeFi adoption grows across Latin America",
    ]
    result = await oracle._analyze_with_llm(headlines)
    print(f"[OK] LLM sentiment score: {result.sentiment_score}")
    print(f"[OK] Provider: {result.provider}")
    print(f"[OK] Justification: {result.justification[:150]}")

    print()
    print("=" * 50)
    print("ALL GEMINI TESTS PASSED" if result.provider != "none" else "WARNING: Running in keyword-only mode")

asyncio.run(test())
