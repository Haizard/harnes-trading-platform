import pandas as pd
import talib
from backtesting import Backtest, Strategy
from backtesting.lib import crossover

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
        # Position size calculation: int(round(size))
        # Allocating 95% of equity to the trade
        price = self.data.Close[-1]
        size = (self.equity * 0.95) / price
        final_size = int(round(size))

        # Entry Logic: Buy (Long) when SMA 20 crosses above SMA 50
        if crossover(self.fast_sma, self.base_sma):
            self.buy(size=final_size)

        # Exit Logic: Sell (Close/Short) when SMA 20 crosses below SMA 50
        elif crossover(self.base_sma, self.fast_sma):
            self.sell(size=final_size)

# Backtest execution
bt = Backtest(df, CobaltOrbit, cash=100000, commission=.001)
stats = bt.run()

print(stats)
print(stats._strategy)