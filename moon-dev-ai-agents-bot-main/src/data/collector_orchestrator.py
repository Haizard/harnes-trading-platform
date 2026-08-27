"""
🌙 Moon Dev's Data Collector Orchestrator
Manages real-time, macro, and cross-market data collection loops
Built with love by Moon Dev 🚀
"""

import asyncio
import schedule
import time
from termcolor import colored, cprint
from src.data.collectors.binance_ws import BinanceWS
from src.data.collectors.birdeye_collector import BirdEyeCollector
from src.data.collectors.coingecko import CoinGeckoCollector
from src.data.processing.feature_engineer import FeatureEngineer
from src.data.collectors.yahoo_finance import YahooFinanceCollector
from src.data.collectors.alpha_vantage import AlphaVantageCollector
from src.data.collectors.fred import FREDCollector
from src.data.collectors.cftc import CFTCCollector
from src.data.collectors.reddit import RedditCollector
from src.data.utils.token_filter import TokenFilter  # 🔍 Smart pre-screening
from src.config import MONITORED_TOKENS

class CollectorOrchestrator:
    def __init__(self):
        self.binance_ws     = BinanceWS()
        self.birdeye        = BirdEyeCollector(interval=300)
        self.feature_engine = FeatureEngineer()
        self.token_filter   = TokenFilter()          # 🔍 Smart pre-screening
        self.coingecko      = CoinGeckoCollector()
        self.yahoo          = YahooFinanceCollector()
        self.alpha_vantage  = AlphaVantageCollector()
        self.fred           = FREDCollector()
        self.cftc           = CFTCCollector()
        self.reddit         = RedditCollector()
        cprint("[ORCHESTRATOR] Moon Dev's Collector Orchestrator initialized", "white", "on_blue")

    async def run_macro_tasks(self):
        """Run periodic macro and correlation collection"""
        while True:
            cprint("[ORCHESTRATOR] Running scheduled macro and correlation tasks...", "cyan")
            
            # 1. CoinGecko Macro Data (Every 1 hour)
            await self.coingecko.get_market_data(ids="bitcoin,ethereum,solana,cardano,ripple")
            
            # 2. Yahoo Finance Correlation Data (Every 1 hour)
            # Crypto
            await self.yahoo.get_historical_data(ticker="BTC-USD")
            await self.yahoo.get_historical_data(ticker="ETH-USD")
            
            # 3. FRED Macro Data (Every 12 hours)
            await self.fred.get_series_data("FEDFUNDS") # Fed Funds Rate
            await self.fred.get_series_data("CPIAUCSL") # CPI
            await self.fred.get_series_data("GDP")      # GDP
            
            # 4. CFTC COT Data (Every 24 hours - usually weekly report)
            await self.cftc.get_cot_data()
            
            # 5. Reddit Sentiment (Every 1 hour)
            await self.reddit.get_subreddit_posts("CryptoCurrency")
            await self.reddit.get_subreddit_posts("Solana")
            
            # Yahoo Finance (Indices, Forex, Commodities)
            # Indices
            await self.yahoo.get_historical_data(ticker="^GSPC") # S&P 500
            await self.yahoo.get_historical_data(ticker="^IXIC") # Nasdaq
            
            # Forex
            await self.yahoo.get_historical_data(ticker="EURUSD=X")
            await self.yahoo.get_historical_data(ticker="JPY=X")
            
            # Commodities
            await self.yahoo.get_historical_data(ticker="GC=F")  # Gold
            
            # 6. Feature Engineering on pre-screened tokens (Every 1 hour)
            # Use Binance symbol format for feature engine
            binance_symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
            for sym in binance_symbols:
                await self.feature_engine.generate_features_from_db(sym)

            cprint("[SUCCESS] Macro tasks and feature engineering completed. Sleeping for 1 hour...", "green")
            await asyncio.sleep(3600)

    async def start(self):
        """Start both the real-time and periodic collection loops"""
        cprint("[ORCHESTRATOR] Starting Moon Dev's Unified Data Pipeline...", "white", "on_blue")
        
        # Run everything together
        try:
            await asyncio.gather(
                self.binance_ws.start(),
                self._run_birdeye_filtered_loop(),   # 🔍 Filtered BirdEye
                self.run_macro_tasks()
            )
        except Exception as e:
            cprint(f"[ERROR] Orchestrator Error: {str(e)}", "white", "on_red")

    async def _run_birdeye_filtered_loop(self):
        """BirdEye collection loop that only queries pre-screened, active tokens."""
        cprint("[ORCHESTRATOR] Starting filtered BirdEye collection loop...", "white", "on_blue")
        while True:
            try:
                # 1. Pre-screen: only query tokens active in the last 60 min
                active_tokens = await self.token_filter.get_active_tokens(
                    MONITORED_TOKENS, lookback_minutes=60
                )

                if not active_tokens:
                    cprint("[BIRDEYE] No tokens passed filter — using cache fallback.", "yellow")
                    active_tokens = MONITORED_TOKENS  # Fallback: run on all

                # 2. Collect data from screened tokens (rate-limited + cached)
                tasks = [self.birdeye.collect_token_data(addr) for addr in active_tokens]
                await asyncio.gather(*tasks)

                cprint(
                    f"[BIRDEYE] Cycle done. Filtered {len(active_tokens)}/{len(MONITORED_TOKENS)} tokens. "
                    f"Sleeping {self.birdeye.interval}s...",
                    "cyan"
                )
            except Exception as e:
                cprint(f"[BIRDEYE] Filtered loop error: {str(e)}. Continuing...", "yellow")

            await asyncio.sleep(self.birdeye.interval)

if __name__ == "__main__":
    orchestrator = CollectorOrchestrator()
    try:
        asyncio.run(orchestrator.start())
    except KeyboardInterrupt:
        cprint("[CLOSED] Moon Dev's Pipeline shutting down...", "white", "on_blue")
