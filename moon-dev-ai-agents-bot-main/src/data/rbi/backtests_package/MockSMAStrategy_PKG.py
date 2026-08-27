import pandas as pd
from backtesting import Backtest, Strategy
from backtesting.lib import crossover
import talib
import numpy as np

class MockSMAStrategy(Strategy):
    def init(self):
        close = self.data.Close
        # Ensure we have enough data for indicators
        self.sma20 = self.I(talib.SMA, close, 20)
        self.sma50 = self.I(talib.SMA, close, 50)

    def next(self):
        if crossover(self.sma20, self.sma50):
            self.buy()
        elif crossover(self.sma50, self.sma20):
            self.position.close()

# Data Loading - using the path expected by the agent
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
df.dropna(inplace=True)

# Run backtest
bt = Backtest(df, MockSMAStrategy, cash=10000, commission=.002)
stats = bt.run()
print(stats)
print(stats._strategy)