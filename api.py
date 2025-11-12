# api.py (minimal + safe errors + simple chat page)
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel

app = FastAPI()

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

@app.get("/", response_class=HTMLResponse)
def index():
    return CHAT

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

# --- simple equity report based on state.json ---
@app.get("/report")
def report():
    """
    Reads the shared state file (STATE_PATH env or ./state.json)
    and returns a quick equity summary for the UI.
    """
    state_path = Path(os.environ.get("STATE_PATH", str(Path(__file__).with_name("state.json"))))
    try:
        if not state_path.exists():
            return {"report": f"state.json not found at {state_path}"}
        data = state_path.read_text(encoding="utf-8")
        import json as _json
        s = _json.loads(data)
        eq_now = s.get("equity_now_usd")
        eq_start = s.get("equity_day_start_usd")
        eq_change = s.get("equity_change_usd")
        paused = s.get("paused")
        symbols = s.get("symbols", [])
        sym_line = ", ".join([str(x.get('symbol')) for x in symbols if isinstance(x, dict)]) or "(none)"
        lines = [
            f"Equity now: {eq_now}",
            f"Day start: {eq_start}",
            f"Change: {eq_change}",
            f"Paused: {paused}",
            f"Symbols: {sym_line}",
        ]
        return {"report": "\n".join(lines)}
    except Exception as e:
        return {"report": f"[REPORT-ERR] {e}"}
