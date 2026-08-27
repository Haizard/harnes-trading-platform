import pandas as pd
import talib
from backtesting import Backtest, Strategy
from backtesting.lib import crossover

class LunarVelocity(Strategy):
    n1 = 20
    n2 = 50

    def init(self):
        # Indicators using self.I() and talib
        # self.I ensures the indicator is properly tracked by the backtester
        self.sma_fast = self.I(talib.SMA, self.data.Close, self.n1)
        self.sma_slow = self.I(talib.SMA, self.data.Close, self.n2)

    def next(self):
        # Entry Logic using the library's crossover utility for robustness
        # Long Entry: Fast SMA crosses above Slow SMA
        if crossover(self.sma_fast, self.sma_slow):
            if self.position.is_short:
                self.position.close()
            # size=0.95 represents 95% of current equity
            self.buy(size=0.95)

        # Short Entry: Fast SMA crosses below Slow SMA
        elif crossover(self.sma_slow, self.sma_fast):
            if self.position.is_long:
                self.position.close()
            self.sell(size=0.95)

# Data Loading and Preprocessing
data_path = 'c:/Users/Dell/Desktop/moon-dev-ai-agents-bot/src/data/rbi/BTC-USD-15m.csv'

try:
    df = pd.read_csv(data_path)
    
    # Clean column names
    df.columns = df.columns.str.strip().str.lower()
    
    # Map to required Backtesting.py format (Capitalized)
    mapping = {'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'}
    df = df.rename(columns=mapping)

    # Convert date/time column to DatetimeIndex (Required for backtesting.py)
    if 'datetime' in df.columns:
        df['datetime'] = pd.to_datetime(df['datetime'])
        df.set_index('datetime', inplace=True)
    elif 'timestamp' in df.columns:
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df.set_index('timestamp', inplace=True)
    elif 'date' in df.columns:
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)

    # Sort index to ensure chronological order
    df = df.sort_index()
    
    # Drop rows with missing OHLC data
    df = df.dropna(subset=['Open', 'High', 'Low', 'Close'])

    # Run Backtest
    # cash: Initial starting balance
    # commission: .002 represents 0.2%
    # trade_on_close: Execute trades at the closing price of the signal candle
    bt = Backtest(df, LunarVelocity, cash=100000, commission=.002, trade_on_close=True)
    stats = bt.run()

    # Print results
    print(stats)
    print("\nStrategy Details:")
    print(stats._strategy)

    # Optional: Plot the results (uncomment if running in a GUI/Notebook environment)
    # bt.plot()

except FileNotFoundError:
    print(f"Error: CSV file not found at {data_path}. Please check the path.")
except Exception as e:
    print(f"An error occurred: {e}")