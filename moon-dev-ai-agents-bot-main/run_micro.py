"""Run the Micro-Cap Trading Engine."""
import asyncio
from src.micro_engine import MicroEngine

if __name__ == "__main__":
    engine = MicroEngine(capital=25.0)
    asyncio.run(engine.run())
