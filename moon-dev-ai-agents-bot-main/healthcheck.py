import os
import sys
import time
import json

from http.server import HTTPServer, BaseHTTPRequestHandler

port = int(os.environ.get("PORT", "8000"))
print(f"[HEALTH] Starting health server on port {port}", flush=True)

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"status": "ok", "timestamp": time.time()}).encode())
    def log_message(self, format, *args):
        pass

server = HTTPServer(("0.0.0.0", port), HealthHandler)
print(f"[HEALTH] Server bound to 0.0.0.0:{port} - READY", flush=True)
server.serve_forever()
