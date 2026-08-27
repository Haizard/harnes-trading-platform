from src.agents.base_agent import BaseStrategy

class MockSMAStrategy(BaseStrategy):
    def generate_signals(self):
        return {'token': 'BTC', 'signal': 'buy', 'direction': 'long', 'metadata': {'reason': 'SMA Crossover'}}