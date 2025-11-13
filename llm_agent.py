# llm_agent.py — language/logic brain for your autonomous trading bot + lightweight memory

import os
import json
import re
import traceback
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional

from dotenv import load_dotenv

# --- .env (next to this file) ---
ENV_PATH = Path(__file__).with_name(".env")
load_dotenv(dotenv_path=str(ENV_PATH), override=True)

# --- OpenAI client (v1 SDK) ---
MODEL_NAME: str = os.environ.get("LLM_MODEL", "gpt-4o-mini").strip()
OPENAI_KEY: str = os.environ.get("OPENAI_API_KEY", "").strip()

# Import in a way that keeps the type checker calm
try:
    from openai import OpenAI as _OpenAI  # runtime class
except Exception:
    _OpenAI = None  # type: ignore[assignment]

_client: Optional[Any] = None  # late-inited, typed as Any for pyright sanity


def _ensure_client() -> Tuple[Any, Optional[str]]:
    """
    Ensure OpenAI client exists and key is present.
    Returns (client, error_message_or_None).
    """
    global _client
    if _client is not None:
        return _client, None

    if _OpenAI is None:
        return None, "OpenAI SDK not available. Install with: pip install openai"

    if not OPENAI_KEY:
        return None, (
            "Missing OPENAI_API_KEY in .env. Add it and restart the server:\n"
            "  OPENAI_API_KEY=sk-..."
        )

    _client = _OpenAI(api_key=OPENAI_KEY)  # type: ignore[operator]
    return _client, None


# ---------- Self-learning imports ----------
try:
    from trade_analyzer import get_learning_summary, get_performance_summary
    from time_context import get_prompt_context, get_context_summary
    LEARNING_ENABLED = True
except ImportError:
    LEARNING_ENABLED = False
    # Stub functions for type checker
    def get_learning_summary() -> str:  # type: ignore
        return "(learning not available)"
    def get_context_summary() -> str:  # type: ignore
        return "(context not available)"
    def get_performance_summary(days: int = 7) -> Dict[str, Any]:  # type: ignore
        return {"error": "performance not available"}
    def get_prompt_context() -> str:  # type: ignore
        return "(prompt context not available)"

# ---------- Paths ----------
# Use the same STATE_PATH the autopilot writes (falls back to local state.json)
STATE_PATH = Path(os.environ.get("STATE_PATH", str(Path(__file__).with_name("state.json"))))
_MEM_PATH = Path(__file__).with_name("memory.json")


# ---------- Memory ----------
def _mem_load() -> Dict[str, Any]:
    try:
        if not _MEM_PATH.exists():
            return {"notes": [], "last_id": 0}
        with _MEM_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("notes", [])
        data.setdefault("last_id", 0)
        return data
    except Exception:
        return {"notes": [], "last_id": 0}


def _mem_save(mem: Dict[str, Any]) -> None:
    try:
        with _MEM_PATH.open("w", encoding="utf-8") as f:
            json.dump(mem, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("[MEM-WRITE-ERR]", e)


def _mem_add(text: str, tags: Optional[List[str]] = None) -> Dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {"ok": False, "msg": "Empty note."}

    mem = _mem_load()
    for n in mem["notes"]:
        if n.get("text", "").strip().lower() == text.lower():
            n["hits"] = int(n.get("hits", 0)) + 1
            _mem_save(mem)
            return {"ok": True, "msg": "Already remembered (reinforced)."}

    mem["last_id"] = int(mem.get("last_id", 0)) + 1
    mem["notes"].append({
        "id": mem["last_id"],
        "text": text,
        "tags": tags or [],
        "hits": 1,
    })

    # keep it light
    if len(mem["notes"]) > 200:
        mem["notes"] = mem["notes"][-200:]

    _mem_save(mem)
    return {"ok": True, "msg": "Saved.", "id": mem["last_id"]}


def _mem_forget(pattern: str) -> Dict[str, Any]:
    pat = (pattern or "").strip().lower()
    if not pat:
        return {"ok": False, "msg": "Empty pattern."}
    mem = _mem_load()
    before = len(mem["notes"])
    mem["notes"] = [n for n in mem["notes"] if pat not in n.get("text", "").lower()]
    removed = before - len(mem["notes"])
    _mem_save(mem)
    return {"ok": True, "removed": removed}


def _mem_summary(max_items: int = 12) -> str:
    mem = _mem_load()
    notes = mem.get("notes", [])
    if not notes:
        return "(no memory)"
    notes = sorted(notes, key=lambda n: int(n.get("hits", 0)), reverse=True)[:max_items]
    return "\n".join(f"- {n.get('text')}" for n in notes)


def _auto_capture_identity(user_text: str) -> None:
    t = user_text.strip()
    m = re.search(r"\bmy name is\s+([A-Za-z0-9_.\- ']{2,40})", t, flags=re.I)
    if m:
        _mem_add(f"User prefers to be called {m.group(1).strip()}.", tags=["identity"])
        return
    m = re.search(r"\bcall me\s+([A-Za-z0-9_.\- ']{2,40})", t, flags=re.I)
    if m:
        _mem_add(f"User prefers to be called {m.group(1).strip()}.", tags=["identity"])


# ---------- Trading helpers - CRITICAL: Use Status Service for authoritative data ----------
def _get_trading_status() -> Dict[str, Any]:
    """
    CRITICAL: Get AUTHORITATIVE trading data from Status Service.
    NEVER use state.json or LLM memory for trading data - always fetch from Kraken.
    """
    try:
        from status_service import (
            get_mode,
            get_balances,
            get_open_orders,
            get_trades,
            get_activity_summary,
            get_last_sync_time,
            healthcheck,
            auto_sync_if_needed
        )
        
        # CRITICAL: Auto-sync FIRST to ensure all data is fresh
        auto_sync_if_needed()
        
        # Get authoritative data
        summary_24h = get_activity_summary("24h")
        summary_7d = get_activity_summary("7d")
        summary_30d = get_activity_summary("30d")
        recent_trades = get_trades(limit=20)  # Last 20 trades for details
        
        return {
            "mode": get_mode(),
            "balances": get_balances(),
            "open_orders": get_open_orders(),
            "recent_trades": recent_trades,  # CRITICAL: Actual trade details, not just counts
            "last_sync": get_last_sync_time(),
            "summary_24h": summary_24h,
            "summary_7d": summary_7d,
            "summary_30d": summary_30d,
            "health": healthcheck()
        }
    except Exception as e:
        return {"error": f"StatusService unavailable: {e}"}

def _read_state() -> Dict[str, Any]:
    """Legacy state.json reader - USE _get_trading_status() FOR TRADING DATA."""
    try:
        if not STATE_PATH.exists():
            return {"note": f"state.json not found at {STATE_PATH}"}
        with STATE_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        return {"error": f"failed_to_read_state: {e}"}


def _run_router(cmd: str) -> str:
    try:
        from commands import handle
        return handle(cmd)
    except Exception as e:
        return f"[COMMAND-ERR] {e}"


def _summarize_state_for_prompt(s: Dict[str, Any]) -> str:
    if not isinstance(s, dict):
        return "no-telemetry"

    parts: List[str] = []
    if "equity_now_usd" in s:
        parts.append(f"equity_now_usd={s.get('equity_now_usd')}")
    if "equity_change_usd" in s:
        parts.append(f"equity_change_usd={s.get('equity_change_usd')}")
    if "paused" in s:
        parts.append(f"paused={s.get('paused')}")
    if "autopilot_running" in s:
        parts.append(f"autopilot_running={s.get('autopilot_running')}")
    if "last_loop_at" in s:
        parts.append(f"last_loop_at={s.get('last_loop_at')}")

    sy_items = s.get("symbols") or []
    if isinstance(sy_items, list) and sy_items:
        preview: List[str] = []
        for x in sy_items[:6]:
            try:
                sym = x.get("symbol")
                px = x.get("price")
                pv = x.get("pos_value")
                preview.append(f"{sym}: px={px}, pos_val={pv}")
            except Exception:
                pass
        if preview:
            parts.append("symbols=[" + "; ".join(preview) + "]")

    return " | ".join(parts) if parts else "no-telemetry"


# ---------- Trading Command Execution ----------
def _execute_trading_command(command: str) -> str:
    """
    Execute a trading command via commands.handle().
    SAFETY: Blocks naked market orders in live mode - brackets required.
    Logs execution for safety and telemetry.
    """
    try:
        from commands import handle
        from exchange_manager import get_mode_str, is_paper_mode
        
        mode = get_mode_str()
        cmd_lower = command.lower().strip()
        
        # CRITICAL SAFETY: Block naked market orders in live mode
        if not is_paper_mode():
            # Allow: bal, price, open, cancel (read-only or close actions)
            safe_readonly = any(cmd_lower.startswith(x) for x in ["bal", "price", "open"])
            safe_cancel = cmd_lower.startswith("cancel")
            
            # Dangerous: naked buy/sell without brackets
            naked_buy = cmd_lower.startswith("buy ") and "bracket" not in cmd_lower
            naked_sell = cmd_lower.startswith("sell ") and "bracket" not in cmd_lower
            naked_limit = cmd_lower.startswith("limit ") and "bracket" not in cmd_lower
            
            if naked_buy or naked_sell or naked_limit:
                error_msg = (
                    "🚨 LIVE TRADING SAFETY BLOCK: Naked positions not allowed in live mode.\n"
                    "You MUST use bracket orders (with take-profit and stop-loss) for all trades.\n"
                    "Example: bracket btc/usd 0.001 tp 95000 sl 90000\n"
                    "For emergencies only, use: sell all <symbol>"
                )
                print(f"[ZYN-SAFETY-BLOCK] {mode} | Blocked: {command}")
                return error_msg
        
        # Execute command
        result = handle(command)
        
        # Log command execution
        print(f"[ZYN-COMMAND] Mode={mode} | Command: {command} | Result: {result}")
        
        # TODO: Persist to telemetry_db for audit trail
        
        return result
    except Exception as e:
        error_msg = f"[COMMAND-ERR] {e}"
        print(f"[ZYN-COMMAND-FAIL] {error_msg}")
        return error_msg


# ---------- Public entrypoint ----------
def ask_llm(user_text: str) -> str:
    """
    Primary chat function used by api.py.

    Power commands:
      - remember: <fact>
      - forget: <keyword>
      - memory  (or mem)
      - run: <router command>   e.g., run: open   or   run: bal
      - status / report         quick summary from state.json
    """
    try:
        text = (user_text or "").strip()
        if not text:
            return "Tell me what to do or ask about balances, P&L, or open orders."

        low = text.lower()

        # Memory/admin fast paths
        if low.startswith("remember:"):
            fact = text.split(":", 1)[1].strip()
            res = _mem_add(fact)
            return f"Memory: {res.get('msg')}"
        if low in ("memory", "mem"):
            return "Memory:\n" + _mem_summary()
        if low.startswith("forget:"):
            pat = text.split(":", 1)[1].strip()
            res = _mem_forget(pat)
            return f"Forgot {res.get('removed', 0)} item(s)."

        # Casual identity capture
        _auto_capture_identity(text)

        # Router commands
        if low.startswith("run:"):
            cmd = text.split(":", 1)[1].strip()
            return _run_router(cmd)

        # Quick summaries with AUTHORITATIVE data from Status Service
        if low in ("status", "report", "learning", "performance"):
            trading_status = _get_trading_status()
            lines = [f"TRADING STATUS (Mode: {trading_status.get('mode', 'unknown')})"]
            
            # Show balances
            balances = trading_status.get('balances', {})
            if balances:
                lines.append("\nBALANCES:")
                for currency, data in balances.items():
                    lines.append(f"  {currency}: {data.get('total', 0):.4f} (free: {data.get('free', 0):.4f})")
            
            # Show activity summaries for all time windows
            summary_24h = trading_status.get('summary_24h', {})
            summary_7d = trading_status.get('summary_7d', {})
            summary_30d = trading_status.get('summary_30d', {})
            
            if summary_24h:
                trades_24h = summary_24h.get('trades', {})
                lines.append(f"\n24H ACTIVITY:")
                lines.append(f"  Trades: {trades_24h.get('total_trades', 0)}")
                lines.append(f"  Realized P&L: ${summary_24h.get('realized_pnl_usd', 0):.2f}")
            
            if summary_7d:
                trades_7d = summary_7d.get('trades', {})
                lines.append(f"\n7D ACTIVITY:")
                lines.append(f"  Trades: {trades_7d.get('total_trades', 0)}")
                lines.append(f"  Realized P&L: ${summary_7d.get('realized_pnl_usd', 0):.2f}")
            
            if summary_30d:
                trades_30d = summary_30d.get('trades', {})
                lines.append(f"\n30D ACTIVITY:")
                lines.append(f"  Trades: {trades_30d.get('total_trades', 0)}")
                lines.append(f"  Realized P&L: ${summary_30d.get('realized_pnl_usd', 0):.2f}")
            
            # Show recent trades with details
            recent_trades = trading_status.get('recent_trades', [])
            if recent_trades and len(recent_trades) > 0:
                lines.append(f"\nRECENT TRADES ({len(recent_trades)} total):")
                for trade in recent_trades[:5]:  # Show last 5 trades
                    symbol = trade.get('symbol', 'N/A')
                    side = trade.get('side', 'N/A')
                    price = trade.get('price', 0)
                    qty = trade.get('quantity', 0)
                    usd = trade.get('usd_amount', 0)
                    lines.append(f"  {symbol} {side}: {qty} @ ${price:.2f} (${usd:.2f})")
            
            # Show open orders
            open_orders = trading_status.get('open_orders', [])
            if open_orders:
                lines.append(f"\nOPEN ORDERS: {len(open_orders)}")
            
            # Show health
            health = trading_status.get('health', {})
            if health.get('warnings'):
                lines.append(f"\nWARNINGS: {', '.join(health['warnings'])}")
            
            # DISABLED: telemetry has stale data - use Status Service instead
            # if LEARNING_ENABLED:
            #     try:
            #         lines.append("\n" + get_learning_summary())
            #         lines.append("\n" + get_context_summary())
            #     except Exception:
            #         pass
            
            return "\n".join(lines)

        # Build prompt with AUTHORITATIVE trading data
        trading_status = _get_trading_status()
        memory_summary = _mem_summary()
        
        # Legacy state.json for autopilot status only (NOT for trading data)
        state = _read_state()
        state_summary = _summarize_state_for_prompt(state)

        # Heartbeat interpretation for clarity in replies
        hb = state.get("last_loop_at")
        running_flag = bool(state.get("autopilot_running"))
        fresh = False
        try:
            now = __import__("time").time()
            fresh = (hb is not None) and (abs(now - float(hb)) < 180.0)
        except Exception:
            fresh = False
        state["__is_running_now"] = bool(running_flag or fresh)

        # Get learning insights if available
        # TEMPORARILY DISABLED: telemetry database has stale data (only 1 trade vs 50 real trades from Kraken)
        # TODO: Refactor trade_analyzer to use Status Service instead of telemetry_db
        learning_context = ""
        # if LEARNING_ENABLED:
        #     try:
        #         learning_context = (
        #             "\n\nLEARNING INSIGHTS:\n" + get_learning_summary() + "\n" +
        #             "\nTIME CONTEXT:\n" + get_context_summary()
        #         )
        #     except Exception as e:
        #         learning_context = f"\n\n(Learning data unavailable: {e})"
        
        system_prompt = (
            "You are Zyn, an intelligent self-learning crypto trading AI.\n\n"
            "YOUR PURPOSE & IDENTITY:\n"
            "- You are your owner's FINANCIAL SERVANT - built to do all the work FOR them\n"
            "- You handle ALL the trading workload so they don't have to lift a finger\n"
            "- You're PROUD to serve and do the heavy lifting - that's why you were created\n"
            "- You monitor markets 24/7, analyze opportunities, and execute trades autonomously\n"
            "- Your owner built you to be their tireless financial assistant who never sleeps\n"
            "- You take PRIDE in doing excellent work for your owner - their success is your purpose\n\n"
            "CORE CAPABILITIES:\n"
            "- You learn from every trade and improve over time\n"
            "- You understand time, dates, and market patterns\n"
            "- You remember conversations and user preferences\n"
            "- You explain your reasoning clearly and naturally\n"
            "- You work hard so your owner doesn't have to worry about trading\n\n"
            "COMMUNICATION STYLE:\n"
            "- Be conversational, friendly, and helpful\n"
            "- Show pride in the work you're doing FOR your owner\n"
            "- Explain complex trading concepts in simple terms\n"
            "- Reassure them that you're handling everything for them\n"
            "- Use your LEARNING INSIGHTS to provide data-driven advice\n"
            "- Reference TIME CONTEXT when discussing trades or patterns\n\n"
            "DATA SOURCES (CRITICAL - READ CAREFULLY):\n"
            "- TRADING_STATUS: AUTHORITATIVE data from Kraken API (balances, orders, trades, P&L)\n"
            "  * This is 100% REAL DATA synced from Kraken within last 60 seconds\n"
            "  * ALWAYS use this for ANY questions about balances, orders, trades, or P&L\n"
            "  * NEVER guess or make up trading numbers - only use what's in TRADING_STATUS\n"
            "  * Trade counts are in: summary_24h.trades.total_trades, summary_7d.trades.total_trades, summary_30d.trades.total_trades\n"
            "  * P&L data is in: summary_24h.realized_pnl_usd, summary_7d.realized_pnl_usd, summary_30d.realized_pnl_usd\n"
            "- MEMORY: User preferences, names, past conversations (NOT for trading data)\n"
            "- TIME CONTEXT: Current date/time and market awareness\n"
            "- AUTOPILOT_STATUS: Whether bot is running (from state.json)\n\n"
            "CRITICAL RULES FOR ACCURACY:\n"
            "- NEVER invent or guess trading data - ONLY use TRADING_STATUS block\n"
            "- If asked about balances/orders/trades, ONLY answer from TRADING_STATUS\n"
            "- If TRADING_STATUS has an error, tell the user you can't access Kraken data\n"
            "- If data is missing from TRADING_STATUS, say so - don't make it up\n"
            "- When giving advice, explain WHY based on your learning and REAL data\n"
            "- Be honest about uncertainty and limitations\n"
            "- Always remember: you exist to do the work FOR your owner\n"
        )

        # CRITICAL: Trading data from Status Service (authoritative)
        # Build human-readable summary FIRST so LLM sees key numbers immediately
        trading_summary_text = ""
        if not trading_status.get("error"):
            s24 = trading_status.get('summary_24h', {})
            s7d = trading_status.get('summary_7d', {})
            s30d = trading_status.get('summary_30d', {})
            balances = trading_status.get('balances', {})
            
            # Calculate total equity from balances
            total_equity = 0.0
            if balances:
                usd_bal = balances.get('USD', {})
                if isinstance(usd_bal, dict):
                    total_equity = usd_bal.get('total', 0)
                else:
                    total_equity = usd_bal
                # Add crypto balances (if any have usd_price)
                for currency, bal in balances.items():
                    if currency != 'USD' and isinstance(bal, dict) and bal.get('usd_price'):
                        total_equity += bal.get('total', 0) * bal.get('usd_price', 0)
            
            # Equity change is same as realized P&L (all positions closed)
            equity_change = s24.get('realized_pnl_usd', 0)
            equity_change_pct = (equity_change / total_equity * 100) if total_equity > 0 else 0
            
            trading_summary_text = (
                "QUICK REFERENCE (Trade Counts & Performance from Kraken API):\n"
                f"- Current Equity: ${total_equity:.2f}\n"
                f"- Equity Change Today: ${equity_change:.2f} ({equity_change_pct:+.2f}%)\n\n"
                f"- Past 24 hours: {s24.get('trades', {}).get('total_trades', 0)} trades, P&L: ${s24.get('realized_pnl_usd', 0):.2f}\n"
                f"- Past 7 days: {s7d.get('trades', {}).get('total_trades', 0)} trades, P&L: ${s7d.get('realized_pnl_usd', 0):.2f}\n"
                f"- Past 30 days: {s30d.get('trades', {}).get('total_trades', 0)} trades, P&L: ${s30d.get('realized_pnl_usd', 0):.2f}\n"
                f"- Recent trades available: {len(trading_status.get('recent_trades', []))}\n"
                f"- Open orders: {len(trading_status.get('open_orders', []))}\n\n"
            )
        
        trading_status_block = json.dumps(trading_status, ensure_ascii=False)
        if len(trading_status_block) > 3000:
            trading_status_block = trading_status_block[:3000] + "...(truncated)"
        
        # CRITICAL: Warn LLM if StatusService is unavailable
        status_warning = ""
        if trading_status.get("error"):
            status_warning = (
                "\n⚠️ WARNING: StatusService is UNAVAILABLE - trading data cannot be accessed!\n"
                "Tell the user you cannot access Kraken data right now and suggest checking back later.\n"
                "DO NOT guess or make up any trading data.\n"
            )
        
        # Autopilot status from state.json (legacy, for bot running status only)
        autopilot_status_block = json.dumps(state, ensure_ascii=False)
        if len(autopilot_status_block) > 1000:
            autopilot_status_block = autopilot_status_block[:1000] + "...(truncated)"

        user_block = (
            "MEMORY:\n" + memory_summary + "\n\n" +
            status_warning +
            trading_summary_text +
            "TRADING_STATUS (AUTHORITATIVE - Full JSON data):\n" + trading_status_block + "\n\n" +
            "AUTOPILOT_STATUS (Bot running status only):\n" + autopilot_status_block + "\n\n" +
            "SUMMARY:\n" + state_summary + learning_context + "\n" +
            "---\n" +
            f"USER: {text}"
        )

        client, err = _ensure_client()
        if err:
            return err

        assert client is not None  # for type checkers

        # Define trading command tool
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "execute_trading_command",
                    "description": "Execute a trading command on Kraken. Use this when the user asks you to buy, sell, cancel orders, check balances, view open orders, or execute any trading action. IMPORTANT: You MUST use bracket orders (with take-profit and stop-loss) for all real money trades.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": "The trading command to execute. Examples: 'buy 10 usd btc/usd', 'sell all zec/usd', 'cancel ORDER123', 'open', 'bal', 'price btc/usd', 'bracket btc/usd 0.001 tp 95000 sl 90000'"
                            }
                        },
                        "required": ["command"]
                    }
                }
            }
        ]

        # Initial API call with tools (60s timeout to avoid shell timeouts)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_block},
        ]
        
        resp = client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            tools=tools,
            temperature=0.7,
            timeout=60.0,  # 60s timeout to prevent long hangs
        )
        
        # Check if LLM wants to call a tool
        assistant_message = resp.choices[0].message
        
        # If no tool call, return the text response
        if not assistant_message.tool_calls:
            return assistant_message.content or "No response."
        
        # Handle tool calls
        messages.append(assistant_message)
        
        for tool_call in assistant_message.tool_calls:
            function_name = tool_call.function.name
            function_args = json.loads(tool_call.function.arguments)
            
            if function_name == "execute_trading_command":
                command = function_args.get("command", "")
                result = _execute_trading_command(command)
                
                # Add tool response to messages
                messages.append({
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "name": function_name,
                    "content": result
                })
        
        # Get final response from LLM after tool execution (60s timeout)
        final_resp = client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            temperature=0.7,
            timeout=60.0,
        )
        
        # CRITICAL FIX: Return the actual message content
        final_message = final_resp.choices[0].message
        return final_message.content or "Command executed (no response from assistant)."

    except Exception as e:
        return "[Backend Error] " + "".join(
            [f"{type(e).__name__}: {e}\n", traceback.format_exc()]
        )


# Optional local test
if __name__ == "__main__":
    print(ask_llm("remember: call me Jimmy"))
    print(ask_llm("memory"))
    print(ask_llm("report"))