"""
Run the trading engine + web dashboard.
Dashboard MUST be the foreground process (binds to PORT) to keep the container alive.
"""
import sys
import os
import threading
import time
import traceback
import logging

# Ensure stdout is unbuffered
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# Load env
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

sys.path.insert(0, '.')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')

def log(msg):
    print(f'[{time.strftime("%H:%M:%S")}] {msg}', flush=True)

def run_engine():
    """Run the trading engine in a background thread."""
    time.sleep(3)  # Let dashboard start first
    try:
        log('[ENGINE] Starting micro engine...')
        from src.micro_engine import MicroEngine
        import asyncio
        capital = float(os.environ.get('CAPITAL', '25.0'))
        engine = MicroEngine(capital=capital)
        asyncio.run(engine.run())
    except Exception as e:
        log(f'[ENGINE] Error: {e}')
        traceback.print_exc()

def main():
    port = int(os.environ.get('PORT', '8000'))
    log(f'[STARTUP] Python {sys.version}')
    log(f'[STARTUP] CWD: {os.getcwd()}')
    log(f'[STARTUP] PORT: {port}')
    
    # Start trading engine in background
    t = threading.Thread(target=run_engine, daemon=True)
    t.start()
    log('[STARTUP] Engine thread started (background)')
    
    # Start dashboard in FOREGROUND (must bind to port to keep container alive)
    try:
        from src.dashboard import run_dashboard
        log(f'[STARTUP] Dashboard starting on 0.0.0.0:{port}')
        run_dashboard(port=port)
    except Exception as e:
        log(f'[STARTUP] Dashboard error: {e}')
        traceback.print_exc()
        # If dashboard fails, keep process alive anyway
        log('[STARTUP] Keeping process alive...')
        while True:
            time.sleep(60)

if __name__ == '__main__':
    main()
