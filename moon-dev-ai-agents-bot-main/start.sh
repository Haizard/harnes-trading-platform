#!/bin/bash
echo "============================================"
echo "Moon Dev Trading Platform"
echo "Starting in ${MODE:-paper} mode..."
echo "============================================"

# Set default mode if not specified
MODE=${MODE:-paper}
CAPITAL=${CAPITAL:-25.0}

if [ "$MODE" = "live" ]; then
    echo "⚠️  LIVE MODE - Real money at risk!"
    python run_micro.py $CAPITAL --live
else
    echo "📝 PAPER MODE - No real money at risk"
    python run_micro.py $CAPITAL
fi
