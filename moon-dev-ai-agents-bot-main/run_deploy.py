import sys
import os
import time

print('=' * 60, flush=True)
print('MOON DEV TRADING PLATFORM', flush=True)
print(f'Python: {sys.version}', flush=True)
print(f'CWD: {os.getcwd()}', flush=True)
print(f'PORT env: {os.environ.get("PORT", "not set")}', flush=True)
print('=' * 60, flush=True)

# Start HTTP server on the PORT Northflank gives us
from http.server import HTTPServer, BaseHTTPRequestHandler
import json

port = int(os.environ.get("PORT", "8000"))

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        msg = "Moon Dev Trading Platform is running!"
        self.wfile.write(msg.encode())
    def log_message(self, format, *args):
        pass  # Suppress request logging

print(f"Binding to 0.0.0.0:{port}", flush=True)
server = HTTPServer(("0.0.0.0", port), Handler)
print(f"Server running on port {port}", flush=True)
sys.stdout.flush()

# Now start the trading engine in a background thread
def start_engine():
    time.sleep(5)
    try:
        sys.path.insert(0, ".")
        from dotenv import load_dotenv
        load_dotenv()
        print("[ENGINE] Loading micro engine...", flush=True)
        from src.micro_engine import MicroEngine
        import asyncio
        capital = float(os.environ.get("CAPITAL", "25.0"))
        engine = MicroEngine(capital=capital)
        print("[ENGINE] Starting...", flush=True)
        asyncio.run(engine.run())
    except Exception as e:
        print(f"[ENGINE] Error: {e}", flush=True)
        import traceback
        traceback.print_exc()

import threading
t = threading.Thread(target=start_engine, daemon=True)
t.start()
print("[ENGINE] Background thread started", flush=True)

# This blocks forever - keeps container alive
server.serve_forever()
