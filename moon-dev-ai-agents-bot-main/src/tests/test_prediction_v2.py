"""Quick live test: generate fresh features then run multi-factor prediction."""
import asyncio
import os
import sys

sys.path.insert(0, os.getcwd())

async def main():
    from src.data.processing.feature_engineer import FeatureEngineer
    from src.models.prediction_engine import PredictionEngine

    # 1. Generate fresh features from live Binance data
    fe = FeatureEngineer()
    features = await fe.generate_features_from_db("BTCUSDT", lookback_trades=2000)

    if features:
        auto = features.get("autonomous", {})
        print("\n--- Autonomous Metrics (from Binance) ---")
        for k, v in auto.items():
            print(f"  {k}: {v}")

    # 2. Run v2 prediction
    pe = PredictionEngine()
    result = await pe.get_prediction("BTCUSDT")
    
    print("\n--- Prediction Result ---")
    print(f"  Signal:     {result['signal']}")
    print(f"  Score:      {result['score']:+d}")
    print(f"  Confidence: {result['confidence']:.0%}")
    print(f"  Reasons:    {result.get('reasons', [])}")
    print(f"  Factors:    {result.get('factors', {})}")

if __name__ == "__main__":
    asyncio.run(main())
