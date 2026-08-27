import pandas as pd
import talib
from backtesting import Backtest, Strategy
from backtesting.lib import crossover

class LunarVelocity(Strategy):
    n1 = 20
    n2 = 50

    def init(self):
        # Indicators using self.I() and talib
        self.sma_fast = self.I(talib.SMA, self.data.Close, self.n1)
        self.sma_slow = self.I(talib.SMA, self.data.Close, self.n2)

    def next(self):
        # Calculate size for the position
        size = (self.equity / self.data.Close[-1]) * 0.95
        position_size = int(round(size))

        # Long Entry: SMA(20) crosses above SMA(50)
        if crossover(self.sma_fast, self.sma_slow):
            if self.position.is_short:
                self.position.close()
            if position_size > 0:
                self.buy(size=position_size)

        # Exit/Short Entry: SMA(20) crosses below SMA(50)
        elif crossover(self.sma_slow, self.sma_fast):
            if self.position.is_long:
                self.position.close()
            if position_size > 0:
                self.sell(size=position_size)

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

# Run Backtest
bt = Backtest(df, LunarVelocity, cash=100000, commission=.002, trade_on_close=True)
stats = bt.run()

# Print results
print(stats)
print(stats._strategy)