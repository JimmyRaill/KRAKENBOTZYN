# api.py (minimal + safe errors + simple chat page)
from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()

# Enable CORS for real-time updates
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- tiny chat page at "/" ---
CHAT = """
<!doctype html><meta charset="utf-8"><title>KrakenBot Chat</title>
<style>
body{font-family:system-ui,Arial;background:#0b0c10;color:#e5e7eb;margin:0}
.container{max-width:800px;margin:40px auto;padding:20px}
#log{white-space:pre-wrap;background:#111827;border:1px solid #374151;border-radius:8px;padding:12px;height:420px;overflow:auto}
.row{display:flex;gap:8px;margin:10px 0}
input,button{font-size:14px}
input[type=text]{flex:1;padding:10px;border:1px solid #374151;border-radius:6px;background:#0b0c10;color:#e5e7eb}
button{padding:10px 14px;border:1px solid #374151;border-radius:6px;background:#1f2937;color:#e5e7eb;cursor:pointer}
button:hover{background:#374151}
</style>
<div class="container">
  <h2>Talk to KrakenBot</h2>
  <div class="row">
    <input id="inp" placeholder='Try: "how much did we make today?" or "what’s my balance?"' />
    <button id="send">Send</button>
  </div>
  <div id="log"></div>
</div>
<script>
const logEl = document.getElementById('log');
function log(s){ logEl.textContent += s + "\\n"; logEl.scrollTop = 1e9; }
async function send(){
  const text = document.getElementById('inp').value.trim();
  if(!text) return;
  log("> " + text);
  try{
    const r = await fetch("/ask", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ text })
    });
    const j = await r.json();
    log(j.answer || JSON.stringify(j));
    if (j.trace) log("\\nTRACE:\\n" + j.trace);
  }catch(e){ log("Network error: " + e.message); }
  document.getElementById('inp').value="";
}
document.getElementById('send').onclick = send;
document.getElementById('inp').addEventListener("keydown", e=>{ if(e.key==="Enter") send(); });
</script>
"""

@app.get("/chat", response_class=HTMLResponse)
def chat():
    return CHAT

@app.get("/", response_class=HTMLResponse)
def dashboard():
    """Professional real-time trading dashboard."""
    return DASHBOARD

# Professional Trading Dashboard HTML
DASHBOARD = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>KrakenBot Trading Dashboard</title>
    <script src="https://unpkg.com/lightweight-charts@4.1.0/dist/lightweight-charts.standalone.production.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: linear-gradient(135deg, #0b0c10 0%, #1a1d29 100%);
            color: #e5e7eb;
            padding: 20px;
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
            padding: 20px;
            background: rgba(31, 41, 55, 0.5);
            border-radius: 12px;
            backdrop-filter: blur(10px);
        }
        .header h1 { 
            font-size: 28px;
            background: linear-gradient(135deg, #3b82f6, #8b5cf6);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .status { display: flex; align-items: center; gap: 10px; }
        .status-dot {
            width: 12px;
            height: 12px;
            border-radius: 50%;
            background: #10b981;
            animation: pulse 2s infinite;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        .status.paused .status-dot { background: #ef4444; }
        
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 20px;
        }
        .card {
            background: rgba(31, 41, 55, 0.6);
            border-radius: 12px;
            padding: 20px;
            border: 1px solid rgba(59, 130, 246, 0.2);
            backdrop-filter: blur(10px);
        }
        .card h2 {
            font-size: 16px;
            color: #9ca3af;
            margin-bottom: 10px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        .metric-value {
            font-size: 32px;
            font-weight: bold;
            margin: 10px 0;
        }
        .positive { color: #10b981; }
        .negative { color: #ef4444; }
        .neutral { color: #6b7280; }
        
        .chart-container {
            grid-column: 1 / -1;
            height: 400px;
            margin-bottom: 20px;
        }
        #equityChart { width: 100%; height: 100%; }
        
        .positions-table {
            width: 100%;
            border-collapse: collapse;
        }
        .positions-table th {
            text-align: left;
            padding: 12px;
            background: rgba(59, 130, 246, 0.1);
            font-weight: 600;
            font-size: 14px;
        }
        .positions-table td {
            padding: 12px;
            border-bottom: 1px solid rgba(75, 85, 99, 0.3);
        }
        .positions-table tr:hover {
            background: rgba(59, 130, 246, 0.05);
        }
        
        .trades-list {
            max-height: 300px;
            overflow-y: auto;
        }
        .trade-item {
            padding: 12px;
            margin: 8px 0;
            background: rgba(17, 24, 39, 0.5);
            border-radius: 8px;
            border-left: 3px solid #3b82f6;
        }
        .trade-item.buy { border-left-color: #10b981; }
        .trade-item.sell { border-left-color: #ef4444; }
        
        .badge {
            display: inline-block;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 600;
        }
        .badge.success { background: rgba(16, 185, 129, 0.2); color: #10b981; }
        .badge.danger { background: rgba(239, 68, 68, 0.2); color: #ef4444; }
        .badge.warning { background: rgba(245, 158, 11, 0.2); color: #f59e0b; }
        .badge.info { background: rgba(59, 130, 246, 0.2); color: #3b82f6; }
        
        .footer {
            text-align: center;
            margin-top: 30px;
            padding: 20px;
            color: #6b7280;
            font-size: 14px;
        }
        
        .nav-links {
            display: flex;
            gap: 15px;
        }
        .nav-links a {
            color: #3b82f6;
            text-decoration: none;
            padding: 8px 16px;
            border-radius: 6px;
            background: rgba(59, 130, 246, 0.1);
            transition: all 0.3s;
        }
        .nav-links a:hover {
            background: rgba(59, 130, 246, 0.2);
        }
    </style>
</head>
<body>
    <div class="header">
        <div>
            <h1>🤖 KrakenBot AI Trading Dashboard</h1>
            <p style="margin-top: 8px; color: #9ca3af;">Self-Learning Cryptocurrency Trading Bot</p>
        </div>
        <div style="display: flex; align-items: center; gap: 20px;">
            <div class="status" id="botStatus">
                <div class="status-dot"></div>
                <span>Active</span>
            </div>
            <div class="nav-links">
                <a href="/chat">💬 Chat</a>
            </div>
        </div>
    </div>

    <div class="grid">
        <div class="card">
            <h2>💰 Equity</h2>
            <div class="metric-value" id="equity">$0.00</div>
            <div id="equityChange" class="neutral">+$0.00 (0.00%)</div>
        </div>
        
        <div class="card">
            <h2>📊 Open Positions</h2>
            <div class="metric-value" id="openPositions">0</div>
            <div class="neutral">Active trades</div>
        </div>
        
        <div class="card">
            <h2>🎯 Win Rate</h2>
            <div class="metric-value" id="winRate">0%</div>
            <div class="neutral" id="winRateSub">No trades yet</div>
        </div>
        
        <div class="card">
            <h2>📈 Total Trades</h2>
            <div class="metric-value" id="totalTrades">0</div>
            <div class="neutral" id="tradeSub">Learning...</div>
        </div>
    </div>

    <div class="card chart-container">
        <h2>📈 Equity Performance</h2>
        <div id="equityChart"></div>
    </div>

    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); gap: 20px;">
        <div class="card">
            <h2>💼 Active Positions</h2>
            <table class="positions-table" id="positionsTable">
                <thead>
                    <tr>
                        <th>Symbol</th>
                        <th>Size</th>
                        <th>Entry</th>
                        <th>Current</th>
                        <th>P/L</th>
                    </tr>
                </thead>
                <tbody id="positionsBody">
                    <tr><td colspan="5" style="text-align: center; color: #6b7280;">No open positions</td></tr>
                </tbody>
            </table>
        </div>
        
        <div class="card">
            <h2>🕒 Recent Trades</h2>
            <div class="trades-list" id="tradesList">
                <div style="text-align: center; color: #6b7280; padding: 20px;">No trades yet</div>
            </div>
        </div>
    </div>

    <div class="footer">
        <p>KrakenBot Self-Learning AI • Last updated: <span id="lastUpdate">Never</span></p>
        <p style="margin-top: 8px;">📊 <span id="statsText">0 decisions, 0 trades, 0 snapshots</span></p>
    </div>

    <script>
        // Initialize chart
        const chartContainer = document.getElementById('equityChart');
        const chart = LightweightCharts.createChart(chartContainer, {
            width: chartContainer.clientWidth,
            height: 350,
            layout: {
                background: { color: 'transparent' },
                textColor: '#9ca3af',
            },
            grid: {
                vertLines: { color: 'rgba(75, 85, 99, 0.2)' },
                horzLines: { color: 'rgba(75, 85, 99, 0.2)' },
            },
            crosshair: {
                mode: LightweightCharts.CrosshairMode.Normal,
            },
            rightPriceScale: {
                borderColor: 'rgba(75, 85, 99, 0.5)',
            },
            timeScale: {
                borderColor: 'rgba(75, 85, 99, 0.5)',
                timeVisible: true,
                secondsVisible: false,
            },
        });

        const lineSeries = chart.addLineSeries({
            color: '#3b82f6',
            lineWidth: 2,
            priceFormat: {
                type: 'price',
                precision: 2,
                minMove: 0.01,
            },
        });

        // Auto-resize chart
        window.addEventListener('resize', () => {
            chart.applyOptions({ width: chartContainer.clientWidth });
        });

        // Fetch and update dashboard
        async function updateDashboard() {
            try {
                const response = await fetch('/api/dashboard');
                const data = await response.json();
                
                // Update equity
                document.getElementById('equity').textContent = `$${data.equity.current.toFixed(2)}`;
                const changeEl = document.getElementById('equityChange');
                const change = data.equity.change || 0;
                const changePct = data.equity.change_pct || 0;
                changeEl.textContent = `${change >= 0 ? '+' : ''}$${change.toFixed(2)} (${changePct.toFixed(2)}%)`;
                changeEl.className = change >= 0 ? 'positive' : change < 0 ? 'negative' : 'neutral';
                
                // Update positions (with defensive check)
                const positions = Array.isArray(data.positions) ? data.positions : [];
                const posCount = positions.filter(p => (p.balance_usd || 0) > 1).length;
                document.getElementById('openPositions').textContent = posCount;
                
                // Update win rate
                if (data.performance && data.performance.total_trades > 0) {
                    const winRate = data.performance.win_rate || 0;
                    document.getElementById('winRate').textContent = `${(winRate * 100).toFixed(1)}%`;
                    document.getElementById('winRateSub').textContent = `${data.performance.wins || 0}W / ${data.performance.losses || 0}L`;
                }
                
                // Update trades count
                document.getElementById('totalTrades').textContent = data.stats.trades || 0;
                document.getElementById('tradeSub').textContent = `${data.stats.decisions || 0} decisions made`;
                
                // Update status
                const statusEl = document.getElementById('botStatus');
                if (data.paused) {
                    statusEl.classList.add('paused');
                    statusEl.querySelector('span').textContent = 'Paused';
                } else {
                    statusEl.classList.remove('paused');
                    statusEl.querySelector('span').textContent = 'Active';
                }
                
                // Update positions table
                const positionsBody = document.getElementById('positionsBody');
                const validPositions = Array.isArray(data.positions) ? data.positions : [];
                if (validPositions.length > 0) {
                    const activePos = validPositions.filter(p => (p.balance_usd || 0) > 1);
                    if (activePos.length > 0) {
                        positionsBody.innerHTML = activePos.map(p => {
                            const pnl = (p.balance_usd || 0) - (p.entry_value_usd || 0);
                            const pnlClass = pnl >= 0 ? 'positive' : 'negative';
                            return `
                                <tr>
                                    <td><strong>${p.symbol || 'Unknown'}</strong></td>
                                    <td>${(p.balance || 0).toFixed(4)}</td>
                                    <td>$${(p.entry_price || 0).toFixed(2)}</td>
                                    <td>$${(p.current_price || 0).toFixed(2)}</td>
                                    <td class="${pnlClass}">${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}</td>
                                </tr>
                            `;
                        }).join('');
                    } else {
                        positionsBody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: #6b7280;">No open positions</td></tr>';
                    }
                } else {
                    positionsBody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: #6b7280;">No open positions</td></tr>';
                }
                
                // Update recent trades
                const tradesList = document.getElementById('tradesList');
                if (data.recent_trades && data.recent_trades.length > 0) {
                    tradesList.innerHTML = data.recent_trades.slice(0, 10).map(t => {
                        const side = t.side || 'unknown';
                        const sideClass = side.toLowerCase();
                        return `
                            <div class="trade-item ${sideClass}">
                                <div style="display: flex; justify-content: space-between; align-items: center;">
                                    <div>
                                        <strong>${t.symbol || 'Unknown'}</strong>
                                        <span class="badge ${sideClass === 'buy' ? 'success' : 'danger'}">${side.toUpperCase()}</span>
                                    </div>
                                    <div style="text-align: right;">
                                        <div>${t.size || 0} @ $${(t.price || 0).toFixed(2)}</div>
                                        <div style="font-size: 12px; color: #9ca3af;">${new Date(t.timestamp).toLocaleString()}</div>
                                    </div>
                                </div>
                                ${t.reason ? `<div style="margin-top: 8px; font-size: 12px; color: #9ca3af;">${t.reason}</div>` : ''}
                            </div>
                        `;
                    }).join('');
                } else {
                    tradesList.innerHTML = '<div style="text-align: center; color: #6b7280; padding: 20px;">No trades yet</div>';
                }
                
                // Update stats
                document.getElementById('statsText').textContent = 
                    `${data.stats.decisions || 0} decisions, ${data.stats.trades || 0} trades, ${data.stats.performance_snapshots || 0} snapshots`;
                
                document.getElementById('lastUpdate').textContent = new Date().toLocaleTimeString();
                
            } catch (error) {
                console.error('Dashboard update error:', error);
            }
        }

        // Fetch equity history for chart
        async function updateChart() {
            try {
                const response = await fetch('/api/equity_history?hours=24');
                const data = await response.json();
                
                if (data.history && data.history.length > 0) {
                    const chartData = data.history.map(item => ({
                        time: new Date(item.time).getTime() / 1000,
                        value: item.value
                    }));
                    lineSeries.setData(chartData);
                }
            } catch (error) {
                console.error('Chart update error:', error);
            }
        }

        // Initial load
        updateDashboard();
        updateChart();
        
        // Auto-refresh every 3 seconds
        setInterval(updateDashboard, 3000);
        setInterval(updateChart, 10000);
    </script>
</body>
</html>
"""

# --- POST /ask (safe wrapper that never hides the error) ---
class AskIn(BaseModel):
    text: str
    token: Optional[str] = None

@app.post("/ask")
async def ask(a: AskIn):
    try:
        from llm_agent import ask_llm
        from telemetry_db import log_conversation
        
        # Get response
        out = ask_llm(a.text)
        
        # Log conversation for learning
        try:
            log_conversation(a.text, out)
        except Exception:
            pass
        
        return {"answer": out}
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        # Return 200 so the UI shows the error text instead of blank 500 page
        return JSONResponse(status_code=200, content={
            "answer": f"[Backend Error] {e.__class__.__name__}: {e}",
            "trace": tb[-1500:]
        })

# --- optional: GET /ask?q=... (lets you ask from the URL) ---
@app.get("/ask")
async def ask_get(q: str = Query(..., description="Your question")):
    try:
        from llm_agent import ask_llm
        return {"answer": ask_llm(q)}
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        return JSONResponse(status_code=200, content={
            "answer": f"[Backend Error] {e.__class__.__name__}: {e}",
            "trace": tb[-1500:]
        })

# --- API Endpoints for Dashboard ---

@app.get("/api/dashboard")
def get_dashboard_data():
    """Get comprehensive dashboard data including positions, P/L, trades, and metrics."""
    try:
        from telemetry_db import get_db, get_recent_trades
        from trade_analyzer import get_performance_summary
        from time_context import get_context_summary
        
        state_path = Path(os.environ.get("STATE_PATH", str(Path(__file__).with_name("state.json"))))
        
        # Read current state
        state = {}
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
        
        # Get trading history
        recent_trades = []
        try:
            recent_trades = get_recent_trades(limit=20)
        except Exception:
            pass
        
        # Get performance metrics
        try:
            perf = get_performance_summary(days=7)
        except Exception:
            perf = {}
        
        # Get database stats
        stats = {"decisions": 0, "trades": 0, "performance_snapshots": 0}
        try:
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) as count FROM decisions")
                stats["decisions"] = dict(cursor.fetchone())["count"]
                cursor.execute("SELECT COUNT(*) as count FROM trades")
                stats["trades"] = dict(cursor.fetchone())["count"]
                cursor.execute("SELECT COUNT(*) as count FROM performance")
                stats["performance_snapshots"] = dict(cursor.fetchone())["count"]
        except Exception:
            pass
        
        # Normalize positions - convert dict to list if needed
        positions_raw = state.get("symbols", {})
        if isinstance(positions_raw, dict):
            positions = list(positions_raw.values())
        elif isinstance(positions_raw, list):
            positions = positions_raw
        else:
            positions = []
        
        return {
            "equity": {
                "current": state.get("equity_now_usd", 0),
                "day_start": state.get("equity_day_start_usd", 0),
                "change": state.get("equity_change_usd", 0),
                "change_pct": state.get("equity_change_pct", 0)
            },
            "positions": positions,
            "paused": state.get("paused", False),
            "recent_trades": recent_trades,
            "performance": perf,
            "stats": stats,
            "timestamp": state.get("ts", datetime.now().isoformat())
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/api/equity_history")
def get_equity_history(hours: int = 24):
    """Get equity history for charting."""
    try:
        from telemetry_db import get_db
        
        cutoff = datetime.now() - timedelta(hours=hours)
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT timestamp, equity_usd, equity_change_usd
                FROM performance
                WHERE timestamp >= ?
                ORDER BY timestamp ASC
            """, (cutoff.isoformat(),))
            
            history = []
            for row in cursor.fetchall():
                r = dict(row)
                history.append({
                    "time": r["timestamp"],
                    "value": r["equity_usd"]
                })
            
            return {"history": history}
    except Exception as e:
        return {"history": [], "error": str(e)}
