import sys
import os
import time
import subprocess

# Force-install required packages if missing
def ensure_package(name, version=None):
    try:
        __import__(name)
    except ImportError:
        pkg = f"{name}=={version}" if version else name
        print(f"[SETUP] Installing {pkg}...", flush=True)
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", pkg])

ensure_package("fastapi", "0.109.0")
ensure_package("uvicorn", "0.25.0")
ensure_package("pydantic", "2.5.3")

print('=' * 60, flush=True)
print('MOON DEV TRADING PLATFORM', flush=True)
print(f'Python: {sys.version}', flush=True)
print(f'CWD: {os.getcwd()}', flush=True)
print(f'PORT env: {os.environ.get("PORT", "not set")}', flush=True)
print('=' * 60, flush=True)

# Start HTTP server on the PORT Northflank gives us
port = int(os.environ.get("PORT", "8000"))

import threading

def start_dashboard():
    """Start the web dashboard. Returns True if successful."""
    try:
        import uvicorn
        from src.web_dashboard import app as dashboard_app
        print(f"[DASHBOARD] Starting FastAPI dashboard on port {port}...", flush=True)
        uvicorn.run(dashboard_app, host="0.0.0.0", port=port, log_level="error")
        return True
    except Exception as e:
        print(f"[DASHBOARD] Failed: {e}", flush=True)
        return False

# Try dashboard first, fallback to simple server
dashboard_ok = False
try:
    # Quick import test
    from src.web_dashboard import app
    print(f"[DASHBOARD] Import OK, starting server...", flush=True)
    dashboard_thread = threading.Thread(target=start_dashboard, daemon=True)
    dashboard_thread.start()
    time.sleep(1)  # Give it a moment to start
    dashboard_ok = True
except Exception as e:
    print(f"[DASHBOARD] Import failed: {e}", flush=True)

if not dashboard_ok:
    # Fallback: simple health check server
    from http.server import HTTPServer, BaseHTTPRequestHandler
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Moon Dev Trading Platform is running!")
        def log_message(self, format, *args):
            pass
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"[DASHBOARD] Fallback: Simple server on port {port}", flush=True)

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
