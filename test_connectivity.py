"""Test aiohttp with ThreadedResolver (Windows DNS fix)."""
import asyncio
import aiohttp
from aiohttp.resolver import ThreadedResolver
import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent / ".env")

def _create_session(**kwargs):
    connector = aiohttp.TCPConnector(
        resolver=ThreadedResolver(),
        use_dns_cache=True,
        ttl_dns_cache=300,
    )
    return aiohttp.ClientSession(connector=connector, **kwargs)

async def test():
    print("=== Testing with ThreadedResolver ===\n")

    # Test RSS
    urls = [
        ("CoinDesk RSS", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
        ("Cointelegraph RSS", "https://cointelegraph.com/rss"),
    ]
    async with _create_session() as session:
        for name, url in urls:
            try:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=10),
                    headers={"User-Agent": "Mozilla/5.0 (Q-Trader/3.0)"},
                ) as resp:
                    text = await resp.text()
                    print(f"  ✅ {name}: HTTP {resp.status} | {len(text)} bytes")
            except Exception as e:
                print(f"  ❌ {name}: {type(e).__name__} — {e}")

    # Test Gemini API
    api_key = os.getenv("GEMINI_API_KEY", "")
    print(f"\n  API Key: {'SET (' + api_key[:10] + '...)' if api_key else 'MISSING'}")

    if api_key:
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
        payload = {
            "contents": [{"parts": [{"text": "Reply with exactly: {\"status\": \"ok\"}"}]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 32},
        }
        try:
            async with _create_session() as session:
                async with session.post(
                    f"{url}?key={api_key}",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    body = await resp.text()
                    print(f"  Gemini API: HTTP {resp.status}")
                    print(f"  Response: {body[:300]}")
        except Exception as e:
            print(f"  ❌ Gemini API: {type(e).__name__} — {e}")

asyncio.run(test())
