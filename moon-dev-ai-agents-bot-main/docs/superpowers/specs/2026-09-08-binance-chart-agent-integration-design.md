# Binance Chart Agent Integration Design

## Goal

Make the native Binance footprint chart an extensible trading workspace that can consume footprint indicators, RBI and custom bot outputs, and AWS Bedrock analysis through one shared chart context and overlay contract.

## Architecture

```text
Binance trades/order book
        -> BinanceChartService
        -> Shared Chart Snapshot
        -> /api/binance/footprint
        -> Native footprint renderer + AI sidebar

Shared Chart Snapshot
        -> RBI/custom bot adapters
        -> Bedrock context builder
        -> Structured agent response
        -> Chart overlays + sidebar analysis
```

The existing native footprint data path remains the source of truth for trades, levels, candles, and DOM. Composition moves behind a service boundary so routes do not own indicator or agent orchestration.

## Shared Contract

The shared snapshot supports:

- `symbol`, `timeframe`, `as_of`, and source metadata
- candles and footprint price levels
- delta, imbalance, absorption, volume profile, POC, and value area
- order-book bids, asks, spread, and depth imbalance
- indicator contributions
- chart overlays and agent signals
- risk context and analysis freshness

Overlay contributions support time anchors, price anchors, zones, lower panels, source, confidence, severity, labels, and structured details. Footprint overlays may target a bucket, price level, price range, DOM, or panel.

## Backend

Add a chart service that composes base Binance data with footprint indicators and registered contributions. Keep pure calculations in dedicated modules and keep FastAPI routes thin. Existing SMC bot and RBI contracts are adapted at the service boundary rather than duplicated in the frontend.

Add chart-context endpoints for:

- retrieving the normalized snapshot
- requesting Bedrock analysis for a snapshot
- continuing a chart-scoped AI conversation

AI requests receive a bounded, server-built context bundle. The model returns structured analysis, levels, evidence, risk notes, and overlay contributions. It does not directly execute trades.

## Frontend

Extend the native chart state with indicators, overlays, agent signals, panel data, visibility, and AI analysis state. Split rendering into background, grid, footprint cells, candles, indicators, agent overlays, signals, and crosshair layers. Add a responsive AI sidebar that can collapse below the chart on mobile.

The sidebar shows market bias, confidence, evidence, active agent signals, risk levels, freshness, and chat. Questions are sent with the current chart context identifier and selected symbol/timeframe.

## Safety

Trade ideas are informational until explicitly confirmed. Any future order ticket must pass existing risk guards, position sizing, and execution policy. Bedrock output is treated as an untrusted analysis contribution and never as direct authority to place an order.

## Delivery Sequence

1. Shared contract and serialization helpers.
2. Binance chart composition service and normalized footprint response.
3. RBI/custom bot adapter hooks and overlay registry.
4. Native renderer layers and responsive AI sidebar shell.
5. Bedrock chart-context analysis endpoint and structured sidebar response.
6. Tests for contracts, stale data, agent failures, rendering bounds, and risk boundaries.

## Non-goals

- Replacing the existing Binance collector.
- Allowing autonomous live execution from the chart chat.
- Rewriting the separate SMC chart in the first integration slice.
