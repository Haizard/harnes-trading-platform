import pandas as pd
import talib
from backtesting import Backtest, Strategy
import numpy as np

# --- DATA LOADING & CLEANING ---
# Moon Dev's Rule: Clean data is happy data! 🌙
data_path = '/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv'

def load_and_clean_data(path):
    print("🌙 Moon Dev is cleaning your data... ✨")
    try:
        df = pd.read_csv(path)
        
        # Clean column names: strip spaces and lowercase
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
        
        # Set datetime index
        df['datetime'] = pd.to_datetime(df['datetime'])
        df.set_index('datetime', inplace=True)
        
        # Drop any rows with NaN values that might break TA-Lib
        df = df.dropna()
        
        print("🚀 Data is ready for the moon!")
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
        # No backtesting.lib.SMA or RSI allowed!
        self.rsi = self.I(talib.RSI, self.data.Close, timeperiod=self.rsi_period)
        
        print("✨ Indicators loaded! Ready to hunt for reversals! ✨")

    def next(self):
        # Ensure we have enough data for crossover logic (at least 2 bars)
        if len(self.data) < 2:
            return

        # Current price and RSI values
        price = self.data.Close[-1]
        
        # ✅ REQUIRED: Manual Crossover Detection
        # Instead of crossover(rsi, 30), we use manual indexing:
        # Check if RSI crossed BELOW the oversold threshold
        rsi_crossed_below_30 = self.rsi[-2] >= self.rsi_oversold and self.rsi[-1] < self.rsi_oversold
        
        # Check if RSI crossed ABOVE the overbought threshold
        rsi_crossed_above_70 = self.rsi[-2] <= self.rsi_overbought and self.rsi[-1] > self.rsi_overbought
        
        # Check if we are already in a position
        if not self.position:
            # LONG ENTRY logic: RSI crosses below 30 (oversold)
            if rsi_crossed_below_30:
                print(f"🔮 SIGNAL: RSI crossed below {self.rsi_oversold} - Entering Long @ {price} 🌙")
                
                # RISK MANAGEMENT: Position Sizing
                risk_amount = self.equity * self.risk_per_trade
                sl_price = price * (1 - self.stop_loss_pct)
                risk_per_share = price - sl_price
                
                if risk_per_share > 0:
                    raw_size = risk_amount / risk_per_share
                    # CRITICAL POSITION SIZING RULE: Must be integer of units
                    final_size = int(round(raw_size))
                    
                    # Execute Buy with Stop Loss
                    self.buy(size=final_size, sl=sl_price)
                    print(f"🚀 Long order placed! Size: {final_size} units | SL: {sl_price:.2f}")

        else:
            # EXIT logic: RSI crosses above 70 (overbought)
            if rsi_crossed_above_70:
                print(f"💰 TARGET: RSI crossed above {self.rsi_overbought} - Closing Position @ {price} ✨")
                self.position.close()

# --- EXECUTION ---
if __name__ == "__main__":
    # Load data
    df = load_and_clean_data(data_path)
    
    if df is not None:
        # Initialize Backtest
        # Starting cash: 1,000,000
        bt = Backtest(df, KineticReversal, cash=1_000_000, commission=.001)
        
        print("\n🌙 Starting Moon Dev Backtest... 🚀")
        
        # Run the backtest
        stats = bt.run()
        
        # Print full stats and strategy parameters
        print("\n" + "="*30)
        print("🌙 FINAL BACKTEST STATS 🌙")
        print("="*30)
        print(stats)
        
        print("\n" + "="*30)
        print("🚀 STRATEGY DETAILS 🚀")
        print("="*30)
        print(stats._strategy)
        
        print("\n✨ Backtest Complete! To the moon! 🚀🌙")
    else:
        print("❌ Data loading failed. Check your path! 🌙")
