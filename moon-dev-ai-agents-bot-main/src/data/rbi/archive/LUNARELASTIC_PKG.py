import pandas as pd
import talib
from backtesting import Backtest, Strategy

# STRATEGY_NAME: LUNAR ELASTIC
# STRATEGY_DETAILS: RSI-based mean reversion (30/70 thresholds) on BTC-USD 15m

class LunarElastic(Strategy):
    rsi_period = 14
    rsi_oversold = 30
    rsi_overbought = 70
    
    def init(self):
        # Use self.I() and talib for indicators 🌙
        self.rsi = self.I(talib.RSI, self.data.close, self.rsi_period)

    def next(self):
        # Manual crossover/threshold logic 🌙
        # We check the last two values to determine logic manually
        if len(self.rsi) < 2:
            return

        # Current and previous RSI values
        curr_rsi = self.rsi[-1]
        prev_rsi = self.rsi[-2]
        
        # Position sizing calculation
        # We calculate size to use 95% of available equity
        price = self.data.close[-1]
        size = (self.equity * 0.95) / price
        final_size = int(round(size)) # Rule: int(round(size))

        if not self.position:
            # Logic: Buy when RSI drops below 30 (Oversold)
            # Manual crossover check: previous was >= 30 and current is < 30
            if prev_rsi >= self.rsi_oversold and curr_rsi < self.rsi_oversold:
                self.buy(size=final_size)
        
        else:
            # Logic: Close when RSI crosses above 70 (Overbought)
            # Manual crossover check: previous was <= 70 and current is > 70
            if prev_rsi <= self.rsi_overbought and curr_rsi > self.rsi_overbought:
                self.position.close()

# Load and Clean Data
data_path = "c:/Users/Dell/Desktop/moon-dev-ai-agents-bot/src/data/rbi/BTC-USD-15m.csv"
df = pd.read_csv(data_path)

# Rule: Clean data columns 🌙
df.columns = df.columns.str.strip().str.lower()

# Ensure datetime index
if 'timestamp' in df.columns:
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df.set_index('timestamp', inplace=True)
elif 'date' in df.columns:
    df['date'] = pd.to_datetime(df['date'])
    df.set_index('date', inplace=True)

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