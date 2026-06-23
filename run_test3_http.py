"""
Attempt to call CREATE RETURN via REST API directly.
"""
import httpx
import asyncio

async def main():
    url = "http://localhost:8000/api/returns"
    params = {
        "order_id": "ORD-20260601-001",
        "type": "refund",
        "reason": "无线鼠标右键不灵敏",
        "description": "客户反馈商品右键不灵敏，影响正常使用。申请仅退款处理。",
        "customer_id": 1,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, params=params)
            print(f"Status: {resp.status_code}")
            print(f"Response: {resp.text}")
            if resp.status_code == 201:
                data = resp.json()
                print(f"\n[TEST-3-RESULT: 成功] create_return 成功调用")
                print(f"RMA单号: {data.get('return_number')}")
                print(f"状态: {data.get('status')}")
            else:
                print(f"\n[TEST-3-RESULT: 失败] HTTP {resp.status_code}")
    except Exception as e:
        print(f"\n[TEST-3-RESULT: 失败] 连接REST API失败: {e}")

asyncio.run(main())
