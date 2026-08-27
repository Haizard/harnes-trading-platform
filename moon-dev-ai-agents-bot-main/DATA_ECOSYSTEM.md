# 🚀 Moon Dev AI Trading Bot — System Documentation

## 🔁 RBI Integration (IMPLEMENTED)

This system now includes a fully functional **RBI (Research → Backtest → Implement) pipeline** supported by persistent MongoDB storage.

### 🎯 Current State
*   **High-Quality Historical Data**: All WebSocket and BirdEye events are persisted to specialized collections.
*   **Realistic Backtesting**: The system includes a `BacktestEngine` with slippage and spread modeling using order book snapshots.
*   **Continuous Learning**: Trades and agent decisions are logged to `strategy_memory` for future optimization.

---

## 🏗️ Core Architecture (IMPLEMENTED)

### 1. Data Collection Engine
*   **Binance WS**: Live trades and order book snapshots streamed to `trades` and `orderbook_snapshots`.
*   **BirdEye Collector**: Continuous polling of Solana on-chain metrics stored in `tokens`.
*   **Macro Tasks**: Periodic polling of FRED, Yahoo Finance, and social sentiment.

### 2. Feature Engineering Engine
*   **Persistence**: Features are calculated from MongoDB data and saved back to `features_dataset`.
*   **Metrics**: Includes RSI, EMAs, BBands, Volume Imbalance, and Liquidity Depth.

### 3. Replay & Simulation Engine
*   **Multi-Source Interleaving**: Replays trades and depth updates in chronological order from separate collections.
*   **Tick-Level Fidelity**: Simulates sub-second market movements for realistic agent testing.

### 4. Backtest Engine
*   **Slippage Modeling**: Uses `orderbook_snapshots` to estimate true execution price.
*   **PnL Tracking**: Comprehensive logging of entry/exit and net performance.

### 5. Strategy Memory
*   **Trade History**: Every agent decision and execution result is logged for AI training.
*   **Contextual Storage**: Captures market conditions at the time of entry.

---

## ⚡ How to Run

1.  **Ensure MongoDB is running** (default: `mongodb://localhost:27017`).
2.  **Set environment variables** in `.env` (MONGO_URI, MONGO_DB_NAME, API Keys).
3.  **Start the pipeline**:
    ```bash
    python src/data/collector_orchestrator.py
    ```
    This starts the real-time collector, the BirdEye collector, and the periodic macro/feature tasks.

---

**Built with love by Moon Dev 🚀**
