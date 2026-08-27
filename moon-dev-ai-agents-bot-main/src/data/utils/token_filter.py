"""
🌙 Moon Dev's Token Filter
Smart pre-filtering using self-computed metrics from MongoDB
Built with love by Moon Dev 🚀
"""

import asyncio
from datetime import datetime, timedelta
from termcolor import cprint

from src.data.storage.mongo_db import MongoStorage

# ── Thresholds ─────────────────────────────────────────────────
MIN_TRADE_COUNT_1H   = 50      # Minimum trades in last hour
MIN_VOLUME_USD_1H    = 500     # Minimum estimated volume (USD) in last hour
MIN_BUY_RATIO        = 0.3     # At least 30% buys (not total dump)


class TokenFilter:
    """
    Filters tokens using YOUR OWN data — no BirdEye needed.
    Computes activity metrics from trades & orderbook snapshots.
    """

    def __init__(self):
        self.storage = MongoStorage()

    async def get_active_tokens(self, symbols: list[str], lookback_minutes: int = 60) -> list[str]:
        """
        Return only tokens that pass self-computed activity filters.
        
        Filters:
          - trade_count > MIN_TRADE_COUNT_1H
          - estimated_volume > MIN_VOLUME_USD_1H  
          - buy_ratio > MIN_BUY_RATIO
        """
        await self.storage.connect()
        active = []

        cutoff = datetime.utcnow() - timedelta(minutes=lookback_minutes)

        for symbol in symbols:
            try:
                pipeline = [
                    {"$match": {
                        "symbol": symbol.upper(),
                        "timestamp": {"$gte": cutoff}
                    }},
                    {"$project": {
                        "price":           "$data.price",
                        "quantity":        "$data.quantity",
                        "is_buyer_maker":  "$data.is_buyer_maker"
                    }},
                    {"$group": {
                        "_id":         None,
                        "trade_count": {"$sum": 1},
                        "total_vol":   {"$sum": {"$multiply": ["$price", "$quantity"]}},
                        "buy_count":   {"$sum": {"$cond": [{"$eq": ["$is_buyer_maker", False]}, 1, 0]}}
                    }}
                ]

                result = await self.storage.db["trades"].aggregate(pipeline).to_list(1)

                if not result:
                    cprint(f"[FILTER] {symbol}: No trade data — skipping", "yellow")
                    continue

                r = result[0]
                trade_count = r.get("trade_count", 0)
                volume_usd  = r.get("total_vol", 0)
                buy_ratio   = r.get("buy_count", 0) / trade_count if trade_count else 0

                passes = (
                    trade_count >= MIN_TRADE_COUNT_1H
                    and volume_usd >= MIN_VOLUME_USD_1H
                    and buy_ratio >= MIN_BUY_RATIO
                )

                status = "✅ ACTIVE" if passes else "❌ LOW-ACTIVITY"
                cprint(
                    f"[FILTER] {symbol:<12} | trades={trade_count:>5} | "
                    f"vol=${volume_usd:>10,.0f} | buy={buy_ratio:.0%} | {status}",
                    "green" if passes else "yellow"
                )

                if passes:
                    active.append(symbol)

            except Exception as e:
                cprint(f"[FILTER] Error processing {symbol}: {str(e)}", "red")

        return active


if __name__ == "__main__":
    async def test():
        f = TokenFilter()
        result = await f.get_active_tokens(["BTCUSDT", "ETHUSDT", "SOLUSDT"])
        print(f"\n✅ Active tokens: {result}")
    asyncio.run(test())
