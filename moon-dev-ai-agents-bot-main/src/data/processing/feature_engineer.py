"""
🌙 Moon Dev's Feature Engineering Agent
Transforms raw market data into institutional-grade features
Built with love by Moon Dev 🚀
"""

import pandas as pd
import pandas_ta as ta
import asyncio
from datetime import datetime, timedelta
from termcolor import colored, cprint

from src.data.storage.mongo_db import MongoStorage

class FeatureEngineer:
    def __init__(self):
        self.storage = MongoStorage()
        cprint("[FEATURE ENGINE] Moon Dev's Feature Engineering Agent initialized", "white", "on_blue")

    async def generate_features_from_db(self, symbol, lookback_trades=1000):
        """Fetch trades from DB and generate advanced features"""
        try:
            await self.storage.connect()
            
            # 1. Fetch raw trades
            trade_cursor = self.storage.db["trades"].find({"symbol": symbol.upper()}).sort("timestamp", -1).limit(lookback_trades)
            trades = await trade_cursor.to_list(length=lookback_trades)
            
            if not trades:
                return None
                
            # Convert to DataFrame
            df_trades = pd.DataFrame([t["data"] for t in trades])
            df_trades['timestamp'] = pd.to_datetime(df_trades['timestamp'])
            df_trades.set_index('timestamp', inplace=True)
            df_trades.sort_index(inplace=True)
            
            # 2. Resample to 1m OHLCV
            df_1m = df_trades['price'].resample('1min').ohlc()
            df_1m['volume'] = df_trades['quantity'].resample('1min').sum()
            
            # 3. Calculate Technical Indicators
            df_1m = self.calculate_basic_indicators(df_1m)
            
            # 4. Fetch Order Book Depth (Latest snapshot)
            depth_doc = await self.storage.db["orderbook_snapshots"].find_one(
                {"symbol": symbol.upper()},
                sort=[("timestamp", -1)]
            )
            depth_features = {}
            if depth_doc:
                depth_features = self.calculate_liquidity_features(depth_doc["data"])
            
            # 5. Add Cross-Market Correlation
            correlation_features = await self.add_correlation_features()

            # 6. ✨ Self-computed autonomous metrics (replaces BirdEye insights)
            autonomous_metrics = self.compute_autonomous_metrics(df_trades, df_1m)

            # 7. Extract latest values as a feature set
            latest_bar = df_1m.iloc[-1].to_dict()

            full_feature_set = {
                "microstructure": self.calculate_microstructure_features([t["data"] for t in trades[:100]]),
                "indicators":     latest_bar,
                "depth":          depth_features,
                "correlation":    correlation_features,
                "autonomous":     autonomous_metrics,    # ← NEW: zero-API metrics
            }

            # 8. Save to features_dataset
            # Note: save_to_collection already wraps in {symbol, timestamp, data: payload}
            # So we pass full_feature_set directly — NOT pre-wrapped
            await self.storage.save_to_collection("features_dataset", symbol, full_feature_set)
            cprint(f"[FEATURE ENGINE] Persisted advanced features for {symbol}", "white", "on_green")

            return full_feature_set

        except Exception as e:
            cprint(f"[ERROR] generate_features_from_db failed: {str(e)}", "white", "on_red")
            return None

    async def build_historical_dataset(self, symbol, start_time, end_time):
        """Backfill features for a historical range in 1m chunks"""
        cprint(f"[FEATURE ENGINE] Backfilling dataset for {symbol} from {start_time} to {end_time}...", "white", "on_blue")
        
        current_time = start_time
        while current_time < end_time:
            # Generate features for this specific window
            # We fetch trades around this timestamp to simulate the 'real-time' view at that moment
            window_end = current_time + timedelta(minutes=1)
            
            await self.generate_features_from_db(symbol, lookback_trades=1000) # This currently uses 'latest'
            # TODO: Refactor generate_features_from_db to accept a 'ref_time' for true historical accuracy
            
            current_time = window_end
            await asyncio.sleep(0.1) # Prevent blocking
            
        cprint(f"[FEATURE ENGINE] Backfill completed for {symbol}", "white", "on_green")

    def compute_autonomous_metrics(self, df_trades: "pd.DataFrame", df_1m: "pd.DataFrame") -> dict:
        """
        ✨ Self-computed metrics from Binance data — zero BirdEye needed.
        
        Computes:
          - volume_spike:   current 1m volume vs rolling 20-bar average
          - momentum_5m:    price change over last 5 bars
          - buy_pressure:   buy volume / total volume ratio
          - volatility:     std(close prices) over last 20 bars
          - liquidity_score: bid-ask proxy from trade spread
        """
        try:
            metrics = {}

            # ── Volume Spike ──────────────────────────────────────────
            # Current bar volume vs 20-bar rolling mean → >1 means spike
            if "volume" in df_1m.columns and len(df_1m) >= 2:
                rolling_vol = df_1m["volume"].rolling(20, min_periods=1).mean()
                current_vol = df_1m["volume"].iloc[-1]
                avg_vol     = rolling_vol.iloc[-1]
                metrics["volume_spike"] = float(current_vol / avg_vol) if avg_vol > 0 else 1.0
            else:
                metrics["volume_spike"] = 1.0

            # ── 5-Minute Momentum ─────────────────────────────────────
            if "close" in df_1m.columns and len(df_1m) >= 6:
                price_now  = df_1m["close"].iloc[-1]
                price_5m   = df_1m["close"].iloc[-6]
                metrics["momentum_5m"]   = float(price_now - price_5m)
                metrics["momentum_5m_pct"] = float((price_now - price_5m) / price_5m * 100) if price_5m else 0.0
            else:
                metrics["momentum_5m"]     = 0.0
                metrics["momentum_5m_pct"] = 0.0

            # ── Buy Pressure ──────────────────────────────────────────
            # True buy = is_buyer_maker == False (taker is the buyer)
            if "is_buyer_maker" in df_trades.columns and len(df_trades) > 0:
                buy_vol  = df_trades.loc[df_trades["is_buyer_maker"] == False, "quantity"].sum()
                sell_vol = df_trades.loc[df_trades["is_buyer_maker"] == True, "quantity"].sum()
                total    = buy_vol + sell_vol
                metrics["buy_pressure"]  = float(buy_vol / total) if total > 0 else 0.5
                metrics["sell_pressure"] = float(sell_vol / total) if total > 0 else 0.5
            else:
                metrics["buy_pressure"]  = 0.5
                metrics["sell_pressure"] = 0.5

            # ── Volatility (20-bar std of close) ─────────────────────
            if "close" in df_1m.columns and len(df_1m) >= 5:
                metrics["volatility_20"] = float(df_1m["close"].rolling(20, min_periods=5).std().iloc[-1] or 0)
            else:
                metrics["volatility_20"] = 0.0

            # ── Liquidity Proxy (avg trade size USD) ──────────────────
            if "price" in df_trades.columns and "quantity" in df_trades.columns:
                df_trades["trade_usd"] = df_trades["price"] * df_trades["quantity"]
                metrics["avg_trade_usd"]   = float(df_trades["trade_usd"].mean())
                metrics["total_volume_usd"] = float(df_trades["trade_usd"].sum())
            else:
                metrics["avg_trade_usd"]   = 0.0
                metrics["total_volume_usd"] = 0.0

            cprint(
                f"[FEATURE ENGINE] Autonomous metrics: "
                f"spike={metrics['volume_spike']:.2f}x | "
                f"mom={metrics['momentum_5m_pct']:.2f}% | "
                f"buy={metrics['buy_pressure']:.0%}",
                "white", "on_green"
            )
            return metrics

        except Exception as e:
            cprint(f"[ERROR] compute_autonomous_metrics: {str(e)}", "red")
            return {}

    async def add_correlation_features(self):
        """Fetch latest correlation data (Gold, BTC) from DB"""
        try:
            # 1. Gold
            gold_doc = await self.storage.db["market_data"].find_one(
                {"symbol": "GC=F"},
                sort=[("timestamp", -1)]
            )
            # 2. BTC
            btc_doc = await self.storage.db["market_data"].find_one(
                {"symbol": "BTC-USD"},
                sort=[("timestamp", -1)]
            )
            
            def get_price(doc, symbol):
                if not doc or "data" not in doc: return None
                data = doc["data"]
                # Look for 'Close', 'close', or 'Close_SYMBOL'
                for k in data.keys():
                    if k.lower().startswith("close"):
                        return float(data[k])
                return None

            return {
                "gold_price": get_price(gold_doc, "GC=F"),
                "btc_market_price": get_price(btc_doc, "BTC-USD")
            }
        except Exception as e:
            cprint(f"[ERROR] add_correlation_features failed: {str(e)}", "red")
            return {}

    def calculate_basic_indicators(self, df):
        """Calculate standard technical indicators using pandas_ta"""
        try:
            # Momentum
            df['RSI'] = ta.rsi(df['close'], length=14)
            
            # Volatility
            bbands = ta.bbands(df['close'], length=20, std=2)
            if bbands is not None:
                df = pd.concat([df, bbands], axis=1)
            
            # Trend
            df['EMA_20'] = ta.ema(df['close'], length=20)
            df['EMA_50'] = ta.ema(df['close'], length=50)
            
            # MACD
            macd = ta.macd(df['close'])
            if macd is not None:
                df = pd.concat([df, macd], axis=1)
            
            cprint("[FEATURE ENGINE] Successfully engineered basic technical features", "white", "on_green")
            return df
        except Exception as e:
            cprint(f"❌ Error calculating basic indicators: {str(e)}", "white", "on_red")
            return df

    def calculate_microstructure_features(self, trade_data):
        """Calculate features from tick/trade level data"""
        try:
            if not trade_data:
                return {}
                
            df = pd.DataFrame(trade_data)
            if df.empty:
                return {}

            # Calculate metrics
            total_volume = df['quantity'].sum()
            buy_volume = df[df['is_buyer_maker'] == False]['quantity'].sum()
            sell_volume = df[df['is_buyer_maker'] == True]['quantity'].sum()
            
            # Volume Imbalance (positive = buyer pressure, negative = seller pressure)
            imbalance = (buy_volume - sell_volume) / total_volume if total_volume > 0 else 0
            
            # Average trade size
            avg_trade_size = df['quantity'].mean()
            
            # Trade count
            trade_count = len(df)
            
            cprint(f"[FEATURE ENGINE] Engineered microstructure: {imbalance:.2f} imbalance over {trade_count} trades", "white", "on_green")
            
            return {
                "volume_imbalance": imbalance,
                "buy_v_sell_ratio": buy_volume / sell_volume if sell_volume > 0 else float('inf'),
                "avg_trade_size": avg_trade_size,
                "trade_count": trade_count
            }
        except Exception as e:
            cprint(f"❌ Error calculating microstructure features: {str(e)}", "white", "on_red")
            return {}

    def calculate_liquidity_features(self, depth_data):
        """Calculate features from order book depth data"""
        try:
            if not depth_data:
                return {}
            
            # Extract bids and asks from the partial book depth or full snapshot
            bids = depth_data.get('b', []) or depth_data.get('bids', [])
            asks = depth_data.get('a', []) or depth_data.get('asks', [])
            
            if not bids or not asks:
                return {}
                
            # Best bid/ask
            best_bid = float(bids[0][0])
            best_ask = float(asks[0][0])
            
            # Spread
            spread = (best_ask - best_bid) / best_bid if best_bid > 0 else 0
            
            # Simple Liquidity Depth (sum of top 5 levels)
            bid_depth = sum([float(b[1]) for b in bids[:5]])
            ask_depth = sum([float(a[1]) for a in asks[:5]])
            
            # Depth Imbalance
            depth_imbalance = (bid_depth - ask_depth) / (bid_depth + ask_depth) if (bid_depth + ask_depth) > 0 else 0
            
            return {
                "spread_bps": spread * 10000,
                "bid_depth_top5": bid_depth,
                "ask_depth_top5": ask_depth,
                "depth_imbalance": depth_imbalance
            }
        except Exception as e:
            cprint(f"❌ Error calculating liquidity features: {str(e)}", "white", "on_red")
            return {}
