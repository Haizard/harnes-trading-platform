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
        # In Docker/Northflank, env vars are injected directly.
        # Only load .env file if it exists (local dev mode).
        if os.path.exists(".env"):
            from dotenv import load_dotenv
            load_dotenv(override=False)  # Don't override injected env vars
        
        # === Twitter auto-login (server has clean IP, no Cloudflare) ===
        twitter_user = os.environ.get("TWITTER_USERNAME", "")
        twitter_email = os.environ.get("TWITTER_EMAIL", "")
        twitter_pass = os.environ.get("TWITTER_PASSWORD", "")
        cookies_path = "cookies.json"
        
        if all([twitter_user, twitter_email, twitter_pass]) and not os.path.exists(cookies_path):
            print("[TWITTER] No cookies.json found, logging in...", flush=True)
            try:
                import asyncio as _aio
                async def _twitter_login():
                    from twikit import Client
                    client = Client()
                    await client.login(
                        auth_info_1=twitter_user,
                        auth_info_2=twitter_email,
                        password=twitter_pass,
                        cookies_file=cookies_path
                    )
                    print("[TWITTER] Login successful! cookies.json saved.", flush=True)
                    return True
                _aio.run(_twitter_login())
            except Exception as e:
                print(f"[TWITTER] Login failed: {e}", flush=True)
                print("[TWITTER] Sentiment will be neutral until cookies are set up.", flush=True)
        elif os.path.exists(cookies_path):
            print("[TWITTER] cookies.json found - sentiment enabled.", flush=True)
        else:
            print("[TWITTER] No credentials in env - sentiment disabled.", flush=True)
        
        # === PostgreSQL connection ===
        db_url = os.environ.get("LUCERIS_DATABASE_URL", "")
        if db_url:
            try:
                from src.db_storage import get_pool
                pool = get_pool()
                if pool:
                    print("[DB] Connected to PostgreSQL", flush=True)
                else:
                    print("[DB] PostgreSQL connection failed", flush=True)
            except Exception as e:
                print(f"[DB] PostgreSQL error: {e}", flush=True)
        else:
            print("[DB] No LUCERIS_DATABASE_URL — using JSON fallback", flush=True)

        print("[ENGINE] Loading micro engine...", flush=True)
        # Debug: show which key env vars are set (not their values)
        for k in ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_BEDROCK_REGION", 
                  "BIRDEYE_API_KEY", "RPC_ENDPOINT", "SOLANA_PRIVATE_KEY",
                  "TWITTER_USERNAME", "TELEGRAM_BOT_TOKEN", "LUCERIS_DATABASE_URL"]:
            status = "SET" if os.environ.get(k) else "MISSING"
            print(f"[ENGINE]   {k}: {status}", flush=True)
        from src.micro_engine import MicroEngine
        import asyncio
        capital = float(os.environ.get("CAPITAL", "100.0"))
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
