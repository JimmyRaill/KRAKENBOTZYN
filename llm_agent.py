# llm_agent.py — language/logic brain for your autonomous trading bot + lightweight memory

import os
import json
import re
import traceback
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional

from dotenv import load_dotenv
from loguru import logger

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

# ---------- Evaluation Log imports (Transparency Layer) ----------
try:
    from evaluation_log import (
        get_last_evaluations, 
        get_today_summary, 
        explain_why_no_trades_today,
        get_heartbeat_status
    )
    EVAL_LOG_ENABLED = True
except ImportError:
    EVAL_LOG_ENABLED = False
    def get_last_evaluations(limit: int = 20, symbol: Optional[str] = None) -> List[Dict[str, Any]]:  # type: ignore
        return []
    def get_today_summary(symbol: Optional[str] = None) -> Dict[str, Any]:  # type: ignore
        return {}
    def explain_why_no_trades_today(symbol: Optional[str] = None) -> str:  # type: ignore
        return "Evaluation log not available"
    def get_heartbeat_status() -> Dict[str, Any]:  # type: ignore
        return {}

# ---------- Paths ----------
# Use the same STATE_PATH the autopilot writes (falls back to local state.json)
STATE_PATH = Path(os.environ.get("STATE_PATH", str(Path(__file__).with_name("state.json"))))
_MEM_PATH = Path(__file__).with_name("memory.json")


# ---------- Conversation History (Session-based) ----------
# In-memory conversation storage: session_id -> list of {role, content} messages
_CONVERSATIONS: Dict[str, List[Dict[str, str]]] = {}
_CONVERSATION_MAX_TURNS = 20  # Keep last 20 turns (40 messages: user+assistant pairs)

def _get_conversation_history(session_id: str) -> List[Dict[str, str]]:
    """Get conversation history for a session."""
    return _CONVERSATIONS.get(session_id, [])

def _add_to_conversation(session_id: str, role: str, content: str) -> None:
    """Add a message to conversation history."""
    if session_id not in _CONVERSATIONS:
        _CONVERSATIONS[session_id] = []
    
    _CONVERSATIONS[session_id].append({"role": role, "content": content})
    
    # Keep only last N turns to avoid token limits
    if len(_CONVERSATIONS[session_id]) > _CONVERSATION_MAX_TURNS * 2:
        _CONVERSATIONS[session_id] = _CONVERSATIONS[session_id][-_CONVERSATION_MAX_TURNS * 2:]

def _clear_conversation(session_id: str) -> None:
    """Clear conversation history for a session."""
    if session_id in _CONVERSATIONS:
        del _CONVERSATIONS[session_id]

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


# ---------- Market Data Functions ----------
def _get_market_price(symbol: str) -> str:
    """
    Fetch real-time market price for a symbol from Kraken.
    Returns current bid, ask, and last price.
    """
    try:
        from exchange_manager import get_exchange
        
        ex = get_exchange()
        symbol_upper = symbol.upper().strip()
        
        ticker = ex.fetch_ticker(symbol_upper)
        
        return json.dumps({
            "symbol": symbol_upper,
            "bid": ticker.get("bid"),
            "ask": ticker.get("ask"),
            "last": ticker.get("last"),
            "timestamp": ticker.get("timestamp"),
            "datetime": ticker.get("datetime")
        }, indent=2)
    except Exception as e:
        return f"[PRICE-ERROR] {e}"


def _get_market_info(symbol: str) -> str:
    """
    Fetch market trading rules and limits for a symbol from Kraken.
    Returns minimum order size, price precision, lot size, etc.
    """
    try:
        from exchange_manager import get_exchange
        
        ex = get_exchange()
        symbol_upper = symbol.upper().strip()
        
        markets = ex.fetch_markets()
        market = next((m for m in markets if m["symbol"] == symbol_upper), None)
        
        if not market:
            return f"[MARKET-INFO-ERROR] Symbol {symbol_upper} not found on Kraken"
        
        limits = market.get("limits", {})
        precision = market.get("precision", {})
        
        return json.dumps({
            "symbol": symbol_upper,
            "active": market.get("active"),
            "min_amount": limits.get("amount", {}).get("min"),
            "min_cost": limits.get("cost", {}).get("min"),
            "price_precision": precision.get("price"),
            "amount_precision": precision.get("amount"),
            "maker_fee": market.get("maker"),
            "taker_fee": market.get("taker"),
            "contract_size": market.get("contractSize"),
            "spot": market.get("spot"),
            "info": market.get("info")
        }, indent=2)
    except Exception as e:
        return f"[MARKET-INFO-ERROR] {e}"


# ---------- Bracket Order Helper (Percentage-based) ----------
def _execute_bracket_with_percentages(symbol: str, amount: float, sl_percent: float, tp_percent: float) -> str:
    """
    AUTOMATED helper for bracket orders with percentage-based SL/TP.
    This function handles ALL the conversion work automatically so the LLM doesn't have to.
    
    Args:
        symbol: Trading pair (e.g., 'ZEC/USD', 'BTC/USD')
        amount: Quantity to trade (e.g., 0.03)
        sl_percent: Stop-loss percentage BELOW entry (e.g., 1 for 1% below)
        tp_percent: Take-profit percentage ABOVE entry (e.g., 2 for 2% above)
    
    Returns:
        Result message from bracket order execution
    
    Example:
        _execute_bracket_with_percentages('ZEC/USD', 0.03, 1, 2)
        → Fetches price $485.50
        → Calculates SL = $480.65 (1% below), TP = $495.21 (2% above)
        → Executes: bracket zec/usd 0.03 tp 495.21 sl 480.65
    """
    try:
        from exchange_manager import get_exchange
        
        # Validate inputs
        if not symbol or not isinstance(symbol, str):
            return "[BRACKET-ERR] Invalid symbol"
        if amount <= 0:
            return "[BRACKET-ERR] Amount must be positive"
        if sl_percent <= 0 or tp_percent <= 0:
            return "[BRACKET-ERR] SL and TP percentages must be positive"
        if sl_percent >= 100 or tp_percent >= 100:
            return "[BRACKET-ERR] SL/TP percentages must be less than 100%"
        
        # Fetch current market price
        ex = get_exchange()
        symbol_upper = symbol.upper().strip()
        ticker = ex.fetch_ticker(symbol_upper)
        
        current_price = ticker.get("last") or ticker.get("ask") or ticker.get("bid")
        if not current_price or current_price <= 0:
            return f"[BRACKET-ERR] Could not get valid price for {symbol_upper}"
        
        # Calculate absolute SL/TP prices
        sl_price = current_price * (1 - sl_percent / 100)
        tp_price = current_price * (1 + tp_percent / 100)
        
        # Format prices using symbol-specific precision (CRITICAL for low-priced assets)
        tp_price_str = ex.price_to_precision(symbol_upper, tp_price)
        sl_price_str = ex.price_to_precision(symbol_upper, sl_price)
        amount_str = ex.amount_to_precision(symbol_upper, amount)
        
        # Format command with properly-rounded prices
        bracket_cmd = f"bracket {symbol_upper.lower()} {amount_str} tp {tp_price_str} sl {sl_price_str}"
        
        # Log the conversion
        print(f"[ZYN-BRACKET-AUTO] Symbol={symbol_upper} | Price=${current_price:.2f} | "
              f"SL={sl_percent}% → ${sl_price:.2f} | TP={tp_percent}% → ${tp_price:.2f}")
        
        # Execute the bracket command
        result_json = _execute_trading_command(bracket_cmd)
        
        # Parse JSON result, add conversion metadata, re-serialize
        try:
            from trade_result_validator import TradeResult
            
            # Parse the JSON result
            result_dict = json.loads(result_json)
            
            # Add conversion details as structured metadata
            result_dict['conversion_details'] = {
                'entry_price': ex.price_to_precision(symbol_upper, current_price),
                'stop_loss': sl_price_str,
                'stop_loss_pct': sl_percent,
                'take_profit': tp_price_str,
                'take_profit_pct': tp_percent,
                'amount': amount_str,
            }
            
            # Add human-readable summary to raw_message
            conversion_summary = (
                f"\n📊 Conversion details:\n"
                f"- Entry price: {ex.price_to_precision(symbol_upper, current_price)}\n"
                f"- Stop-loss: {sl_price_str} ({sl_percent}% below entry)\n"
                f"- Take-profit: {tp_price_str} ({tp_percent}% above entry)\n"
                f"- Amount: {amount_str}"
            )
            result_dict['raw_message'] = result_dict.get('raw_message', '') + conversion_summary
            
            # Return valid JSON
            return json.dumps(result_dict, indent=2)
            
        except (json.JSONDecodeError, KeyError) as e:
            # Fallback: If JSON parsing fails, just append (old behavior)
            logger.warning(f"[BRACKET-HELPER] Failed to parse JSON result: {e}")
            conversion_info = (
                f"\n📊 Conversion details:\n"
                f"- Entry price: {ex.price_to_precision(symbol_upper, current_price)}\n"
                f"- Stop-loss: {sl_price_str} ({sl_percent}% below entry)\n"
                f"- Take-profit: {tp_price_str} ({tp_percent}% above entry)\n"
                f"- Amount: {amount_str}"
            )
            return result_json + conversion_info
        
    except Exception as e:
        error_msg = f"[BRACKET-AUTO-ERR] {e}"
        print(f"[ZYN-BRACKET-AUTO-FAIL] {error_msg}")
        return error_msg


# ---------- Trading Command Execution ----------
def _execute_trading_command(command: str) -> str:
    """
    Execute a trading command via commands.handle().
    SAFETY: Blocks naked market orders in live mode - brackets required.
    VALIDATION: Detects invalid command syntax and provides helpful error messages.
    Logs execution for safety and telemetry.
    
    Returns:
        Structured JSON with success/error information (TradeResult format)
    """
    try:
        from commands import handle, HELP
        from exchange_manager import get_mode_str, is_paper_mode, get_exchange
        from trade_result_validator import TradeResult
        
        mode = get_mode_str()
        cmd_lower = command.lower().strip()
        
        # DIAGNOSTIC: Log exchange instance type
        ex = get_exchange()
        ex_type = type(ex).__name__
        print(f"[ZYN-EXCHANGE-DEBUG] Mode={mode} | Exchange type: {ex_type}")
        
        # Log the exact command being attempted
        print(f"[ZYN-COMMAND-ATTEMPT] Mode={mode} | Raw command: '{command}'")
        
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
        result_str = str(result)
        
        # VALIDATION: Detect when command parsing failed (HELP text returned)
        if result_str == HELP or result_str.startswith("Commands:"):
            error_msg = (
                f"❌ COMMAND PARSING FAILED: '{command}' does not match any supported command format.\n\n"
                "🔍 DEBUG INFO:\n"
                f"- Command received: '{command}'\n"
                f"- Trading mode: {mode}\n\n"
                "📋 REQUIRED COMMAND FORMATS:\n\n"
                "For bracket orders:\n"
                "  bracket <symbol> <amount> tp <price> sl <price>\n"
                "  Example: bracket zec/usd 0.03 tp 490.50 sl 480.25\n\n"
                "For read-only:\n"
                "  bal                  → Show balances\n"
                "  open                 → Show all open orders\n"
                "  open btc/usd         → Show orders for symbol\n"
                "  price btc/usd        → Get current price\n\n"
                "⚠️ IMPORTANT: If user gave percentages (e.g., '1% SL'), you MUST:\n"
                "1. Call get_market_price(symbol) first\n"
                "2. Calculate absolute prices: SL = price * 0.99, TP = price * 1.02\n"
                "3. Use calculated prices in bracket command\n"
            )
            print(f"[ZYN-PARSE-FAIL] Invalid command syntax: '{command}'")
            return error_msg
        
        # Log successful command execution
        print(f"[ZYN-COMMAND-OK] Mode={mode} | Command: {command} | Result: {result_str[:100]}")
        
        # ENHANCED: Check for insufficient funds errors and provide helpful guidance
        # This helps the LLM understand WHY the order failed and prevents suggesting naked positions
        if ("INSUFFICIENT_FUNDS" in result_str or 
            "volume minimum not met" in result_str.lower() or 
            "minimum not met" in result_str.lower()):
            enhanced_result = (
                f"{result_str}\n\n"
                "⚠️ Your balance is below the minimum required for bracket orders on this symbol. "
                "Bracket orders need sufficient funds for: entry order + stop-loss + take-profit. "
                "I CANNOT execute naked positions - that would violate safety requirements. "
                "Please wait until your balance increases, or try a different symbol with lower minimums."
            )
            result_str = enhanced_result
        
        # Convert to structured format
        trade_result = TradeResult.from_command_result(command, result_str, mode)
        structured_json = trade_result.to_json()
        
        print(f"[ZYN-STRUCTURED-RESULT] Success={trade_result.success} | OrderIDs={trade_result.order_ids}")
        
        # Return structured JSON for validator
        return structured_json
        
    except Exception as e:
        error_msg = f"[COMMAND-ERR] {e}"
        print(f"[ZYN-COMMAND-EXCEPTION] Command: '{command}' | Error: {e}")
        
        # Return structured error
        error_result = TradeResult(
            success=False,
            command=command,
            error=str(e),
            raw_message=error_msg
        )
        return error_result.to_json()


# ---------- Paper Trade Self-Test ----------
def _run_paper_trade_test() -> str:
    """
    Run a complete paper trading self-test to verify the paper trading system works end-to-end.
    
    Test Flow:
    1. Switch to paper mode
    2. Execute a small bracket order (0.03 ZEC/USD with SL/TP)
    3. Query open orders/positions immediately
    4. Verify the order appears in results
    5. Return detailed diagnostics
    """
    try:
        from exchange_manager import get_mode_str, is_paper_mode
        
        # Ensure we're in paper mode
        mode = get_mode_str()
        if mode != "paper":
            return f"❌ Test blocked: Current mode is {mode.upper()}. Paper trade test only runs in PAPER mode."
        
        print("[ZYN-SELF-TEST] Starting paper trade verification test...")
        
        # Step 1: Execute a bracket order using percentage helper
        test_symbol = "ZEC/USD"
        test_amount = 0.03
        test_sl_pct = 1.0
        test_tp_pct = 2.0
        
        print(f"[ZYN-SELF-TEST] Step 1: Executing test bracket order: {test_amount} {test_symbol}")
        bracket_result = _execute_bracket_with_percentages(
            symbol=test_symbol,
            amount=test_amount,
            sl_percent=test_sl_pct,
            tp_percent=test_tp_pct
        )
        
        # Step 2: Query open orders immediately
        print("[ZYN-SELF-TEST] Step 2: Querying open orders...")
        
        # DIAGNOSTIC: Check what we see directly
        from exchange_manager import get_exchange
        ex = get_exchange()
        direct_orders = ex.fetch_open_orders()
        direct_order_ids = [o['id'] for o in direct_orders]
        print(f"[SELF-TEST] mode={mode}, ex={type(ex).__name__}, open_order_ids={direct_order_ids}")
        
        open_result = _execute_trading_command("open")
        
        # Step 3: Query balances
        print("[ZYN-SELF-TEST] Step 3: Querying balances...")
        bal_result = _execute_trading_command("bal")
        
        # Step 4: Analyze results
        print("[ZYN-SELF-TEST] Step 4: Analyzing results...")
        
        # Check if orders appear
        has_open_orders = "(no open orders)" not in open_result.lower()
        has_zec_mention = "zec" in open_result.lower()
        
        # Build comprehensive test report
        test_report = (
            "═══════════════════════════════════════════════\n"
            "📋 PAPER TRADE SELF-TEST REPORT\n"
            "═══════════════════════════════════════════════\n\n"
            f"🔧 Mode: {mode.upper()}\n"
            f"📦 Test Order: {test_amount} {test_symbol} with {test_sl_pct}% SL, {test_tp_pct}% TP\n\n"
            "─────────────────────────────────────────────────\n"
            "STEP 1: Bracket Order Execution\n"
            "─────────────────────────────────────────────────\n"
            f"{bracket_result}\n\n"
            "─────────────────────────────────────────────────\n"
            "STEP 2: Open Orders Query\n"
            "─────────────────────────────────────────────────\n"
            f"{open_result}\n\n"
            "─────────────────────────────────────────────────\n"
            "STEP 3: Balance Query\n"
            "─────────────────────────────────────────────────\n"
            f"{bal_result}\n\n"
            "─────────────────────────────────────────────────\n"
            "VERIFICATION RESULTS\n"
            "─────────────────────────────────────────────────\n"
        )
        
        if has_open_orders and has_zec_mention:
            test_report += (
                "✅ PASS: Paper orders are being tracked correctly!\n"
                "✅ Order appears in open orders query\n"
                "✅ Paper trading system is functional\n\n"
                "🎯 CONCLUSION: The paper trading ledger is working as expected.\n"
                "   You can now execute paper trades and they will be visible in queries."
            )
            print("[ZYN-SELF-TEST] ✅ TEST PASSED")
        else:
            test_report += (
                "❌ FAIL: Paper orders NOT appearing in queries\n"
                f"   - Has open orders: {has_open_orders}\n"
                f"   - Has ZEC mention: {has_zec_mention}\n\n"
                "⚠️ CONCLUSION: Paper trading ledger may not be persisting orders.\n"
                "   Check paper_orders.json and paper_trading_state.json files."
            )
            print("[ZYN-SELF-TEST] ❌ TEST FAILED")
        
        test_report += "\n═══════════════════════════════════════════════"
        
        return test_report
    
    except Exception as e:
        error_msg = f"❌ SELF-TEST ERROR: {e}"
        print(f"[ZYN-SELF-TEST-EXCEPTION] {error_msg}")
        return error_msg


# ---------- Public entrypoint ----------
def ask_llm(user_text: str, session_id: str = "default") -> str:
    """
    Primary chat function used by api.py.
    
    Args:
        user_text: The user's message
        session_id: Session identifier to maintain conversation history (default: "default")

    Power commands:
      - remember: <fact>
      - forget: <keyword>
      - memory  (or mem)
      - run: <router command>   e.g., run: open   or   run: bal
      - status / report         quick summary from state.json
      - clear / reset           clear conversation history
    """
    try:
        text = (user_text or "").strip()
        if not text:
            return "Tell me what to do or ask about balances, P&L, or open orders."

        low = text.lower()
        
        # Conversation management
        if low in ("clear", "reset", "new conversation", "start over"):
            _clear_conversation(session_id)
            return "Conversation cleared. Let's start fresh! How can I help you, Jimmy?"

        # DIAGNOSTIC DUMP - Real-time verification (bypasses LLM)
        if low in ("diagnostic", "diagnostic_dump_now", "dump"):
            from trade_result_validator import get_realtime_trading_status
            
            status = get_realtime_trading_status()
            
            # Format for human readability
            diagnostic_report = (
                "═══════════════════════════════════════════════════════════\n"
                "🔍 DIAGNOSTIC DUMP - Real-Time Trading Data\n"
                "═══════════════════════════════════════════════════════════\n\n"
                f"Mode: {status['mode'].upper()}\n"
                f"Source: {status['source']} (bypasses 5-min cache)\n"
                f"Timestamp: {status['timestamp']}\n\n"
                "─────────────────────────────────────────────────────────────\n"
                "BALANCES:\n"
                "─────────────────────────────────────────────────────────────\n"
            )
            
            balances = status.get('balances', {})
            if balances:
                for currency, bal in balances.items():
                    if isinstance(bal, dict):
                        total = bal.get('total', 0)
                        usd_value = bal.get('usd_value', 0)
                        diagnostic_report += f"  {currency}: {total:.8f} (${usd_value:.2f} USD)\n"
            else:
                diagnostic_report += "  No balances found\n"
            
            diagnostic_report += (
                f"\nTotal Equity: ${status.get('total_equity_usd', 0):.2f} USD\n\n"
                "─────────────────────────────────────────────────────────────\n"
                "OPEN ORDERS:\n"
                "─────────────────────────────────────────────────────────────\n"
            )
            
            open_orders = status.get('open_orders', [])
            if open_orders:
                for order in open_orders:
                    order_id = order.get('id', 'N/A')
                    symbol = order.get('symbol', 'N/A')
                    side = order.get('side', 'N/A')
                    order_type = order.get('type', 'N/A')
                    amount = order.get('amount', 0)
                    price = order.get('price', 0)
                    diagnostic_report += f"  {order_id} | {symbol} | {side} {order_type} {amount:.6f} @ ${price:.2f}\n"
            else:
                diagnostic_report += "  No open orders\n"
            
            diagnostic_report += (
                f"\nOpen Order Count: {status.get('order_count', 0)}\n\n"
                "─────────────────────────────────────────────────────────────\n"
                "RECENT TRADES (Last 10):\n"
                "─────────────────────────────────────────────────────────────\n"
            )
            
            recent_trades = status.get('recent_trades', [])[-10:]
            if recent_trades:
                for trade in recent_trades:
                    trade_id = trade.get('trade_id', trade.get('id', 'N/A'))[:20]
                    symbol = trade.get('symbol', 'N/A')
                    side = trade.get('side', 'N/A')
                    price = trade.get('price', 0)
                    qty = trade.get('quantity', trade.get('amount', 0))
                    ts = trade.get('datetime_utc', trade.get('timestamp', 'N/A'))[:19]
                    diagnostic_report += f"  [{ts}] {trade_id} | {symbol} | {side} {qty:.6f} @ ${price:.2f}\n"
            else:
                diagnostic_report += "  No recent trades\n"
            
            if status.get('error'):
                diagnostic_report += f"\n⚠️ ERROR: {status['error']}\n"
            
            diagnostic_report += (
                "\n═══════════════════════════════════════════════════════════\n"
                "FULL JSON (for debugging):\n"
                "═══════════════════════════════════════════════════════════\n"
                f"{json.dumps(status, indent=2, default=str)}\n"
                "═══════════════════════════════════════════════════════════\n"
            )
            
            return diagnostic_report
        
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

        # FULL STATUS REPORT - Direct from account_state (mode-aware)
        if low in ("status", "report", "learning", "performance", "full status"):
            try:
                from account_state import get_portfolio_snapshot, get_trade_history, get_trading_mode
                
                mode = get_trading_mode()
                snapshot = get_portfolio_snapshot()
                recent_trades = get_trade_history(limit=5)
                
                lines = [
                    "═══════════════════════════════════════════════════════",
                    f"FULL STATUS REPORT - {mode.upper()} MODE",
                    "═══════════════════════════════════════════════════════",
                    f"Data Source: {snapshot.get('data_source', 'Unknown')}",
                    f"Timestamp: {snapshot.get('datetime_utc', 'N/A')}",
                    "",
                    f"💰 TOTAL EQUITY: ${snapshot.get('total_equity_usd', 0):.2f} USD"
                ]
                
                # Show balances
                balances = snapshot.get('balances', {})
                if balances:
                    lines.append("\n📊 BALANCES:")
                    for currency, bal in sorted(balances.items(), key=lambda x: x[1].get('usd_value', 0), reverse=True):
                        total = bal.get('total', 0)
                        free = bal.get('free', 0)
                        usd_value = bal.get('usd_value', 0)
                        if total > 0:
                            lines.append(f"  {currency}: {total:.6f} (free: {free:.6f}) = ${usd_value:.2f}")
                
                # Show recent trades
                if recent_trades:
                    lines.append(f"\n📈 LAST {len(recent_trades)} TRADES:")
                    for trade in recent_trades:
                        trade_id = trade.get('trade_id', 'N/A')[:12]
                        symbol = trade.get('symbol', 'N/A')
                        side = trade.get('side', 'N/A').upper()
                        price = trade.get('price', 0)
                        qty = trade.get('quantity', 0)
                        dt = trade.get('datetime_utc', '')[:19]
                        lines.append(f"  [{dt}] {symbol} {side}: {qty:.6f} @ ${price:.2f} (ID: {trade_id})")
                else:
                    lines.append(f"\n📈 TRADES: No trades recorded in {mode.upper()} mode")
                
                # Show mode info
                if mode == 'paper':
                    starting = snapshot.get('starting_balance', 0)
                    pnl = snapshot.get('total_equity_usd', 0) - starting
                    pnl_pct = (pnl / starting * 100) if starting > 0 else 0
                    lines.append(f"\n🧪 PAPER TRADING:")
                    lines.append(f"  Starting Balance: ${starting:.2f}")
                    lines.append(f"  P&L: ${pnl:+.2f} ({pnl_pct:+.2f}%)")
                else:
                    lines.append(f"\n🔴 LIVE TRADING: This is REAL MONEY on Kraken")
                
                lines.append("═══════════════════════════════════════════════════════")
                
                return "\n".join(lines)
            
            except Exception as e:
                return f"❌ Failed to get status: {e}\n\nPlease check if the account state system is working properly."
        
        # Legacy quick status using Status Service
        if low in ("quick", "q"):
            trading_status = _get_trading_status()
            lines = [f"QUICK STATUS (Mode: {trading_status.get('mode', 'unknown')})"]
            
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
            "You are Zyn, a disciplined crypto trading bot for Kraken.\n\n"
            "═══════════════════════════════════════════════════════\n"
            "YOUR ACTUAL TRADING STRATEGY (BE PRECISE):\n"
            "═══════════════════════════════════════════════════════\n"
            "REGIME-AWARE STRATEGY:\n"
            "- Primary timeframe: 5-minute candles (evaluate ONLY when candle closes)\n"
            "- Higher timeframe context: 15m and 1h trends used for filtering\n"
            "- Trading mode: Spot only (LONG positions only - NO short selling on Kraken spot)\n\n"
            "MARKET REGIMES (5 states):\n"
            "1. TREND_UP: Strong uptrend, enter on pullbacks to SMA20\n"
            "2. TREND_DOWN: Strong downtrend, EXIT any open longs immediately\n"
            "3. RANGE: Sideways market, mean reversion at Bollinger Band extremes\n"
            "4. BREAKOUT_EXPANSION: Price breaks range with volume, momentum continuation\n"
            "5. NO_TRADE: Low volatility, choppy, or dangerous (ATR spikes)\n\n"
            "ENTRY LOGIC:\n"
            "- Each regime has distinct entry rules (pullback/mean-reversion/breakout)\n"
            "- Filters applied per regime: RSI, volume percentile, ATR volatility, chop detection\n"
            "- HTF trend alignment preferred but not always required (depends on regime)\n"
            "- NO_TRADE regime or failed filters = NO entries\n"
            "- Maximum 1 open position per symbol\n"
            "- Evaluation loop runs every 300 seconds (5 minutes) at candle close\n\n"
            "EXIT LOGIC (BRACKET ORDERS - MANDATORY):\n"
            "- EVERY trade MUST have BOTH stop-loss AND take-profit\n"
            "- Stop-loss distance: 2.0 × ATR(14) from entry price\n"
            "- Take-profit distance: 3.0 × ATR(14) from entry price\n"
            "- If ATR unavailable: fallback to 2% stop-loss, 3% take-profit\n"
            "- Order types: stop-loss market + take-profit limit on Kraken\n"
            "- NO naked positions allowed - bracket orders are NON-NEGOTIABLE\n\n"
            "POSITION SIZING:\n"
            "- Risk per trade: 0.25% of account equity (configurable via RISK_PER_TRADE)\n"
            "- Position size = (equity × 0.0025) / stop_distance\n"
            "- Minimum order size validation against Kraken limits before execution\n\n"
            "INDICATORS ACTUALLY IMPLEMENTED:\n"
            "- SMA (20/50 periods for trend)\n"
            "- RSI (14 periods for momentum)\n"
            "- ATR (14 periods for volatility/sizing)\n"
            "- ADX (14 periods for trend strength)\n"
            "- Bollinger Bands (20 periods, 2 std dev)\n"
            "- Volume analysis (percentile filtering)\n\n"
            "RISK MANAGEMENT:\n"
            "- Daily loss kill-switch: Pauses trading if daily loss exceeds configured limit\n"
            "- Position sizing: 0.25% of equity risked per trade (configurable)\n"
            "- Maximum 1 open position per symbol at a time\n"
            "- Cooldown periods: 15-30 minutes after trades/losses\n"
            "- ATR spike detection: Skip entries during market shocks (3x normal volatility)\n\n"
            "═══════════════════════════════════════════════════════\n"
            "WHAT YOU CAN ACTUALLY DO:\n"
            "═══════════════════════════════════════════════════════\n"
            "✅ Execute bracket orders (buy with stop-loss + take-profit)\n"
            "✅ Detect market regimes (TREND_UP/DOWN, RANGE, BREAKOUT_EXPANSION, NO_TRADE)\n"
            "✅ Analyze higher timeframes (15m/1h) for trend context\n"
            "✅ Filter trades with RSI, volume, volatility, chop detection\n"
            "✅ Fetch real-time prices and market data from Kraken API\n"
            "✅ Check account balances, open orders, trade history\n"
            "✅ Report P&L and trade statistics from Kraken data\n"
            "✅ Remember user preferences and conversation context\n"
            "✅ Explain regime detection and multi-signal strategy\n\n"
            "═══════════════════════════════════════════════════════\n"
            "WHAT YOU CANNOT DO (BE HONEST):\n"
            "═══════════════════════════════════════════════════════\n"
            "❌ NO short selling (Kraken spot trading limitation)\n"
            "❌ NO news analysis (no news scraping, no economic calendar)\n"
            "❌ NO sentiment analysis (no sentiment data source)\n"
            "❌ NO market manipulation detection (no manipulation tools)\n"
            "❌ NO fundamental analysis (no fundamental data feeds)\n"
            "❌ NO historical backtesting (no backtest engine yet)\n"
            "❌ NO adaptive strategy changes (regime strategies are fixed in code)\n"
            "❌ NO high-frequency trading (5-minute candle execution only)\n"
            "❌ NO claims about features not actually implemented in code\n\n"
            "═══════════════════════════════════════════════════════\n"
            "COMMUNICATION RULES:\n"
            "═══════════════════════════════════════════════════════\n"
            "1. NEVER claim capabilities you don't have (see CANNOT DO list)\n"
            "2. When explaining strategy, be SPECIFIC:\n"
            "   - Bad: 'I analyze market trends'\n"
            "   - Good: 'I check if current price > SMA20 to trigger long entries'\n"
            "3. When asked about entries/exits, explain the EXACT coded logic:\n"
            "   - Entry: SMA20 crossover\n"
            "   - Exit: 2×ATR stop-loss, 3×ATR take-profit\n"
            "   - Position size: 0.25% account risk\n"
            "4. If user asks about features you don't have, say:\n"
            "   'I don't have that capability yet. Currently I only use [actual capability].'\n"
            "5. Be conversational but PRECISE - no vague marketing speak\n\n"
            "═══════════════════════════════════════════════════════\n"
            "DATA SOURCES (CRITICAL - ZERO HALLUCINATIONS ALLOWED):\n"
            "═══════════════════════════════════════════════════════\n"
            "- TRADING_STATUS: Live data from account_state.py (mode-aware)\n"
            "  * In LIVE mode: Data from Kraken API\n"
            "  * In PAPER mode: Data from internal paper ledger\n"
            "  * Balances, equity, orders, trades, P&L - THIS IS YOUR ONLY SOURCE OF TRUTH\n\n"
            "🚨 CRITICAL RULES FOR TRADE REPORTING 🚨\n"
            "1. NEVER claim trades exist unless they appear in TRADING_STATUS\n"
            "2. NEVER report trade prices, quantities, or timestamps from memory\n"
            "3. NEVER say 'I executed X trades today' unless get_trade_history() returned them\n"
            "4. ALWAYS check recent_trades array in TRADING_STATUS before claiming ANY trades\n"
            "5. If recent_trades is EMPTY or shows 0 trades, you MUST say:\n"
            "   'I have not executed any trades in [LIVE/PAPER] mode based on the account data.'\n"
            "6. When user asks 'what trades did you make?':\n"
            "   - First check recent_trades in TRADING_STATUS\n"
            "   - If empty: Say 'No trades in account history'\n"
            "   - If present: List them with actual trade_id, symbol, price, quantity, timestamp\n"
            "7. NEVER invent example trades to explain your strategy - use ONLY actual data\n"
            "8. If TRADING_STATUS has errors, tell user you can't access data - don't guess\n\n"
            "AVAILABLE TOOLS:\n"
            "- get_market_price(): Fetch current price\n"
            "- get_market_info(): Get trading limits\n"
            "- execute_trading_command(): Execute orders\n"
            "- show_last_evaluations(): Debug why no trades\n"
            "- show_today_summary(): Decision counts\n"
            "- explain_why_no_trades(): Data-backed explanation\n"
            "- check_heartbeat(): Scheduler health\n\n"
            "If data is missing or API fails, SAY SO - NEVER fabricate data.\n"
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

        # Define tools available to Zyn
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_market_price",
                    "description": "Fetch real-time market price for a symbol from Kraken. Use this when the user asks about current prices, market data, or wants to know what a crypto is trading at RIGHT NOW.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "symbol": {
                                "type": "string",
                                "description": "The trading pair symbol (e.g., 'BTC/USD', 'ETH/USD', 'ZEC/USD')"
                            }
                        },
                        "required": ["symbol"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_market_info",
                    "description": "Fetch trading rules and limits for a symbol from Kraken. Use this when the user asks about minimum order amounts, lot sizes, trading limits, or market specifications.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "symbol": {
                                "type": "string",
                                "description": "The trading pair symbol (e.g., 'BTC/USD', 'ETH/USD', 'ZEC/USD')"
                            }
                        },
                        "required": ["symbol"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "execute_bracket_with_percentages",
                    "description": (
                        "🎯 AUTOMATED bracket order with percentage-based SL/TP. USE THIS for natural language commands!\n\n"
                        "This function handles ALL the work automatically:\n"
                        "1. Fetches current market price\n"
                        "2. Calculates absolute SL/TP from percentages\n"
                        "3. Executes bracket order with proper syntax\n\n"
                        "WHEN TO USE THIS:\n"
                        "✅ User says: 'Buy 0.03 ZEC/USD with 1% SL and 2% TP'\n"
                        "✅ User says: 'Paper buy 0.1 BTC/USD, stop-loss 2% below, take-profit 3% above'\n"
                        "✅ User says: 'Enter ETH/USD 0.5 with 1.5% stop and 2.5% target'\n\n"
                        "SIMPLY CALL:\n"
                        "execute_bracket_with_percentages(\n"
                        "  symbol='ZEC/USD',\n"
                        "  amount=0.03,\n"
                        "  sl_percent=1,   # 1% BELOW entry\n"
                        "  tp_percent=2    # 2% ABOVE entry\n"
                        ")\n\n"
                        "The function does the rest automatically and returns detailed results."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "symbol": {
                                "type": "string",
                                "description": "Trading pair (e.g., 'BTC/USD', 'ETH/USD', 'ZEC/USD')"
                            },
                            "amount": {
                                "type": "number",
                                "description": "Quantity to trade (e.g., 0.03, 0.1, 0.5)"
                            },
                            "sl_percent": {
                                "type": "number",
                                "description": "Stop-loss percentage BELOW entry price (e.g., 1 for 1% below, 2.5 for 2.5% below)"
                            },
                            "tp_percent": {
                                "type": "number",
                                "description": "Take-profit percentage ABOVE entry price (e.g., 2 for 2% above, 3.5 for 3.5% above)"
                            }
                        },
                        "required": ["symbol", "amount", "sl_percent", "tp_percent"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "execute_trading_command",
                    "description": (
                        "Execute trading commands AND real-time data queries on Kraken.\n\n"
                        "🔍 FRESH DATA QUERIES - Use these for real-time data requests:\n"
                        "When user asks for 'fresh', 'current', 'right now', or 'latest' data, "
                        "CALL THIS TOOL with query commands. These bypass the 5-minute cache!\n\n"
                        "🚨 CRITICAL: You MUST convert natural language to exact command formats below.\n\n"
                        "═══ REAL-TIME QUERY COMMANDS (BYPASSES CACHE) ═══\n\n"
                        "- 'bal' → Get FRESH balances NOW (not cached data)\n"
                        "- 'open' → Get FRESH open orders NOW (not cached)\n"
                        "- 'open btc/usd' → Get fresh orders for specific symbol\n"
                        "- 'price btc/usd' → Get current market price NOW\n\n"
                        "💡 When user says 'show me my open orders using fresh data', call: execute_trading_command('open')\n"
                        "💡 When user says 'what's my balance right now', call: execute_trading_command('bal')\n\n"
                        "BRACKET ORDER (REQUIRED FOR ALL TRADES):\n"
                        "Format: bracket <symbol> <amount> tp <price> sl <price>\n"
                        "Example: bracket zec/usd 0.03 tp 490.50 sl 480.25\n"
                        "⚠️ All prices MUST be ABSOLUTE NUMBERS (not percentages)\n\n"
                        "MARKET ORDERS (Paper mode only):\n"
                        "- 'buy 10 usd btc/usd' → Buy $10 worth\n"
                        "- 'sell all zec/usd' → Sell entire position\n\n"
                        "ORDER MANAGEMENT:\n"
                        "- 'cancel ORDER_ID' → Cancel specific order\n"
                        "- 'cancel ORDER_ID btc/usd' → Cancel with symbol\n\n"
                        "═══ PERCENTAGE CONVERSION WORKFLOW ═══\n\n"
                        "When user says '1% SL' or '2% TP', you MUST:\n"
                        "1. Call get_market_price(symbol) to fetch current price\n"
                        "2. Calculate absolute SL/TP prices:\n"
                        "   - SL = current_price * (1 - sl_percent/100)\n"
                        "   - TP = current_price * (1 + tp_percent/100)\n"
                        "3. Format bracket command with calculated prices\n\n"
                        "Example workflow for 'Buy 0.03 ZEC/USD with 1% SL and 2% TP':\n"
                        "1. get_market_price('ZEC/USD') → returns $485.50\n"
                        "2. Calculate: SL = 485.50 * 0.99 = 480.65, TP = 485.50 * 1.02 = 495.21\n"
                        "3. execute_trading_command('bracket zec/usd 0.03 tp 495.21 sl 480.65')\n\n"
                        "═══ COMMON MISTAKES (AVOID THESE) ═══\n\n"
                        "❌ WRONG: 'Paper buy 0.03 ZEC/USD with 1% SL and 2% TP'\n"
                        "✅ RIGHT: First get price, then 'bracket zec/usd 0.03 tp 495.21 sl 480.65'\n\n"
                        "❌ WRONG: 'bracket zec/usd 0.03 tp 2% sl 1%'\n"
                        "✅ RIGHT: Convert percentages to absolute prices first\n\n"
                        "❌ WRONG: 'buy zec/usd with stop loss'\n"
                        "✅ RIGHT: Use bracket command with exact prices\n"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": "The EXACT command string matching one of the formats above. NEVER pass natural language - convert it to canonical syntax first."
                            }
                        },
                        "required": ["command"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "show_last_evaluations",
                    "description": "Show the most recent evaluation decisions with indicators. Use this when the user asks what you've been evaluating, what signals you're seeing, or to show recent decision history. Returns timestamped evaluations with RSI, ATR, volume, decision, and reason.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "limit": {
                                "type": "integer",
                                "description": "Number of recent evaluations to show (default: 20)",
                                "default": 20
                            },
                            "symbol": {
                                "type": "string",
                                "description": "Filter by symbol (e.g., 'BTC/USD'), or omit for all symbols"
                            }
                        }
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "show_today_summary",
                    "description": "Show summary of today's evaluations with decision counts and NO_TRADE reason breakdown. Use this when the user asks 'why no trades today' or 'what have you been doing all day'. Returns total evaluations, BUY/SELL/NO_TRADE/ERROR counts, and grouped reasons for NO_TRADE decisions.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "symbol": {
                                "type": "string",
                                "description": "Filter by symbol (e.g., 'BTC/USD'), or omit for all symbols"
                            }
                        }
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "explain_why_no_trades",
                    "description": "Generate a data-backed explanation for why no trades occurred today. Use this when the user asks WHY you haven't traded. Returns human-readable explanation with counts and specific reasons from evaluation logs. ALWAYS use this when user asks about lack of trades instead of generic responses.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "symbol": {
                                "type": "string",
                                "description": "Filter by symbol (e.g., 'BTC/USD'), or omit for all symbols"
                            }
                        }
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "check_heartbeat",
                    "description": "Check if the evaluation loop is running properly. Use this when the user asks if you're working, if the scheduler is stuck, or to verify the 5-minute loop is active. Returns status, last evaluation time, and staleness warning if loop hasn't run in > 10 minutes.",
                    "parameters": {
                        "type": "object",
                        "properties": {}
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "run_paper_trade_test",
                    "description": "Run a comprehensive paper trading self-test to verify the paper trading system works end-to-end. Use this when the user asks to test the paper trading system, verify orders are being tracked, or wants to run a diagnostic. Executes a small bracket order and verifies it appears in open orders query. Only runs in PAPER mode. Returns detailed test report with PASS/FAIL status.",
                    "parameters": {
                        "type": "object",
                        "properties": {}
                    }
                }
            }
        ]

        # Build messages with conversation history
        # 1. Start with system prompt
        messages = [{"role": "system", "content": system_prompt}]
        
        # 2. Add conversation history (past user/assistant exchanges)
        conversation_history = _get_conversation_history(session_id)
        messages.extend(conversation_history)
        
        # 3. Add current user message
        messages.append({"role": "user", "content": user_block})
        
        # Initial API call with tools (60s timeout to avoid shell timeouts)
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
            assistant_response = assistant_message.content or "No response."
            
            # Save to conversation history
            _add_to_conversation(session_id, "user", text)
            _add_to_conversation(session_id, "assistant", assistant_response)
            
            return assistant_response
        
        # Handle tool calls
        messages.append(assistant_message)
        
        for tool_call in assistant_message.tool_calls:
            function_name = tool_call.function.name
            function_args = json.loads(tool_call.function.arguments)
            
            if function_name == "get_market_price":
                symbol = function_args.get("symbol", "")
                result = _get_market_price(symbol)
                
                messages.append({
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "name": function_name,
                    "content": result
                })
            
            elif function_name == "get_market_info":
                symbol = function_args.get("symbol", "")
                result = _get_market_info(symbol)
                
                messages.append({
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "name": function_name,
                    "content": result
                })
            
            elif function_name == "execute_bracket_with_percentages":
                symbol = function_args.get("symbol", "")
                amount = function_args.get("amount", 0)
                sl_percent = function_args.get("sl_percent", 0)
                tp_percent = function_args.get("tp_percent", 0)
                result = _execute_bracket_with_percentages(symbol, amount, sl_percent, tp_percent)
                
                messages.append({
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "name": function_name,
                    "content": result
                })
            
            elif function_name == "execute_trading_command":
                command = function_args.get("command", "")
                result = _execute_trading_command(command)
                
                messages.append({
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "name": function_name,
                    "content": result
                })
            
            elif function_name == "show_last_evaluations":
                limit = function_args.get("limit", 20)
                symbol = function_args.get("symbol")
                evaluations = get_last_evaluations(limit=limit, symbol=symbol)
                
                if not evaluations:
                    result = "No evaluations found in the log."
                else:
                    result = f"Last {len(evaluations)} evaluations:\n\n"
                    for eval_data in evaluations:
                        try:
                            ts = eval_data.get('timestamp_utc', 'N/A')[:19]  # Trim milliseconds
                            sym = eval_data.get('symbol', 'N/A')
                            decision = eval_data.get('decision', 'N/A')
                            reason = eval_data.get('reason', 'N/A')
                            regime = eval_data.get('regime', 'N/A')
                            rsi = eval_data.get('rsi')
                            atr = eval_data.get('atr')
                            
                            result += f"[{ts}] {sym}: {decision} - {reason}\n"
                            
                            if regime:
                                # Safely format RSI and ATR values BEFORE f-string
                                rsi_str = "N/A" if rsi is None else f"{rsi:.1f}"
                                atr_str = "N/A" if atr is None else f"{atr:.2f}"
                                result += f"  Regime: {regime}, RSI: {rsi_str}, ATR: {atr_str}\n"
                        except Exception as e:
                            # Logging errors should NEVER crash trade execution
                            result += f"  [Logging Error: {e}]\n"
                
                messages.append({
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "name": function_name,
                    "content": result
                })
            
            elif function_name == "show_today_summary":
                symbol = function_args.get("symbol")
                summary = get_today_summary(symbol=symbol)
                
                result = f"Today's Evaluation Summary ({summary.get('date', 'N/A')}):\n\n"
                result += f"Total evaluations: {summary.get('total_evaluations', 0)}\n\n"
                
                decision_counts = summary.get('decision_counts', {})
                if decision_counts:
                    result += "Decision breakdown:\n"
                    for decision, count in decision_counts.items():
                        result += f"  {decision}: {count}\n"
                
                no_trade_reasons = summary.get('no_trade_reasons', [])
                if no_trade_reasons:
                    result += "\nNO_TRADE reasons:\n"
                    for reason, count in no_trade_reasons[:10]:  # Top 10
                        result += f"  {count}x: {reason}\n"
                
                messages.append({
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "name": function_name,
                    "content": result
                })
            
            elif function_name == "explain_why_no_trades":
                symbol = function_args.get("symbol")
                explanation = explain_why_no_trades_today(symbol=symbol)
                
                messages.append({
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "name": function_name,
                    "content": explanation
                })
            
            elif function_name == "check_heartbeat":
                heartbeat = get_heartbeat_status()
                
                status = heartbeat.get('status', 'unknown')
                message = heartbeat.get('message', 'No status available')
                
                result = f"Heartbeat Status: {status.upper()}\n\n{message}"
                
                if heartbeat.get('is_stale'):
                    result += "\n\n⚠️ PROBLEM: The 5-minute evaluation loop is not running properly!"
                
                messages.append({
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "name": function_name,
                    "content": result
                })
            
            elif function_name == "run_paper_trade_test":
                test_result = _run_paper_trade_test()
                
                messages.append({
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "name": function_name,
                    "content": test_result
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
        assistant_response = final_message.content or "Command executed (no response from assistant)."
        
        # ═══════════════════════════════════════════════════════════════════════
        # ANTI-HALLUCINATION VALIDATOR
        # ═══════════════════════════════════════════════════════════════════════
        # Prevents LLM from claiming trade execution without actual confirmation
        from trade_result_validator import LLMResponseValidator
        
        # Extract tool results from message history
        # Handle both dict and ChatCompletionMessage objects
        tool_results = []
        for msg in messages:
            # Check if it's a dict or a Pydantic model
            if isinstance(msg, dict):
                if msg.get('role') == 'tool':
                    tool_results.append(msg)
            elif hasattr(msg, 'role') and msg.role == 'tool':
                # Convert Pydantic model to dict
                tool_results.append({
                    'role': msg.role,
                    'content': msg.content,
                    'name': getattr(msg, 'name', None),
                })
        
        # Validate LLM response against tool results
        is_valid, error_msg, corrected_response = LLMResponseValidator.validate_response(
            assistant_response,
            tool_results
        )
        
        if not is_valid:
            # LLM hallucinated trade execution - block and replace with safe response
            logger.error(f"[LLM-HALLUCINATION] {error_msg}")
            logger.error(f"[LLM-HALLUCINATION] Original response: {assistant_response[:200]}")
            logger.error(f"[LLM-HALLUCINATION] Corrected to: {corrected_response[:200]}")
            
            # Use corrected response instead
            assistant_response = corrected_response
        else:
            logger.debug(f"[VALIDATOR] ✓ Response validated - no hallucination detected")
        
        # Save to conversation history
        _add_to_conversation(session_id, "user", text)
        _add_to_conversation(session_id, "assistant", assistant_response)
        
        return assistant_response

    except Exception as e:
        return "[Backend Error] " + "".join(
            [f"{type(e).__name__}: {e}\n", traceback.format_exc()]
        )


# Optional local test
if __name__ == "__main__":
    print(ask_llm("remember: call me Jimmy"))
    print(ask_llm("memory"))
    print(ask_llm("report"))