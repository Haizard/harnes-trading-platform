"""
🌙 Moon Dev's Strategy Memory System
Logs agent decisions and trade outcomes to MongoDB for learning
Built with love by Moon Dev 🚀
"""

import asyncio
from datetime import datetime
from termcolor import colored, cprint
from src.data.storage.mongo_db import MongoStorage

class StrategyMemory:
    def __init__(self):
        self.storage = MongoStorage()
        cprint("[MEMORY] Strategy Memory System initialized", "white", "on_blue")

    async def log_trade(self, symbol, side, price, quantity, reason, status="ENTRY", pnl=None):
        """Log a trade event to the trade_history collection"""
        try:
            payload = {
                "symbol": symbol.upper(),
                "side": side.upper(),
                "price": float(price),
                "quantity": float(quantity),
                "reason": str(reason),
                "status": status.upper(),
                "pnl": float(pnl) if pnl is not None else 0,
                "timestamp": datetime.utcnow()
            }
            
            await self.storage.save_to_collection("trade_history", symbol, payload)
            
            color = "green" if status == "ENTRY" else ("cyan" if pnl and pnl > 0 else "red")
            cprint(f"[MEMORY] Logged {status} for {symbol}: {side} @ {price} (PnL: {pnl})", "white", f"on_{color}")
            
        except Exception as e:
            cprint(f"[ERROR] StrategyMemory logging failed: {str(e)}", "white", "on_red")

if __name__ == "__main__":
    # Example usage
    memory = StrategyMemory()
    asyncio.run(memory.log_trade("BTCUSDT", "BUY", 95000, 0.01, "SMA Cross detected", "ENTRY"))
