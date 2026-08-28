"""
Run both the trading engine and web dashboard concurrently.
"""
import sys
import os
import asyncio
import threading
import traceback

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

sys.path.insert(0, ".")

def start_dashboard():
    """Start the web dashboard in a separate thread."""
    try:
        from src.dashboard import run_dashboard
        run_dashboard(port=8000)
    except Exception as e:
        print("[DASHBOARD] Error: " + str(e))

def main():
    print("[STARTUP] Moon Dev Trading Platform starting...")
    print("[STARTUP] Python " + sys.version)
    print("[STARTUP] CWD: " + os.getcwd())
    
    capital = 25.0
    if len(sys.argv) > 1:
        try:
            capital = float(sys.argv[1])
        except ValueError:
            pass
    
    # Start dashboard in background thread
    try:
        dashboard_thread = threading.Thread(target=start_dashboard, daemon=True)
        dashboard_thread.start()
        print("[STARTUP] Dashboard running on http://0.0.0.0:8000")
    except Exception as e:
        print("[STARTUP] Dashboard error: " + str(e))
    
    # Start trading engine
    try:
        from src.micro_engine import MicroEngine
        engine = MicroEngine(capital=capital)
        asyncio.run(engine.run())
    except KeyboardInterrupt:
        print("[STARTUP] Interrupted")
    except Exception as e:
        print("[STARTUP] Engine error: " + str(e))
        traceback.print_exc()
        # Keep dashboard running even if engine fails
        print("[STARTUP] Keeping dashboard alive...")
        while True:
            try:
                import time
                time.sleep(60)
            except KeyboardInterrupt:
                break


if __name__ == "__main__":
    main()
