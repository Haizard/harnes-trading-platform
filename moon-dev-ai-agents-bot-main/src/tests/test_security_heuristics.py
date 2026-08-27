import asyncio
import sys
import os

sys.path.insert(0, os.getcwd())

from src.data.collectors.birdeye_collector import BirdEyeCollector

async def test():
    be = BirdEyeCollector()

    # Simulate token overview dicts with different scenarios
    test_cases = [
        {
            "name": "✅ Good token (passes all)",
            "data": {"liquidity": 10_000, "v1hUSD": 800, "createdAt": 1700000000}
        },
        {
            "name": "❌ Low liquidity",
            "data": {"liquidity": 100, "v1hUSD": 800, "createdAt": 1700000000}
        },
        {
            "name": "❌ Low volume",
            "data": {"liquidity": 10_000, "v1hUSD": 10, "createdAt": 1700000000}
        },
        {
            "name": "❌ Brand new token (just launched)",
            "data": {
                "liquidity": 10_000,
                "v1hUSD": 800,
                # Set createdAt to 5 minutes ago (Unix timestamp)
                "createdAt": __import__("datetime").datetime.utcnow().timestamp() - 300
            }
        },
        {
            "name": "✅ Unknown age (no createdAt field → assume OK)",
            "data": {"liquidity": 10_000, "v1hUSD": 800}
        },
    ]

    print("\n" + "=" * 60)
    print("  🛡️  Token Security Heuristic Filter Tests")
    print("=" * 60)

    for tc in test_cases:
        result = be._passes_heuristics(tc["data"])
        verdict = "PASS" if result else "REJECT"
        print(f"  [{verdict}] {tc['name']}")

    print("=" * 60)
    print("✅ Test complete — no BirdEye security API needed!\n")

if __name__ == "__main__":
    asyncio.run(test())
