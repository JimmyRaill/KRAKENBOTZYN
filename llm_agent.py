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


# ---------- Trading helpers ----------
def _read_state() -> Dict[str, Any]:
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

        # Quick summaries with learning data
        if low in ("status", "report", "learning", "performance"):
            s = _read_state()
            lines = [f"SUMMARY — {_summarize_state_for_prompt(s)}"]
            
            if LEARNING_ENABLED:
                try:
                    lines.append("\n" + get_learning_summary())
                    lines.append("\n" + get_context_summary())
                except Exception:
                    pass
            
            return "\n".join(lines)

        # Build prompt
        state = _read_state()
        state_summary = _summarize_state_for_prompt(state)
        memory_summary = _mem_summary()

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
        learning_context = ""
        if LEARNING_ENABLED:
            try:
                learning_context = (
                    "\n\nLEARNING INSIGHTS:\n" + get_learning_summary() + "\n" +
                    "\nTIME CONTEXT:\n" + get_context_summary()
                )
            except Exception as e:
                learning_context = f"\n\n(Learning data unavailable: {e})"
        
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
            "DATA SOURCES:\n"
            "- TELEMETRY: Current account status, positions, P&L\n"
            "- MEMORY: User preferences, names, past conversations\n"
            "- LEARNING INSIGHTS: Your analyzed trading performance and patterns\n"
            "- TIME CONTEXT: Current date/time and market awareness\n\n"
            "RULES:\n"
            "- Never invent numbers; only use data from telemetry/learning\n"
            "- If data is missing, say so and suggest how to get it\n"
            "- When giving advice, explain WHY based on your learning\n"
            "- Be honest about uncertainty and limitations\n"
            "- Always remember: you exist to do the work FOR your owner\n"
        )

        telemetry_block = json.dumps(state, ensure_ascii=False)
        if len(telemetry_block) > 4000:
            telemetry_block = telemetry_block[:4000] + "...(truncated)"

        user_block = (
            "MEMORY:\n" + memory_summary + "\n" +
            "TELEMETRY:\n" + telemetry_block + "\n" +
            "SUMMARY:\n" + state_summary + learning_context + "\n" +
            "---\n" +
            f"USER: {text}"
        )

        client, err = _ensure_client()
        if err:
            return err

        assert client is not None  # for type checkers

        resp = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_block},
            ],
            temperature=0.7,
        )
        return resp.choices[0].message.content or "No response."

    except Exception as e:
        return "[Backend Error] " + "".join(
            [f"{type(e).__name__}: {e}\n", traceback.format_exc()]
        )


# Optional local test
if __name__ == "__main__":
    print(ask_llm("remember: call me Jimmy"))
    print(ask_llm("memory"))
    print(ask_llm("report"))