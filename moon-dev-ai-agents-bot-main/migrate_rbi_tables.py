"""
One-off migration: create the RBI pipeline tables in PostgreSQL.

Creates (idempotent):
  rbi_runs           - web-triggered RBI pipeline runs
  rbi_strategies     - strategy lifecycle records (idea -> decision -> deploy)
  rbi_session_events - per-phase pipeline audit events
  trades.strategy_name column - live PnL attribution back to RBI strategies

Usage:
  python migrate_rbi_tables.py
"""

import os
import sys
from pathlib import Path

# Load .env manually (no python-dotenv dependency needed)
ROOT = Path(__file__).parent
env_file = ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

# Map the .env connection string to the var db_storage expects
if not os.environ.get("LUCERIS_DATABASE_URL"):
    for candidate in ("postgress_db_connection_string", "DATABASE_URL"):
        if os.environ.get(candidate):
            os.environ["LUCERIS_DATABASE_URL"] = os.environ[candidate]
            print(f"[MIGRATE] Mapped {candidate} -> LUCERIS_DATABASE_URL")
            break

sys.path.insert(0, str(ROOT))

from src import db_storage  # noqa: E402


def main():
    pool = db_storage.get_pool()
    if not pool:
        print("[MIGRATE] FAILED: no DB connection — check LUCERIS_DATABASE_URL")
        return 1

    print("[MIGRATE] Running table creation (idempotent)...")
    db_storage._init_tables()

    # Verify
    expected_tables = ["rbi_runs", "rbi_strategies", "rbi_session_events"]
    with pool.connection() as conn:
        rows = conn.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public'
        """).fetchall()
        existing = {r["table_name"] for r in rows}

        col = conn.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'trades' AND column_name = 'strategy_name'
        """).fetchone()

        for t in expected_tables:
            status = "OK" if t in existing else "MISSING!"
            print(f"[MIGRATE] table {t}: {status}")
        print(f"[MIGRATE] trades.strategy_name column: "
              f"{'OK' if col else 'MISSING!'}")

        # Row counts
        for t in expected_tables:
            if t in existing:
                n = conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
                print(f"[MIGRATE]   {t}: {n} rows")

    ok = all(t in existing for t in expected_tables) and bool(col)
    print(f"[MIGRATE] {'SUCCESS' if ok else 'INCOMPLETE'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
