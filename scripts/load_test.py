"""Small laptop-demo load probe; run only against a dedicated demo dataset."""

import argparse
import asyncio
import os
import time

import httpx


async def main(concurrency: int, url: str, api_key: str, user_id: str) -> None:
    async with httpx.AsyncClient(timeout=60) as client:
        started = time.perf_counter()
        async def ask(number: int):
            response = await client.post(
                f"{url.rstrip('/')}/query", headers={"X-Demo-API-Key": api_key},
                json={"user_id": user_id, "agent_id": "load_probe", "query": f"What is my leave balance? ({number})"},
            )
            return response.status_code
        statuses = await asyncio.gather(*(ask(number) for number in range(concurrency)))
    print({"concurrency": concurrency, "elapsed_seconds": round(time.perf_counter() - started, 2), "status_counts": {code: statuses.count(code) for code in sorted(set(statuses))}})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--concurrency", type=int, default=40)
    parser.add_argument("--url", default=os.getenv("CONTEXT_LAYER_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--api-key", default=os.getenv("DEMO_API_KEY"))
    parser.add_argument("--user-id", default=os.getenv("DEMO_USER_ID", "E0001"))
    args = parser.parse_args()
    if not args.api_key:
        parser.error("--api-key or DEMO_API_KEY is required")
    asyncio.run(main(args.concurrency, args.url, args.api_key, args.user_id))
