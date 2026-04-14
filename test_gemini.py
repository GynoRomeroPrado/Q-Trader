import asyncio
import aiohttp
import os
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

async def test():
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
    prompt = "Reply with exactly: {\"status\": \"ok\"}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 1024,
            "responseMimeType": "application/json",
        },
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{url}?key={api_key}", json=payload) as resp:
            text = await resp.text()
            print(resp.status)
            print(text)

asyncio.run(test())
