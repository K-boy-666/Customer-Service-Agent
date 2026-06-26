"""Submit satisfaction survey via REST API - fallback for MCP unavailability."""
import httpx
import asyncio

async def main():
    url = "http://localhost:8000/api/surveys"
    payload = {
        "rating": 5,
        "feedback": "客服小客非常专业，问题迅速解决"
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, params=payload)
        print(f"Status: {resp.status_code}")
        print(f"Response: {resp.text}")
        return resp.status_code, resp.json()

result = asyncio.run(main())
print(f"\nFinal result: {result}")
