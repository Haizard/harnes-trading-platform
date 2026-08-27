import asyncio
import os
import sys
from datetime import datetime, timedelta
from termcolor import cprint

# Add root to path
sys.path.append(os.getcwd())

from src.data.processing.feature_engineer import FeatureEngineer
from src.models.prediction_engine import PredictionEngine
from src.data.storage.mongo_db import MongoStorage

async def validate_pipeline():
    cprint("🚀 Starting Moon Dev's Feature Pipeline Validation", "white", "on_blue")
    
    storage = MongoStorage()
    engineer = FeatureEngineer()
    predictor = PredictionEngine()
    
    symbol = "BTCUSDT"
    
    try:
        # 1. Check if we have enough trades for testing
        await storage.connect()
        trade_count = await storage.db["trades"].count_documents({"symbol": symbol})
        cprint(f"[TEST] Found {trade_count} trades for {symbol}", "cyan")
        
        if trade_count < 100:
            cprint("⚠️ Not enough trades to generate meaningful features. Run the collector for a few minutes first.", "yellow")
            # return
            
        # 2. Generate Features
        cprint(f"[TEST] Generating features for {symbol}...", "cyan")
        features = await engineer.generate_features_from_db(symbol, lookback_trades=1000)
        
        if features:
            cprint(f"[SUCCESS] Generated features: {list(features.keys())}", "green")
            cprint(f"[TEST] Sample Indicators: {list(features['indicators'].keys())[:5]}...", "white")
            cprint(f"[TEST] depth imbalance: {features['depth'].get('book_imbalance', 'N/A')}", "white")
        else:
            cprint("❌ Failed to generate features", "red")
            
        # 3. Test Prediction
        cprint(f"[TEST] Fetching prediction for {symbol}...", "cyan")
        prediction = await predictor.get_prediction(symbol)
        
        if prediction:
            cprint(f"[SUCCESS] Prediction: {prediction['signal']} ({prediction['reason']})", "green")
        else:
            cprint("❌ Failed to get prediction", "red")
            
        # 4. Verify MongoDB persistence
        feature_check = await storage.db["features_dataset"].find_one({"symbol": symbol}, sort=[("timestamp", -1)])
        if feature_check:
            cprint(f"[SUCCESS] Verified document in MongoDB with timestamp {feature_check['timestamp']}", "green")
        else:
            cprint("❌ Document not found in MongoDB", "red")
            
    except Exception as e:
        cprint(f"❌ Validation failed: {str(e)}", "red")
    finally:
        await storage.close()

if __name__ == "__main__":
    asyncio.run(validate_pipeline())
