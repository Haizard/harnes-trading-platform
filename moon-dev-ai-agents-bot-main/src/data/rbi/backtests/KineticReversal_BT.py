import pandas as pd
import talib
from backtesting import Backtest, Strategy
import numpy as np

# --- DATA LOADING & CLEANING ---
# Moon Dev's Rule: Clean data is happy data! 🌙
data_path = '/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv'

def load_and_clean_data(path):
    print("🌙 Moon Dev is cleaning your data... ✨")
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

class KineticReversal(Strategy):
    # Strategy Parameters
    rsi_period = 14
    rsi_oversold = 30
    rsi_overbought = 70
    stop_loss_pct = 0.015  # 1.5% stop loss
    risk_per_trade = 0.02  # Risk 2% of equity per trade

    def init(self):
        print("🌙 Initializing KineticReversal Strategy... 🚀")
        # ALWAYS use self.I() for indicators with TA-Lib
        self.rsi = self.I(talib.RSI, self.data.Close, timeperiod=self.rsi_period)
        
        # Optional: Track the swing low for SL if we wanted, 
        # but the prompt asks for a hard 1.5-2% stop.
        print("✨ Indicators loaded! Ready to hunt for reversals! ✨")

    def next(self):
        # Current price and RSI values
        price = self.data.Close[-1]
        rsi_val = self.rsi[-1]
        
        # Check if we are already in a position
        if not self.position:
            # LONG ENTRY logic: RSI crosses below 30 (oversold)
            if rsi_val < self.rsi_oversold:
                print(f"🔮 SIGNAL: RSI is {rsi_val:.2f} (Oversold!) - Entering Long @ {price} 🌙")
                
                # RISK MANAGEMENT: Position Sizing
                # Calculate size based on 2% risk and 1.5% stop loss distance
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
            if rsi_val > self.rsi_overbought:
                print(f"💰 TARGET: RSI is {rsi_val:.2f} (Overbought!) - Closing Position @ {price} ✨")
                self.position.close()

# --- EXECUTION ---
if __name__ == "__main__":
    # Load data
    df = load_and_clean_data(data_path)
    
    # Initialize Backtest
    # Starting cash: 1,000,000 as requested
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