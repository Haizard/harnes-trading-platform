import pandas as pd
import talib
from backtesting import Backtest, Strategy

# Data Loading
data_path = 'c:/Users/Dell/Desktop/moon-dev-ai-agents-bot/src/data/rbi/BTC-USD-15m.csv'
df = pd.read_csv(data_path)
df.columns = df.columns.str.strip().str.lower()
mapping = {'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'}
df = df.rename(columns=mapping)

if 'datetime' in df.columns:
    df['datetime'] = pd.to_datetime(df['datetime'])
    df.set_index('datetime', inplace=True)
elif 'timestamp' in df.columns:
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df.set_index('timestamp', inplace=True)

df = df.dropna(subset=['Open', 'High', 'Low', 'Close'])

class CobaltOrbit(Strategy):
    fast_period = 20
    base_period = 50

    def init(self):
        # Indicators using self.I() and talib
        self.fast_sma = self.I(talib.SMA, self.data.Close, self.fast_period)
        self.base_sma = self.I(talib.SMA, self.data.Close, self.base_period)

    def next(self):
        # Ensure we have at least two points to check for a crossover
        if len(self.fast_sma) < 2 or len(self.base_sma) < 2:
            return

        # Position size calculation: int(round(size))
        # Allocating 95% of equity to the trade
        price = self.data.Close[-1]
        size = (self.equity * 0.95) / price
        final_size = int(round(size))

        # Manual Crossover Logic
        # fast_sma crosses above base_sma
        cross_up = self.fast_sma[-2] < self.base_sma[-2] and self.fast_sma[-1] > self.base_sma[-1]
        # fast_sma crosses below base_sma
        cross_down = self.fast_sma[-2] > self.base_sma[-2] and self.fast_sma[-1] < self.base_sma[-1]

        # Entry Logic: Buy (Long) when SMA 20 crosses above SMA 50
        if cross_up:
            self.buy(size=final_size)

        # Exit Logic: Sell (Close/Short) when SMA 20 crosses below SMA 50
        elif cross_down:
            self.sell(size=final_size)

# Backtest execution
bt = Backtest(df, CobaltOrbit, cash=100000, commission=.001)
stats = bt.run()

print(stats)
print(stats._strategy)