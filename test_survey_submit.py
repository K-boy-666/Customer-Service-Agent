"""Submit satisfaction survey via REST API for Test Scenario 7."""
import httpx
import asyncio

async def main():
    url = "http://localhost:8000/api/surveys"
    payload = {
        "rating": 5,
        "feedback": "客服小客非常专业，问题迅速解决",
        "customer_id": 0,
        "order_id": ""
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, params=payload)
        print(f"Status: {resp.status_code}")
        print(f"Response: {resp.text}")
        resp.raise_for_status()

asyncio.run(main())
