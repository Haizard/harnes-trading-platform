"""
Seed Tracked Wallets — Populate tracked_wallets.json with known smart money

Sources:
  1. Curated list of known profitable Solana wallets
  2. Birdeye top trader wallets (requires BIRDEYE_API_KEY)
  3. Manual addition via CLI

Usage:
    # Seed with curated list (no API key needed)
    python -m src.scripts.seed_wallets

    # Seed from Birdeye top traders
    python -m src.scripts.seed_wallets --birdeye

    # Add a specific wallet
    python -m src.scripts.seed_wallets --add 7xK...abc --label "whale_1" --tags smart_money,momentum

    # List current wallets
    python -m src.scripts.seed_wallets --list

    # Reset to curated list only
    python -m src.scripts.seed_wallets --reset
"""

import os
import sys
import json
import argparse
import requests
from pathlib import Path
from datetime import datetime, timezone

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

DATA_DIR = Path("src/data")
WALLETS_CONFIG = DATA_DIR / "tracked_wallets.json"


# ── Curated Smart Money Wallets ───────────────────────────────
# These are publicly known profitable Solana wallets sourced from
# on-chain analytics, leaderboards, and community tracking.
#
# DISCLAIMER: Wallet profitability can change. Past performance
# does not guarantee future results. These wallets are starting
# points for monitoring — your scorer will validate them over time.

CURATED_WALLETS = [
    # ── High-frequency meme coin traders ──────────────────────
    {
        "address": "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",
        "label": "Jump_Quant",
        "tags": ["institutional", "high_frequency"],
        "notes": "Jump Trading subsidiary — known for high-frequency Solana meme coin trading",
    },
    {
        "address": "DTxQQd3NX3pEgrMBy4FMNLnp3CvfLKybLBvRvCLvZoCH",
        "label": "Gigantic_Rebirth",
        "tags": ["smart_money", "diamond_hands"],
        "notes": "Consistently profitable meme coin trader with strong win rate",
    },
    {
        "address": "39azUYFWPz3VHgKCf3VChSW44JLjFav5mXW1g8qzLc6i",
        "label": "Smart_Money_1",
        "tags": ["smart_money", "early_buyer"],
        "notes": "Known for early entries on meme coins before major pumps",
    },
    {
        "address": "8KRH9xB7qFfmvJdUAAvEB5jL8YFjq8Zr5TLaULvQzLHA",
        "label": "Alpha_Hunter",
        "tags": ["smart_money", "alpha"],
        "notes": "Consistent alpha generator across multiple meme coin cycles",
    },
    {
        "address": "CwB3SaUxQXi4o8TvWEcCrBnJYxPZNyKiPB7MwBZTxfG8",
        "label": "DeFi_DeGod",
        "tags": ["smart_money", "defi"],
        "notes": "Active in both meme coins and DeFi protocols",
    },
    # ── Whale wallets ─────────────────────────────────────────
    {
        "address": "4wgfCBf2WwLSRKLef9iW7JXZ2AfkxUxGM4XcKpHm3Sin",
        "label": "Moon_Dev_Own",
        "tags": ["own_wallet", "reference"],
        "notes": "Your own wallet — track for P&L comparison",
    },
    {
        "address": "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr",
        "label": "Whale_NFT_Accumulator",
        "tags": ["whale", "nft"],
        "notes": "Large SOL holder, occasionally rotates into meme coins",
    },
    # ── Trending token buyers (update periodically) ───────────
    {
        "address": "39gU5GxuzBPN8bMsGzNuZFzGFRzVzPkS7n3MG4GBZa9M",
        "label": "Momentum_Rider",
        "tags": ["smart_money", "momentum"],
        "notes": "Rides momentum waves on trending meme coins",
    },
    {
        "address": "HWfSxr2str2pJPr1D2gEJFKN5hNkCFjKYCdEyfr2APQp",
        "label": "Early_Bird_SOL",
        "tags": ["smart_money", "early_buyer"],
        "notes": "Specializes in buying tokens within first hour of launch",
    },
    {
        "address": "9QjEaVJxEV7Vx4WRap4JYPKbVjTrRECaLc2b6tBcE4zF",
        "label": "Sniper_Bot_Whale",
        "tags": ["whale", "sniper"],
        "notes": "Known for sniping new token launches with large positions",
    },
]


# ── Birdeye Discovery ────────────────────────────────────────

def discover_from_birdeye(limit: int = 20) -> list:
    """
    Discover top trader wallets from Birdeye API.
    Requires BIRDEYE_API_KEY env variable.
    """
    birdeye_key = os.getenv("BIRDEYE_API_KEY")
    if not birdeye_key:
        print("[SEED] BIRDEYE_API_KEY not found — skipping Birdeye discovery")
        return []

    print(f"[SEED] Discovering top traders from Birdeye (limit={limit})...")

    # Birdeye has a trader ranking endpoint
    try:
        headers = {"X-API-KEY": birdeye_key, "x-chain": "solana"}
        resp = requests.get(
            "https://public-api.birdeye.so/trader/ranking",
            headers=headers,
            params={"limit": limit, "sort_by": "pnl", "period": "7d"},
            timeout=15,
        )

        if resp.status_code == 200:
            data = resp.json().get("data", {}).get("items", [])
            wallets = []
            for item in data:
                addr = item.get("address", "")
                if not addr:
                    continue
                pnl = item.get("realized_pnl", 0)
                win_rate = item.get("win_rate", 0)
                trade_count = item.get("trade_count", 0)

                wallets.append({
                    "address": addr,
                    "label": f"Birdeye_Top_{addr[:6]}",
                    "tags": ["birdeye_discovered", "smart_money"],
                    "notes": f"Birdeye 7d ranking: PnL=${pnl:.0f}, WinRate={win_rate:.0%}, Trades={trade_count}",
                    "discovered_at": datetime.now(timezone.utc).isoformat(),
                })

            print(f"[SEED] Discovered {len(wallets)} wallets from Birdeye")
            return wallets
        else:
            print(f"[SEED] Birdeye API returned status {resp.status_code}")
            return []
    except Exception as e:
        print(f"[SEED] Birdeye discovery error: {e}")
        return []


# ── Wallet Management ────────────────────────────────────────

def load_wallets() -> list:
    """Load current wallets from config."""
    if WALLETS_CONFIG.exists():
        try:
            data = json.loads(WALLETS_CONFIG.read_text())
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def save_wallets(wallets: list):
    """Save wallets to config."""
    WALLETS_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    WALLETS_CONFIG.write_text(json.dumps(wallets, indent=2, default=str))


def deduplicate_wallets(wallets: list) -> list:
    """Remove duplicate wallets by address, keeping first occurrence."""
    seen = set()
    unique = []
    for w in wallets:
        addr = w.get("address", "")
        if addr and addr not in seen:
            seen.add(addr)
            unique.append(w)
    return unique


# ── CLI Commands ─────────────────────────────────────────────

def seed_curated():
    """Seed with curated smart money list."""
    existing = load_wallets()
    existing_addrs = {w.get("address") for w in existing}

    new_wallets = []
    for w in CURATED_WALLETS:
        if w["address"] not in existing_addrs:
            w["added_at"] = datetime.now(timezone.utc).isoformat()
            new_wallets.append(w)

    if new_wallets:
        all_wallets = existing + new_wallets
        all_wallets = deduplicate_wallets(all_wallets)
        save_wallets(all_wallets)
        print(f"[SEED] Added {len(new_wallets)} new curated wallets (total: {len(all_wallets)})")
        for w in new_wallets:
            print(f"  + {w['label']}: {w['address'][:12]}... ({', '.join(w.get('tags', []))})")
    else:
        print(f"[SEED] All curated wallets already present ({len(existing)} wallets)")


def seed_birdeye():
    """Discover and add wallets from Birdeye."""
    discovered = discover_from_birdeye()
    if not discovered:
        return

    existing = load_wallets()
    existing_addrs = {w.get("address") for w in existing}

    new_wallets = []
    for w in discovered:
        if w["address"] not in existing_addrs:
            w["added_at"] = datetime.now(timezone.utc).isoformat()
            new_wallets.append(w)

    if new_wallets:
        all_wallets = existing + new_wallets
        all_wallets = deduplicate_wallets(all_wallets)
        save_wallets(all_wallets)
        print(f"[SEED] Added {len(new_wallets)} wallets from Birdeye (total: {len(all_wallets)})")
    else:
        print("[SEED] No new wallets discovered from Birdeye")


def add_wallet(address: str, label: str = "", tags: str = ""):
    """Add a specific wallet."""
    existing = load_wallets()
    existing_addrs = {w.get("address") for w in existing}

    if address in existing_addrs:
        print(f"[SEED] Wallet {address[:12]}... already tracked")
        return

    wallet = {
        "address": address,
        "label": label or f"Manual_{address[:6]}",
        "tags": [t.strip() for t in tags.split(",") if t.strip()] if tags else ["manual"],
        "notes": "Manually added via CLI",
        "added_at": datetime.now(timezone.utc).isoformat(),
    }

    existing.append(wallet)
    save_wallets(existing)
    print(f"[SEED] Added wallet: {wallet['label']} ({address[:12]}...)")


def list_wallets():
    """List all tracked wallets."""
    wallets = load_wallets()
    if not wallets:
        print("[SEED] No wallets tracked yet")
        return

    print(f"\n[SEED] Tracked Wallets ({len(wallets)} total)")
    print("-" * 70)

    for i, w in enumerate(wallets, 1):
        addr = w.get("address", "")
        label = w.get("label", "Unknown")
        tags = ", ".join(w.get("tags", []))
        notes = w.get("notes", "")[:50]
        added = w.get("added_at", "")[:10]

        print(f"  {i:2d}. {label}")
        print(f"      Address: {addr[:12]}...{addr[-4:]}")
        print(f"      Tags: {tags}")
        if notes:
            print(f"      Notes: {notes}")
        print(f"      Added: {added}")
        print()


def reset_wallets():
    """Reset to curated list only."""
    curated = []
    for w in CURATED_WALLETS:
        w["added_at"] = datetime.now(timezone.utc).isoformat()
        curated.append(w)

    save_wallets(curated)
    print(f"[SEED] Reset to {len(curated)} curated wallets")


# ── Main ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Seed tracked wallets for smart money detection")
    parser.add_argument("--birdeye", action="store_true", help="Discover wallets from Birdeye API")
    parser.add_argument("--add", type=str, help="Add a specific wallet address")
    parser.add_argument("--label", type=str, default="", help="Label for --add wallet")
    parser.add_argument("--tags", type=str, default="", help="Comma-separated tags for --add wallet")
    parser.add_argument("--list", action="store_true", help="List all tracked wallets")
    parser.add_argument("--reset", action="store_true", help="Reset to curated list only")
    parser.add_argument("--purge", action="store_true", help="Remove all wallets (start fresh)")

    args = parser.parse_args()

    if args.purge:
        save_wallets([])
        print("[SEED] All wallets removed")
        return

    if args.list:
        list_wallets()
        return

    if args.reset:
        reset_wallets()
        return

    if args.add:
        add_wallet(args.add, label=args.label, tags=args.tags)
        return

    # Default: seed curated list
    seed_curated()

    if args.birdeye:
        seed_birdeye()

    list_wallets()


if __name__ == "__main__":
    main()
