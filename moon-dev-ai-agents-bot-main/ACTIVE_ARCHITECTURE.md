# Active Architecture

## Current production path

This project is being migrated to Solana. The supported runtime path is:

```text
run_deploy.py / start.sh
    -> micro_engine.py
    -> Solana RPC, DexScreener, and Birdeye data
    -> PostgreSQL via src/db_storage.py
    -> JSONL fallback for selected local records
```

PostgreSQL is the source of truth for the active Solana bot. Its operational data includes executed or paper trades, OHLCV candles, wallet events, engine events, scanner results, and strategy records.

## Binance research path

The following stack is retained for historical research and manual experiments only:

```text
Binance WebSocket
    -> src/data/collectors/binance_ws.py
    -> PostgreSQL binance_market_trades / binance_depth_updates
    -> future footprint aggregation and chart delivery
```

Binance is a research-only market-data source and is separate from Solana execution. Raw Binance trades and depth updates must be stored in PostgreSQL. They must not be written to the Solana `trades` table, which contains executed or paper positions.

MongoDB is not required by either the active Solana micro-engine or the PostgreSQL-backed Binance research collector. Older MongoDB collectors and processors remain in the repository only as legacy code until they are formally archived.

## Data ownership

- Solana market and trading data: PostgreSQL and the active `src/` modules.
- Binance research data: PostgreSQL tables `binance_market_trades` and `binance_depth_updates`.
- New visualization and order-flow work must target PostgreSQL-backed data. It must not introduce MongoDB persistence.

## Migration status

The MongoDB implementation and its dependency remain in the repository temporarily so legacy scripts are not broken unexpectedly. Before removing them, verify that no scheduled job, deployment command, or manual workflow still invokes the legacy collectors or processors.

## Security

Do not commit `.env` files or paste credentials into source, logs, documentation, or issue reports. Rotate credentials that have been exposed.
