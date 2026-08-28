import sys
sys.path.insert(0, ".")
from src.micro_engine import MicroEngine

if __name__ == "__main__":
    capital = float(sys.argv[1]) if len(sys.argv) > 1 else 25.0
    engine = MicroEngine(capital=capital)
    import asyncio
    asyncio.run(engine.run())
