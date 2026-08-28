"""DataGatherer - Collects enriched data from all agents for AI."""
import os, json, time, requests
from datetime import datetime, timedelta, timezone
from pathlib import Path

BIRDEYE_API = "https://public-api.birdeye.so"
SOLANA_RPC = os.getenv("RPC_ENDPOINT", "https://api.mainnet-beta.solana.com")


class DataGatherer:
    def __init__(self):
        self.birdeye_key = os.getenv("BIRDEYE_API_KEY")
        self._cache = {}
        self.data_dir = Path("src/data")
        print("[DATA] DataGatherer initialized (Birdeye: " + ("ON" if self.birdeye_key else "OFF") + ")")

    def gather_all(self, token_address, symbol=""):
        data = {"symbol": symbol, "address": token_address}
        profile = self._get_birdeye_profile(token_address)
        if profile:
            data["birdeye_profile"] = profile
        security = self._get_birdeye_security(token_address)
        if security:
            data["birdeye_security"] = security
        history = self._get_trading_history(symbol)
        if history:
            data["trading_history"] = history
        # Per-token sentiment (from lightweight analyzer)
        token_sent = self._get_token_sentiment(symbol)
        if token_sent:
            data["token_sentiment"] = token_sent
        # Overall market sentiment (from CSV history)
        sentiment = self._get_sentiment_data()
        if sentiment:
            data["market_sentiment"] = sentiment
        portfolio = self._get_portfolio_context()
        if portfolio:
            data["portfolio"] = portfolio
        whale = self._get_whale_data(token_address)
        if whale:
            data["whale_data"] = whale
        return data

    def _get_token_sentiment(self, symbol):
        """Get per-token Twitter sentiment from lightweight analyzer."""
        if not symbol:
            return None
        try:
            from src.lightweight_sentiment import get_lightweight_sentiment
            sent = get_lightweight_sentiment()
            data = sent.get_token_sentiment(symbol)
            if data:
                return {"score": data.get("score", 0), "label": data.get("label", "neutral"),
                        "tweet_count": data.get("tweet_count", 0),
                        "positive_pct": data.get("positive_pct", 0),
                        "negative_pct": data.get("negative_pct", 0)}
        except Exception:
            pass
        return None

    def _get_birdeye_profile(self, addr):
        if not self.birdeye_key:
            return None
        key = "p_" + addr
        if key in self._cache:
            return self._cache[key]
        try:
            r = requests.get(BIRDEYE_API + "/defi/v3/token/profile",
                headers={"X-API-KEY": self.birdeye_key},
                params={"address": addr}, timeout=10)
            if r.status_code == 200:
                d = r.json().get("data", {})
                p = {"name": d.get("name", ""), "description": d.get("description", "")[:200],
                     "has_website": bool(d.get("website")), "has_twitter": bool(d.get("twitter")),
                     "has_telegram": bool(d.get("telegram"))}
                self._cache[key] = p
                return p
        except Exception:
            pass
        return None

    def _get_birdeye_security(self, addr):
        if not self.birdeye_key:
            return None
        key = "s_" + addr
        if key in self._cache:
            return self._cache[key]
        try:
            r = requests.get(BIRDEYE_API + "/defi/v3/token/security",
                headers={"X-API-KEY": self.birdeye_key},
                params={"address": addr}, timeout=10)
            if r.status_code == 200:
                d = r.json().get("data", {})
                s = {"top_10_holder_pct": d.get("top10HolderPercent", 0),
                     "holder_count": d.get("holderCount", 0),
                     "creator_pct": d.get("creatorPercent", 0)}
                self._cache[key] = s
                return s
        except Exception:
            pass
        return None

    def _get_trading_history(self, symbol):
        f = self.data_dir / "sniper" / "positions.jsonl"
        if not f.exists():
            return None
        trades = []
        try:
            for line in open(f):
                t = json.loads(line)
                if t.get("symbol") == symbol:
                    trades.append({"action": t.get("action"), "pnl": t.get("pnl_usd", 0), "status": t.get("status", "")})
        except Exception:
            pass
        return {"count": len(trades), "last": trades[-1]} if trades else None

    def _get_sentiment_data(self):
        f = self.data_dir / "sentiment_history.csv"
        if not f.exists():
            return None
        try:
            import pandas as pd
            df = pd.read_csv(f)
            if df.empty:
                return None
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            cutoff = datetime.now() - timedelta(hours=24)
            recent = df[df["timestamp"] > cutoff]
            if recent.empty:
                return None
            avg = float(recent["sentiment_score"].mean())
            return {"avg_24h": round(avg, 3), "label": "pos" if avg > 0.1 else "neg" if avg < -0.1 else "neutral"}
        except Exception:
            return None

    def _get_portfolio_context(self):
        f = self.data_dir / "paper_trading" / "paper_trades.jsonl"
        if not f.exists():
            return None
        try:
            total = wins = pnl = 0
            for line in open(f):
                t = json.loads(line)
                if t.get("action") == "exit":
                    total += 1
                    p = t.get("pnl_usd", 0)
                    pnl += p
                    if p > 0:
                        wins += 1
            if total > 0:
                return {"trades": total, "win_rate": round(wins/total*100, 1), "pnl": round(pnl, 4)}
        except Exception:
            pass
        return None

    def _get_whale_data(self, addr):
        try:
            r = requests.post(SOLANA_RPC, json={"jsonrpc": "2.0", "id": 1,
                "method": "getTokenLargestAccounts", "params": [addr]}, timeout=10)
            if r.status_code == 200:
                accts = r.json().get("result", {}).get("value", [])
                if accts:
                    return {"holders": len(accts),
                            "risk": "high" if len(accts) < 10 else "medium" if len(accts) < 20 else "low"}
        except Exception:
            pass
        return None
