import asyncio
import os
import sys
from termcolor import cprint

sys.path.append(os.getcwd())

from src.data.collectors.birdeye_collector import BirdEyeCollector
from src.data.collectors.fred import FREDCollector
from src.data.collectors.alpha_vantage import AlphaVantageCollector
from src.data.storage.mongo_db import MongoStorage
from src.config import MONITORED_TOKENS

TIMEOUT = 20  # seconds per test

async def run_with_timeout(coro, label, timeout=TIMEOUT):
    try:
        result = await asyncio.wait_for(coro, timeout=timeout)
        cprint(f"[SUCCESS] {label}", "white", "on_green")
        return result
    except asyncio.TimeoutError:
        cprint(f"[TIMEOUT] {label} timed out after {timeout}s — skipping", "yellow")
    except Exception as e:
        cprint(f"[ERROR] {label}: {str(e)}", "white", "on_red")

async def main():
    cprint("=" * 60, "blue")
    cprint("   🌙 Moon Dev — All Collectors Data Audit", "white", "on_blue")
    cprint("=" * 60, "blue")

    storage = MongoStorage()
    await storage.connect()

    # ── 1. BirdEye ────────────────────────────────────────────
    cprint("\n[1/3] 🦅 BirdEye On-chain Collector", "cyan")
    be = BirdEyeCollector()
    if MONITORED_TOKENS:
        await run_with_timeout(
            be.collect_token_data(MONITORED_TOKENS[0]),
            f"BirdEye token: {MONITORED_TOKENS[0][:10]}..."
        )

    # ── 2. FRED ───────────────────────────────────────────────
    cprint("\n[2/3] 📉 FRED Macro Collector", "cyan")
    fred = FREDCollector()
    await run_with_timeout(
        fred.get_series_data("FEDFUNDS"),
        "FRED — Federal Funds Rate"
    )
    await run_with_timeout(
        fred.get_series_data("DGS10"),
        "FRED — 10Y Treasury Yield"
    )

    # ── 3. Alpha Vantage ──────────────────────────────────────
    cprint("\n[3/3] 🏛️ Alpha Vantage Historical Collector", "cyan")
    av = AlphaVantageCollector()
    await run_with_timeout(
        av.get_crypto_daily(symbol="ETH", market="USD"),
        "Alpha Vantage — ETH/USD Daily",
        timeout=30
    )

    # ── Final Audit ───────────────────────────────────────────
    cprint("\n📊 Final MongoDB Audit:", "white", "on_blue")
    cols = ["trades", "orderbook_snapshots", "tokens", "market_data", "features_dataset"]
    print(f"\n{'Collection':<22} {'Docs':>8}   Status")
    print("-" * 45)
    for col in cols:
        count = await storage.db[col].count_documents({})
        status = "✅" if count > 0 else "❌"
        print(f"{col:<22} {count:>8}   {status}")

    print("-" * 45)
    await storage.close()
    cprint("\n🚀 Audit Complete — Moon Dev Data Engine Ready!", "white", "on_green")

if __name__ == "__main__":
    asyncio.run(main())
