"""
Simple Web Dashboard for monitoring the trading engine.
Runs on port 8000, shows live stats and recent trades.
"""
import json
import os
from pathlib import Path
from datetime import datetime

# Minimal web server - no heavy dependencies needed
from http.server import HTTPServer, BaseHTTPRequestHandler

DATA_DIR = Path("src/data")


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(self._get_dashboard_html().encode())
        elif self.path == "/api/stats":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(self._get_stats(), default=str).encode())
        elif self.path == "/api/trades":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(self._get_recent_trades(), default=str).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def _get_stats(self):
        stats = {"scanner": {}, "trades": {}, "engine": {}}
        
        # Scanner stats
        scanner_file = DATA_DIR / "scanner" / "scanner_results.jsonl"
        if scanner_file.exists():
            lines = scanner_file.read_text().strip().split("\n")
            stats["scanner"]["total_scans"] = len([l for l in lines if l])
        
        # Trade stats
        trades_file = DATA_DIR / "paper_trading" / "paper_trades.jsonl"
        if trades_file.exists():
            trades = [json.loads(l) for l in trades_file.read_text().strip().split("\n") if l]
            entries = [t for t in trades if t.get("action") == "entry"]
            exits = [t for t in trades if t.get("action") == "exit"]
            wins = [t for t in exits if t.get("pnl_usd", 0) > 0]
            total_pnl = sum(t.get("pnl_usd", 0) for t in exits)
            
            stats["trades"] = {
                "total_entries": len(entries),
                "total_exits": len(exits),
                "wins": len(wins),
                "losses": len(exits) - len(wins),
                "win_rate": round(len(wins) / len(exits) * 100, 1) if exits else 0,
                "total_pnl": round(total_pnl, 4),
            }
        
        # Engine events
        events_file = DATA_DIR / "micro_engine" / "engine_events.jsonl"
        if events_file.exists():
            events = [json.loads(l) for l in events_file.read_text().strip().split("\n") if l]
            stats["engine"]["total_events"] = len(events)
            stats["engine"]["last_event"] = events[-1] if events else None
        
        return stats

    def _get_recent_trades(self):
        trades_file = DATA_DIR / "paper_trading" / "paper_trades.jsonl"
        if not trades_file.exists():
            return []
        trades = []
        for line in trades_file.read_text().strip().split("\n"):
            if line:
                try:
                    trades.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return trades[-20:]  # Last 20 trades

    def _get_dashboard_html(self):
        return """<!DOCTYPE html>
<html>
<head>
    <title>Moon Dev Trading Dashboard</title>
    <meta http-equiv="refresh" content="30">
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
               background: #0a0a0a; color: #e0e0e0; margin: 0; padding: 20px; }
        .header { text-align: center; padding: 20px 0; border-bottom: 1px solid #333; }
        .header h1 { color: #00ff88; margin: 0; }
        .header p { color: #888; margin: 5px 0 0 0; }
        .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); 
                 gap: 15px; margin: 20px 0; }
        .stat-card { background: #1a1a1a; border: 1px solid #333; border-radius: 8px; 
                     padding: 15px; text-align: center; }
        .stat-card h3 { color: #00ff88; margin: 0 0 10px 0; font-size: 14px; }
        .stat-card .value { font-size: 28px; font-weight: bold; color: #fff; }
        .stat-card .label { color: #888; font-size: 12px; margin-top: 5px; }
        .positive { color: #00ff88 !important; }
        .negative { color: #ff4444 !important; }
        .trades { background: #1a1a1a; border: 1px solid #333; border-radius: 8px; 
                  padding: 15px; margin: 20px 0; }
        .trades h2 { color: #00ff88; margin: 0 0 15px 0; font-size: 16px; }
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid #333; }
        th { color: #888; font-weight: normal; font-size: 12px; }
        td { font-size: 14px; }
        .status { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 12px; }
        .status-entry { background: #00ff8822; color: #00ff88; }
        .status-exit { background: #ff444422; color: #ff4444; }
        .refresh-note { text-align: center; color: #666; font-size: 12px; margin-top: 20px; }
    </style>
</head>
<body>
    <div class="header">
        <h1>Moon Dev Trading Dashboard</h1>
        <p>Paper Trading Mode | Auto-refreshes every 30s</p>
    </div>
    
    <div class="stats" id="stats">
        <div class="stat-card">
            <h3>TOTAL ENTRIES</h3>
            <div class="value" id="entries">-</div>
        </div>
        <div class="stat-card">
            <h3>WIN RATE</h3>
            <div class="value" id="winrate">-</div>
        </div>
        <div class="stat-card">
            <h3>TOTAL P&L</h3>
            <div class="value" id="pnl">-</div>
        </div>
        <div class="stat-card">
            <h3>ENGINE EVENTS</h3>
            <div class="value" id="events">-</div>
        </div>
    </div>
    
    <div class="trades">
        <h2>Recent Trades</h2>
        <table>
            <thead>
                <tr>
                    <th>Action</th>
                    <th>Symbol</th>
                    <th>Amount</th>
                    <th>P&L</th>
                    <th>Status</th>
                </tr>
            </thead>
            <tbody id="trades">
                <tr><td colspan="5" style="text-align: center; color: #666;">Loading...</td></tr>
            </tbody>
        </table>
    </div>
    
    <div class="refresh-note">Auto-refreshes every 30 seconds</div>
    
    <script>
        async function loadData() {
            try {
                const statsRes = await fetch('/api/stats');
                const stats = await statsRes.json();
                
                document.getElementById('entries').textContent = stats.trades.total_entries || 0;
                document.getElementById('winrate').textContent = (stats.trades.win_rate || 0) + '%';
                
                const pnl = stats.trades.total_pnl || 0;
                const pnlEl = document.getElementById('pnl');
                pnlEl.textContent = '$' + pnl.toFixed(4);
                pnlEl.className = 'value ' + (pnl >= 0 ? 'positive' : 'negative');
                
                document.getElementById('events').textContent = stats.engine.total_events || 0;
                
                const tradesRes = await fetch('/api/trades');
                const trades = await tradesRes.json();
                
                const tbody = document.getElementById('trades');
                if (trades.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: #666;">No trades yet</td></tr>';
                } else {
                    tbody.innerHTML = trades.reverse().map(t => {
                        const pnl = t.pnl_usd || 0;
                        const pnlClass = pnl >= 0 ? 'positive' : 'negative';
                        return '<tr>' +
                            '<td><span class="status status-' + t.action + '">' + t.action.toUpperCase() + '<
span></td>' +
                            '<td>' + (t.symbol || '-') + '</td>' +
                            '<td>$' + (t.amount_usd || 0).toFixed(2) + '</td>' +
                            '<td class="' + pnlClass + '">$' + pnl.toFixed(4) + '</td>' +
                            '<td>' + (t.status || '-') + '</td>' +
                            '</tr>';
                    }).join('');
                }
            } catch (e) {
                console.error('Error loading data:', e);
            }
        }
        loadData();
    </script>
</body>
</html>"""


def run_dashboard(port=8000):
    server = HTTPServer(("0.0.0.0", port), DashboardHandler)
    print("[DASHBOARD] Running on http://0.0.0.0:" + str(port))
    server.serve_forever()


if __name__ == "__main__":
    run_dashboard()
