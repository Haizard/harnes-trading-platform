import pandas as pd
import talib
from backtesting import Backtest, Strategy
import numpy as np
import os

# --- DATA LOADING & CLEANING ---
# Moon Dev's Rule: Clean data is happy data! 🌙
data_path = 'c:/Users/Dell/Desktop/moon-dev-ai-agents-bot/src/data/rbi/BTC-USD-15m.csv'

def load_and_clean_data(path):
    print("🌙 Moon Dev is cleaning your data... ✨")
    try:
        # Check if file exists first to avoid messy errors
        if not os.path.exists(path):
            print(f"❌ File not found at: {path}")
            return None

        df = pd.read_csv(path)
        
        # Clean column names: strip spaces and lowercase for easier mapping
        df.columns = df.columns.str.strip().str.lower()
        
        # Drop unnamed columns
        df = df.drop(columns=[col for col in df.columns if 'unnamed' in col.lower()])
        
        # Ensure proper column mapping for backtesting.py
        # Required: Open, High, Low, Close, Volume (Capitalized)
        mapping = {
            'open': 'Open',
            'high': 'High',
            'low': 'Low',
            'close': 'Close',
            'volume': 'Volume'
        }
        df = df.rename(columns=mapping)
        
        # Set datetime index - CRITICAL for backtesting.py
        if 'datetime' in df.columns:
            df['datetime'] = pd.to_datetime(df['datetime'])
            df.set_index('datetime', inplace=True)
        elif 'timestamp' in df.columns:
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df.set_index('timestamp', inplace=True)
        
        # Ensure all required columns are present and numeric
        required_cols = ['Open', 'High', 'Low', 'Close']
        for col in required_cols:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # Drop any rows with NaN values that might break TA-Lib
        df = df.dropna()
        
        # Sort index to ensure chronological order
        df = df.sort_index()
        
        print(f"🚀 Data is ready for the moon! Loaded {len(df)} rows. ✨")
        return df
    except Exception as e:
        print(f"❌ Error loading data: {e}")
        return None

class KineticReversal(Strategy):
    # Strategy Parameters
    rsi_period = 14
    rsi_oversold = 30
    rsi_overbought = 70
    stop_loss_pct = 0.015  # 1.5% stop loss
    risk_per_trade = 0.02  # Risk 2% of equity per trade

    def init(self):
        print("🌙 Initializing KineticReversal Strategy... 🚀")
        
        # ✅ REQUIRED: Use talib and wrap in self.I()
        # Converting to numpy array to ensure TA-Lib compatibility
        self.rsi = self.I(talib.RSI, self.data.Close, timeperiod=self.rsi_period)
        
        print("✨ Indicators loaded! Ready to hunt for reversals! ✨")

    def next(self):
        # Ensure we have enough data for crossover logic (at least 2 bars)
        if len(self.data) < 2:
            return

        # Current price and RSI values
        price = self.data.Close[-1]
        
        # ✅ REQUIRED: Manual Crossover Detection
        # Check if RSI crossed BELOW the oversold threshold
        rsi_crossed_below_30 = self.rsi[-2] >= self.rsi_oversold and self.rsi[-1] < self.rsi_oversold
        
        # Check if RSI crossed ABOVE the overbought threshold
        rsi_crossed_above_70 = self.rsi[-2] <= self.rsi_overbought and self.rsi[-1] > self.rsi_overbought
        
        # Check if we are already in a position
        if not self.position:
            # LONG ENTRY logic: RSI crosses below 30 (oversold)
            if rsi_crossed_below_30:
                print(f"🔮 SIGNAL: RSI ({self.rsi[-1]:.2f}) crossed below {self.rsi_oversold} - Entering Long @ {price} 🌙")
                
                # RISK MANAGEMENT: Position Sizing
                risk_amount = self.equity * self.risk_per_trade
                sl_price = price * (1 - self.stop_loss_pct)
                risk_per_share = price - sl_price
                
                if risk_per_share > 0:
                    raw_size = risk_amount / risk_per_share
                    
                    # CRITICAL POSITION SIZING RULE: Must be integer of units for whole numbers
                    # OR a fraction < 1.0 for % of equity. We are using unit-based here.
                    final_size = int(round(raw_size))
                    
                    if final_size > 0:
                        # Execute Buy with Stop Loss (sl must be a price level)
                        self.buy(size=final_size, sl=sl_price)
                        print(f"🚀 Long order placed! Size: {final_size} units | SL: {sl_price:.2f} ✨")
                    else:
                        print("⚠️ Calculated size was 0. Skipping trade. 🌙")

        else:
            # EXIT logic: RSI crosses above 70 (overbought)
            if rsi_crossed_above_70:
                print(f"💰 TARGET: RSI ({self.rsi[-1]:.2f}) crossed above {self.rsi_overbought} - Closing Position @ {price} ✨")
                self.position.close()

# --- EXECUTION ---
if __name__ == "__main__":
    # Load data
    df = load_and_clean_data(data_path)
    
    if df is not None and not df.empty:
        # Initialize Backtest
        # Starting cash: 1,000,000
        bt = Backtest(df, KineticReversal, cash=1_000_000, commission=.001)
        
        print("\n🌙 Starting Moon Dev Backtest... 🚀")
        
        # Run the backtest
        stats = bt.run()
        
        # Print full stats and strategy parameters
        print("\n" + "="*40)
        print("🌙 FINAL MOON DEV BACKTEST STATS 🌙")
        print("="*40)
        print(stats)
        
        print("\n" + "="*40)
        print("🚀 STRATEGY DETAILS 🚀")
        print("="*40)
        print(stats._strategy)
        
        # Optional: Plot the results (uncomment if you want to see the chart)
        # bt.plot()
        
        print("\n✨ Backtest Complete! To the moon! 🚀🌙")
    else:
        print("❌ Data loading failed or dataframe is empty. Check your path and data format! 🌙")