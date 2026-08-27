import pandas as pd
import talib
from backtesting import Backtest, Strategy

# 🌙 STRATEGY_NAME: LUNAR SNAPBACK
# 🌙 STRATEGY_DETAILS: RSI-based mean reversion for BTC-USD 15m

class LunarSnapback(Strategy):
    rsi_period = 14
    oversold = 30
    overbought = 70

    def init(self):
        # Use self.I() and talib for indicators as requested
        self.rsi = self.I(talib.RSI, self.data.Close, self.rsi_period)

    def next(self):
        # Calculate position size: int(round(size))
        # Using 95% of equity for position sizing
        equity = self.equity
        price = self.data.Close[-1]
        size = (equity * 0.95) / price
        pos_size = int(round(size))

        # Buy logic: RSI dips below 30
        if self.rsi < self.oversold:
            if not self.position.is_long:
                # Close any short positions first
                if self.position.is_short:
                    self.position.close()
                self.buy(size=pos_size)

        # Sell logic: RSI crosses above 70
        elif self.rsi > self.overbought:
            if not self.position.is_short:
                # Close any long positions first (Capturing profits)
                if self.position.is_long:
                    self.position.close()
                self.sell(size=pos_size)

# 🌙 Data Loading
data_path = 'c:/Users/Dell/Desktop/moon-dev-ai-agents-bot/src/data/rbi/BTC-USD-15m.csv'

try:
    df = pd.read_csv(data_path)
    df.columns = df.columns.str.strip().str.lower()

    # Map columns to Backtesting.py format (Capitalized)
    mapping = {'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'}
    df = df.rename(columns=mapping)

    # Set index: check for 'datetime' or 'timestamp'
    if 'datetime' in df.columns:
        df['datetime'] = pd.to_datetime(df['datetime'])
        df.set_index('datetime', inplace=True)
    elif 'timestamp' in df.columns:
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df.set_index('timestamp', inplace=True)

    df.dropna(inplace=True)

    # 🌙 Run Backtest
    # Initial cash: 10,000, Commission: 0.1% (standard for many exchanges)
    bt = Backtest(df, LunarSnapback, cash=10000, commission=.001)
    
    stats = bt.run()
    
    # 🌙 Print Stats and Strategy
    print("--- LUNAR SNAPBACK STATS ---")
    print(stats)
    print("\n--- STRATEGY DETAILS ---")
    print(stats._strategy)

    # Optional: Plot the results
    # bt.plot()

except FileNotFoundError:
    print(f"Error: Data file not found at {data_path}. Please check the path.")
except Exception as e:
    print(f"An error occurred: {e}")