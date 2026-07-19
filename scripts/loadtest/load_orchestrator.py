#!/usr/bin/env python3
"""Standalone load test script: starts uvicorn, fires concurrent requests via httpx.

Usage:
    python scripts/loadtest/load_orchestrator.py --concurrency 50 --duration 10

Not part of pytest gate. Run manually for performance baselines.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

import httpx
from security import create_dev_jwt

# Add src to path
SRC = Path(__file__).resolve().parent.parent.parent / "src"
sys.path.insert(0, str(SRC))

BASE_URL = "http://127.0.0.1:8000"


async def load_test(concurrency: int, duration: int) -> None:
    """Fire concurrent requests at /api/orchestrator/respond for the given duration."""
    latencies: list[float] = []
    errors = 0
    total = 0

    # Generate a dev JWT with analytics role (has read + orchestrator perms via admin).
    # Use admin role to cover both /api/orders (order:read) and /api/orchestrator/respond.
    jwt_token = create_dev_jwt("loadtest-user", "admin")
    headers = {"Authorization": f"Bearer {jwt_token}"}

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30, headers=headers) as client:
        # Seed: get an order ID from the API
        resp = await client.get("/api/orders?limit=1")
        if resp.status_code != 200:
            print(f"ERROR: Cannot reach API at {BASE_URL} (status={resp.status_code}). Is the server running?")
            return
        orders = resp.json().get("orders", [])
        if not orders:
            print("ERROR: No orders in database. Run seed_data first.")
            return
        order_id = orders[0]["id"]

        async def worker(client: httpx.AsyncClient, worker_id: int) -> None:
            nonlocal errors, total
            messages = [
                f"订单 {order_id} 物流到哪里了?",
                "你好,请问有什么服务?",
                f"我要退货 订单 {order_id}",
            ]
            while True:
                msg = messages[total % len(messages)]
                start = time.perf_counter()
                try:
                    resp = await client.post(
                        "/api/orchestrator/respond",
                        json={
                            "message": msg,
                            "conversation_id": f"load-{worker_id}-{total}",
                        },
                    )
                    if resp.status_code != 200:
                        errors += 1
                except Exception:
                    errors += 1
                latencies.append(time.perf_counter() - start)
                total += 1

        tasks = [asyncio.create_task(worker(client, i)) for i in range(concurrency)]
        await asyncio.sleep(duration)
        for t in tasks:
            t.cancel()

    # Report
    latencies.sort()
    n = len(latencies)
    if n == 0:
        print("No requests completed.")
        return

    p50 = latencies[n // 2]
    p95 = latencies[int(n * 0.95)]
    p99 = latencies[int(n * 0.99)]
    qps = n / duration

    print(f"\n{'='*50}")
    print(f"Load Test Results")
    print(f"{'='*50}")
    print(f"Concurrency:    {concurrency}")
    print(f"Duration:       {duration}s")
    print(f"Total requests: {n}")
    print(f"Errors:         {errors}")
    print(f"QPS:            {qps:.1f}")
    print(f"P50:            {p50*1000:.0f}ms")
    print(f"P95:            {p95*1000:.0f}ms")
    print(f"P99:            {p99*1000:.0f}ms")
    print(f"Mean:           {statistics.mean(latencies)*1000:.0f}ms")
    print(f"{'='*50}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Load test the orchestrator API")
    parser.add_argument("--concurrency", type=int, default=20, help="Number of concurrent workers")
    parser.add_argument("--duration", type=int, default=10, help="Test duration in seconds")
    args = parser.parse_args()

    asyncio.run(load_test(args.concurrency, args.duration))


if __name__ == "__main__":
    main()
