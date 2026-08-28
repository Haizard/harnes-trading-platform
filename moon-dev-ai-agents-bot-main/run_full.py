"""
Run both the trading engine and web dashboard concurrently.
"""
import sys
import asyncio
import threading
from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, ".")

def start_dashboard():
    """Start the web dashboard in a separate thread."""
    from src.dashboard import run_dashboard
    run_dashboard(port=8000)

def main():
    capital = float(sys.argv[1]) if len(sys.argv) > 1 else 25.0
    
    # Start dashboard in background thread
    dashboard_thread = threading.Thread(target=start_dashboard, daemon=True)
    dashboard_thread.start()
    print("[STARTUP] Dashboard running on http://0.0.0.0:8000")
    
    # Start trading engine
    from src.micro_engine import MicroEngine
    engine = MicroEngine(capital=capital)
    asyncio.run(engine.run())


if __name__ == "__main__":
    main()
