"""
🌙 Moon Dev's Research Agent
Performs deep analysis on high-resolution market data from MongoDB
Built with love by Moon Dev 🚀
"""

import asyncio
from termcolor import cprint
from datetime import datetime
import pandas as pd
from src.data.storage.mongo_db import MongoStorage
from src.data.processing.feature_engineer import FeatureEngineer

class ResearchAgent:
    def __init__(self):
        self.storage = MongoStorage()
        self.engineer = FeatureEngineer()
        cprint("[RESEARCH] Moon Dev's Research Agent initialized", "white", "on_blue")

    async def get_market_health(self, symbol="BTCUSDT", window_minutes=5):
        """Perform deep research on a token's recent activity"""
        cprint(f"[RESEARCH] Analyzing {symbol} for the last {window_minutes} minutes...", "white", "on_blue")
        
        try:
            await self.storage.connect()
            
            # 1. Fetch recent trades
            collection = self.storage.db["market_data"]
            
            # Get latest 1000 trades for the symbol
            trades_cursor = collection.find({
                "symbol": symbol.upper(),
                "type": "trade"
            }).sort("timestamp", -1).limit(1000)
            
            trades = await trades_cursor.to_list(length=1000)
            
            if not trades:
                cprint(f"[WARN] No trades found for {symbol} in MongoDB", "white", "on_yellow")
                return None

            # Extract trade data for feature engineer
            trade_list = [t['data'] for t in trades]
            
            # 2. Fetch latest depth
            depth_doc = await collection.find_one({
                "symbol": symbol.upper(),
                "type": "depth"
            }, sort=[("timestamp", -1)])
            
            # 3. Calculate features
            micro_stats = self.engineer.calculate_microstructure_features(trade_list)
            liq_stats = self.engineer.calculate_liquidity_features(depth_doc)
            
            # 4. Synthesize report
            report = {
                "symbol": symbol,
                "timestamp": datetime.now().isoformat(),
                "micro_stats": micro_stats,
                "liquidity_stats": liq_stats,
                "summary": self._generate_summary(micro_stats, liq_stats)
            }
            
            cprint(f"[SUCCESS] Research complete for {symbol}", "white", "on_green")
            return report
            
        except Exception as e:
            cprint(f"[ERROR] Research failed: {str(e)}", "white", "on_red")
            return None
        finally:
            await self.storage.close()

    def _generate_summary(self, micro, liq):
        """Generate a human-readable summary of the research"""
        if not micro or not liq:
            return "Insufficient data for summary"
            
        imbalance = micro.get('volume_imbalance', 0)
        depth_imb = liq.get('depth_imbalance', 0)
        
        pressure = "BULLISH" if imbalance > 0.1 else "BEARISH" if imbalance < -0.1 else "NEUTRAL"
        liq_state = "LIQUID" if liq.get('spread_bps', 10) < 5 else "TIGHT"
        
        return f"Market is {pressure} (Vol Imb: {imbalance:.2f}). Liquidity is {liq_state} (Depth Imb: {depth_imb:.2f})."

if __name__ == "__main__":
    agent = ResearchAgent()
    try:
        report = asyncio.run(agent.get_market_health())
        if report:
            import json
            print(json.dumps(report, indent=2))
    except KeyboardInterrupt:
        pass
