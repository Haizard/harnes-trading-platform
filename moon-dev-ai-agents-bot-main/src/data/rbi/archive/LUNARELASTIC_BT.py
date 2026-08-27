import pandas as pd
import talib
from backtesting import Backtest, Strategy
from backtesting.lib import resample_apply

# STRATEGY_NAME: LUNAR ELASTIC
# STRATEGY_DETAILS: RSI-based mean reversion (30/70 thresholds) on BTC-USD 15m

class LunarElastic(Strategy):
    rsi_period = 14
    rsi_oversold = 30
    rsi_overbought = 70
    
    def init(self):
        # Use self.I() and talib for indicators as requested 🌙
        self.rsi = self.I(talib.RSI, self.data.close, self.rsi_period)

    def next(self):
        # Logic: Buy when RSI drops below 30 (Oversold)
        # Close when RSI crosses above 70 (Overbought)
        
        # Position sizing calculation
        # We calculate size to use 95% of available equity
        price = self.data.close[-1]
        size = (self.equity * 0.95) / price
        final_size = int(round(size)) # Rule: int(round(size))

        if not self.position:
            if self.rsi[-1] < self.rsi_oversold:
                self.buy(size=final_size)
        
        elif self.rsi[-1] > self.rsi_overbought:
            self.position.close()

# Load and Clean Data
data_path = "c:/Users/Dell/Desktop/moon-dev-ai-agents-bot/src/data/rbi/BTC-USD-15m.csv"
df = pd.read_csv(data_path)

# Rule: Clean data columns 🌙
df.columns = df.columns.str.strip().str.lower()

# Ensure datetime index
df['timestamp'] = pd.to_datetime(df['timestamp'])
df.set_index('timestamp', inplace=True)

# Sort index to ensure chronological order
df.sort_index(inplace=True)

# Initialize Backtest
bt = Backtest(
    df, 
    LunarElastic, 
    cash=10000, 
    commission=.001, # 0.1% commission
    exclusive_orders=True
)

# Run and Print Stats
stats = bt.run()
print("🌙 LUNAR ELASTIC BACKTEST RESULTS 🌙")
print(stats)

# Optional: Plot the results
# bt.plot()