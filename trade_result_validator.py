"""
Trade Result Validator - Anti-Hallucination Safeguards

This module enforces that the LLM cannot claim trade execution without actual confirmation.
All trade-related claims are validated against structured tool results.
"""
import re
import json
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, asdict
from loguru import logger


@dataclass
class TradeResult:
    """Structured trade execution result - replaces string-based responses"""
    success: bool
    command: str
    symbol: Optional[str] = None
    mode: Optional[str] = None
    order_ids: Optional[List[str]] = None
    error: Optional[str] = None
    raw_message: str = ""
    
    def __post_init__(self):
        if self.order_ids is None:
            self.order_ids = []
    
    def to_json(self) -> str:
        """Serialize to JSON for LLM tool results"""
        return json.dumps(asdict(self), indent=2)
    
    @classmethod
    def from_command_result(cls, command: str, result_str: str, mode: str) -> 'TradeResult':
        """
        Parse command result string into structured format.
        Handles both success and error cases, including PARTIAL SUCCESS.
        
        PARTIAL SUCCESS DETECTION:
        - Entry executed on Kraken but protection (TP/SL) failed
        - Marked as success=True so LLM doesn't hallucinate "nothing executed"
        - Error field contains protection failure details
        """
        # Check for error patterns (EXPANDED for Kraken-specific errors)
        has_error = any(pattern in result_str for pattern in [
            '-ERR', 'failed', 'FAIL', 'Error', 'error',
            'EOrder:', 'EGeneral:', 'EService:', 'EFunding:', 'ETAPI:',  # Kraken error codes
            'Insufficient funds', 'insufficient', 'Invalid',
            'Order could not be created', 'cannot', 'unable to',
            'Minimum order size', 'Position size', 'Rate limit',
            'API rate limit', 'Invalid nonce', 'Invalid signature'
        ])
        
        # CRITICAL: Detect partial success patterns (entry succeeded, protection failed)
        has_entry_success = any(pattern in result_str for pattern in [
            'ENTRY EXECUTED ON KRAKEN',
            'Entry Order:',
            '✅ Entry:',
            'Entry: O',  # Order ID pattern for entry
            'PARTIAL EXECUTION',
            'entry succeeded',
            'entry_status": "success'
        ])
        
        has_protection_failure = any(pattern in result_str for pattern in [
            'PROTECTION FAILED',
            'NAKED POSITION',
            'protection_status": "failed',
            'protection_status": "not_protected',
            'NO PROTECTIVE BRACKETS',
            'TP/SL PLACEMENT FAILED'
        ])
        
        is_partial_success = has_entry_success and has_protection_failure
        
        # Determine command type first (needed for success detection)
        cmd_type = "unknown"
        is_query_command = False
        
        if "bracket" in command.lower():
            cmd_type = "bracket"
        elif "open" in command.lower():
            cmd_type = "query_orders"
            is_query_command = True
        elif "bal" in command.lower():
            cmd_type = "query_balance"
            is_query_command = True
        elif "price" in command.lower():
            cmd_type = "query_price"
            is_query_command = True
        elif "cancel" in command.lower():
            cmd_type = "cancel"
        
        # Check for success patterns
        # CRITICAL FIX: Query commands (bal, open, price) are ALWAYS successful unless they error
        # They don't return "OK" or "SUCCESS" keywords, so we check by command type
        if is_query_command:
            has_success = not has_error  # Queries succeed if they don't error
        else:
            # Trading commands: Check for full success OR partial success
            has_success_keyword = any(pattern in result_str for pattern in [
                'BRACKET OK', 'BUY OK', 'SELL OK', 'LIMIT BUY OK', 'LIMIT SELL OK',
                'STOP BUY OK', 'STOP SELL OK', 'CANCEL OK', 'OK', 'SUCCESS', 'executed', 'FILLED',
                'FULLY SUCCESSFUL', 'FULLY PROTECTED'
            ])
            has_order_id = bool(re.search(r'(PAPER-[A-F0-9]+|O[A-Z0-9]{5,}|id=\S+)', result_str))
            
            # Success if:
            # 1. Normal success: success keyword AND no error
            # 2. Partial success: entry succeeded even if protection failed
            has_success = (has_success_keyword and not has_error) or is_partial_success
        
        # Extract order IDs
        order_ids = re.findall(r'(PAPER-[A-F0-9]+|O[A-Z0-9]{5,})', result_str)
        
        # Extract symbol if present
        symbol_match = re.search(r'([A-Z]{3,}/[A-Z]{3,})', result_str)
        symbol = symbol_match.group(1) if symbol_match else None
        
        return cls(
            success=has_success,
            command=cmd_type,
            symbol=symbol,
            mode=mode,
            order_ids=order_ids,
            error=result_str if has_error else None,
            raw_message=result_str
        )


class LLMResponseValidator:
    """
    Validates LLM responses against actual tool execution results.
    Prevents hallucination of trade executions.
    """
    
    # Success claim patterns that trigger validation
    # COMPREHENSIVE: Match trade execution claims while avoiding casual diagnostic language
    # Covers: auxiliaries, adverbs, light verbs, contractions, direct forms, plurals, all tenses
    SUCCESS_PATTERNS = [
        # Verb-first patterns: "executed (your/the) [descriptors] trade(s)/order(s)/position(s)"
        r'\b(successfully\s+)?(executed|placed|opened|filled|completed)\s+(your|the|a|an|this|that|these|those)\s+(?:\S+\s+)+(trades?|orders?|positions?)\b',
        # Verb-first simple: "executed (your/the/a) trade(s)/order(s)/position(s)"
        r'\b(successfully\s+)?(executed|placed|opened|filled|completed)\s+(your|the|a|an|this|that|these|those)?\s*(trades?|orders?|positions?)\b',
        
        # Noun-first with auxiliaries: "(your) trade is/was/has been executed"
        r'\b(your|the|a|an|this|that|these|those)\s+(?:\S+\s+)+(trades?|orders?|positions?)\s+(is|are|was|were|has\s+been|had\s+been|have\s+been)\s+(executed|placed|opened|filled|completed)(\s+successfully)?\b',
        r'\b(your|the|a|an|this|that|these|those)?\s*(trades?|orders?|positions?)\s+(is|are|was|were|has\s+been|had\s+been|have\s+been)\s+(executed|placed|opened|filled|completed)(\s+successfully)?\b',
        
        # Contractions: "(your) order's/trade's filled" (handles apostrophe variants)
        r'\b(your|the|a|an|this|that|these|those)\s+(?:\S+\s+)+(trades?|orders?|positions?)[\'\u2019]s?\s+(executed|placed|opened|filled|completed)\b',
        r'\b(your|the|a|an|this|that|these|those)?\s*(trades?|orders?|positions?)[\'\u2019]s?\s+(just|now|finally|already)?\s*(executed|placed|opened|filled|completed)\b',
        r'\b(your|the|a|an|this|that|these|those)?\s*(trades?|orders?|positions?)[\'\u2019]s?\s+been\s+(executed|placed|opened|filled|completed)\b',
        
        # Noun-first with adverbs: "(your) order just/now filled"
        r'\b(your|the|a|an|this|that|these|those)\s+(?:\S+\s+)+(trades?|orders?|positions?)\s+(just|now|finally|already)\s+(executed|placed|opened|filled|completed)\b',
        r'\b(your|the|a|an|this|that|these|those)?\s*(trades?|orders?|positions?)\s+(just|now|finally|already)\s+(executed|placed|opened|filled|completed)\b',
        
        # Noun-first with light verbs: "(your) order got/get filled"
        r'\b(your|the|a|an|this|that|these|those)\s+(?:\S+\s+)+(trades?|orders?|positions?)\s+(got|gets?|getting)\s+(executed|placed|opened|filled|completed)\b',
        r'\b(your|the|a|an|this|that|these|those)?\s*(trades?|orders?|positions?)\s+(got|gets?|getting)\s+(executed|placed|opened|filled|completed)\b',
        
        # Common short forms (singular and plural)
        r'\borders?\s+filled\b',
        r'\btrades?\s+executed\b',
        r'\bpositions?\s+opened\b',
        r'\borders?\s+executed\b',
    ]
    
    # Patterns that indicate query/diagnostic language (NOT trade claims)
    # Used to EXCLUDE responses from validation even if they match success patterns
    QUERY_INDICATORS = [
        # Query/fetch/check language
        r'\b(let me|i will|i\'ll)\s+(query|check|show|look|fetch|retrieve)\b',
        r'\bquery\s+(for|to|the)\b',
        r'\bcheck\s+(your|the|my)\s+(balance|orders|position|history)\b',
        r'\bshow\s+(you|me|the)\s+(balance|orders|position|history)\b',
        r'\blook(ing)?\s+at\s+(your|the|my)\s+(balance|orders|position|history|evaluations|trades)\b',
        r'\bfetch(ing|ed)?\s+(your|the|my)\s+(balance|data|information)\b',
        r'\bretriev(e|ing|ed)\s+(your|the|my)\s+(balance|data)\b',
        
        # Status/report presentation language
        r'\bhere\s+(is|are)\s+(your|the)\s+(balance|orders|position|history|status|diagnostic)\b',
        r'\bhere[\'\u2019]s\s+(the|your)\s+(detailed|current|latest)\b',
        r'\b(balance|open\s+orders|evaluation|history)\s+(shows|indicates)\b',
        
        # Diagnostic/status report language
        r'\bdiagnostic\s+(and|status|report|information)\b',
        r'\btrading\s+status\b',
        r'\bcurrent\s+mode\b',
        r'\bstatus\s+report\b',
        r'\b(detailed|complete)\s+(status|diagnostic|report)\b',
        
        # Market data presentation (not execution)
        r'\bcurrent\s+price\s+(of|for|is)\b',
        r'\bmarket\s+price\b',
        r'\bprice\s+(data|information|quote)\b',
        
        # Interrogative/question starters (asking about trades, not claiming execution)
        r'\bhow\s+many\s+(trades?|orders?|positions?)\b',
        r'\bshow\s+(me|us)\s+(the\s+)?(last|recent|all)\s+\d+\s+(trades?|orders?|positions?)\b',
        r'\bgive\s+(me|us)\s+(the\s+)?(exact\s+)?(number|count|list)\b',
        r'\bwhat\s+(are|is)\s+(the\s+)?(number|count|total)\s+of\b',
        r'\blist\s+(all|the)\s+(trades?|orders?|positions?)\b',
        r'\btell\s+me\s+(about|the\s+number)\b',
        
        # Historical/retrospective language (asking about past data)
        r'\bin\s+the\s+last\s+\d+\s+(hours?|days?|minutes?)\b',
        r'\btrade\s+history\b',
        r'\bfrom\s+(kraken|exchange|the\s+exchange)\b',
        r'\blast\s+\d+\s+(trades?|orders?|entries|executions)\b',
        r'\brecent\s+(trades?|orders?|history)\b',
        r'\bhistorical\s+(trades?|orders?|data)\b',
        
        # Data presentation headers/labels (NOT execution claims)
        r'\bsummary\s+of\s+(requested\s+)?information\b',
        r'\bnumber\s+of\s+(trades?|orders?|positions?)\s+in\s+(kraken|trade\s+history)\b',
        r'\bentries\s+from\s+(your|the)\s+(internal|executed|order)\s+log\b',
        r'\b(executed|placed)\s+(trades?|orders?)\s+in\s+the\s+last\b',  # "Executed trades in the last 24h" as a header
        r'\b\d+\s+entries\b',  # "0 entries", "5 entries"
        r'\b(zero|no)\s+(trades?|orders?|entries)\b',  # "No trades", "Zero entries"
    ]
    
    # Error/failure patterns (EXPANDED for Kraken-specific errors)
    ERROR_PATTERNS = [
        r'-ERR',
        r'\bfailed\b',
        r'\berror\b',
        r'\bcannot\b',
        r'\bunable to\b',
        r'\binsufficient\b',
        r'EOrder:',
        r'EGeneral:',
        r'EService:',
        r'EFunding:',
        r'ETAPI:',
        r'Insufficient funds',
        r'Order could not be created',
        r'\bInvalid\b',
        r'Minimum order size',
        r'Position size',
        r'Rate limit',
        r'Invalid nonce',
        r'Invalid signature',
    ]
    
    @classmethod
    def validate_response(
        cls, 
        llm_response: str, 
        tool_results: List[Dict[str, Any]]
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Validate LLM response against tool execution results.
        
        Args:
            llm_response: The LLM's generated response text
            tool_results: List of tool call results (messages with role='tool')
        
        Returns:
            (is_valid, error_message, corrected_response)
            - is_valid: True if response is truthful
            - error_message: Reason for failure (if invalid)
            - corrected_response: Safe response to return instead (if invalid)
        """
        # Check if LLM claims successful execution
        llm_claims_success = cls._detect_success_claim(llm_response)
        
        if not llm_claims_success:
            # No execution claims, allow response
            return True, None, None
        
        # LLM is claiming success - verify against tool results
        logger.info(f"[VALIDATOR] LLM claims execution success, validating against {len(tool_results)} tool results")
        
        # Parse tool results for actual execution status
        trade_tools = cls._extract_trade_tool_results(tool_results)
        
        if not trade_tools:
            # No trade tools were called, but LLM claims success
            error = "LLM claimed trade execution but no trade tools were called"
            logger.error(f"[LLM-HALLUCINATION] {error}")
            corrected = (
                "⚠️ Internal error: I attempted to execute a trade but no command was actually sent. "
                "Please verify your positions using 'open' and 'bal' commands. "
                "If you intended to trade, please retry the command."
            )
            return False, error, corrected
        
        # Check if any tool reported success
        any_success = any(result.get('success', False) for result in trade_tools)
        any_error = any(result.get('error') is not None for result in trade_tools)
        
        if any_error and llm_claims_success:
            # Tool failed but LLM claims success - surface the actual error
            actual_error = trade_tools[0].get('error', 'unknown error')
            error = f"LLM claimed success but tool showed failure: {actual_error}"
            logger.error(f"[LLM-HALLUCINATION] {error}")
            
            # Surface the actual error message to user instead of hiding it
            corrected = f"⚠️ The command failed with error:\n\n{actual_error}"
            return False, error, corrected
        
        if not any_success and llm_claims_success:
            # No tool confirmed success but LLM claims it
            # Check if there's an error message we can surface
            first_result = trade_tools[0] if trade_tools else {}
            actual_error = first_result.get('error')
            raw_message = first_result.get('raw_message', '')
            
            error = "LLM claimed success but no tool result confirmed execution"
            logger.error(f"[LLM-HALLUCINATION] {error}")
            
            if actual_error:
                # Surface the actual error instead of generic message
                corrected = f"⚠️ Command failed with error:\n\n{actual_error}"
            elif raw_message and any(err_pattern in raw_message for err_pattern in ['-ERR', 'failed', 'error', 'Error']):
                # Surface the raw message if it contains error information
                corrected = f"⚠️ Command failed:\n\n{raw_message}"
            else:
                # Last resort: show raw message and ask user to verify
                corrected = (
                    f"⚠️ Execution status unclear. Command output:\n\n{raw_message}\n\n"
                    "Please verify with 'open' and 'bal' commands."
                )
            return False, error, corrected
        
        # Validation passed - LLM claim matches tool results
        logger.info("[VALIDATOR] ✓ LLM response validated against tool results")
        return True, None, None
    
    @classmethod
    def _detect_success_claim(cls, text: str) -> bool:
        """
        Detect if LLM is claiming successful trade execution.
        
        Returns False if response clearly contains query/diagnostic language,
        even if it matches success patterns (to avoid false positives).
        
        PRIORITY: Query/diagnostic indicators take precedence over success patterns.
        If the response contains diagnostic language, it's NOT a trade claim.
        """
        text_lower = text.lower()
        
        # Check if response contains query/diagnostic language
        has_query_language = any(re.search(pattern, text_lower) for pattern in cls.QUERY_INDICATORS)
        
        # PRIORITY FIX: If it contains query/diagnostic language, it's NOT a trade claim
        # This prevents false positives on status reports that happen to mention trading
        if has_query_language:
            return False
        
        # Check if response claims trade success
        has_success_claim = any(re.search(pattern, text_lower) for pattern in cls.SUCCESS_PATTERNS)
        
        # Only validate if there's an explicit success claim AND no query language
        return has_success_claim
    
    @classmethod
    def _extract_trade_tool_results(cls, tool_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Extract trade-related tool results and parse them.
        
        CRITICAL: Skips query commands (bal, open, price) which aren't trades.
        
        Returns list of parsed TradeResult dictionaries.
        """
        trade_results = []
        
        for tool_msg in tool_results:
            if tool_msg.get('role') != 'tool':
                continue
            
            tool_name = tool_msg.get('name', '')
            content = tool_msg.get('content', '')
            
            # Check if this is a trade-related tool
            if tool_name in ['execute_trading_command', 'execute_bracket_with_percentages']:
                # CRITICAL FIX: Skip query commands - they're not trades
                # Query commands: bal, open, price, debug, history, evaluations
                content_lower = content.lower()
                is_query_result = any(indicator in content_lower for indicator in [
                    'balance:', 'balances:', 'equity:', 'usd value:',  # bal command
                    'open orders:', 'no open orders', 'orders for',    # open command
                    'current price:', 'market price:', 'price:',       # price command
                    'debug status:', 'heartbeat:', 'evaluation',       # debug/diagnostic
                    'trade history:', 'recent trades:',                # history command
                ])
                
                if is_query_result:
                    # This is a query result, not a trade - skip validation
                    logger.debug(f"[VALIDATOR] Skipping query result from validation (tool={tool_name})")
                    continue
                
                # Try to parse as JSON first (new structured format)
                try:
                    parsed = json.loads(content)
                    if 'success' in parsed:
                        trade_results.append(parsed)
                        continue
                except (json.JSONDecodeError, TypeError):
                    pass
                
                # Fall back to string parsing (legacy format)
                # Look for success/error indicators
                result_dict = {
                    'success': 'OK' in content and '-ERR' not in content,
                    'error': content if any(p in content for p in ['-ERR', 'failed', 'error']) else None,
                    'raw_message': content,
                    'order_ids': re.findall(r'(PAPER-[A-F0-9]+|O[A-Z0-9]{5,})', content)
                }
                trade_results.append(result_dict)
        
        return trade_results
    
    @classmethod
    def strip_unconfirmed_success_language(cls, text: str, has_confirmed_success: bool) -> str:
        """
        Strip success language from text if not backed by tool confirmation.
        
        Args:
            text: LLM response text
            has_confirmed_success: Whether any tool confirmed success
        
        Returns:
            Sanitized text with success claims removed/replaced
        """
        if has_confirmed_success:
            return text  # Allow success language
        
        # Replace success claims with neutral language
        replacements = {
            r'\bsuccessfully executed\b': 'attempted to execute',
            r'\btrade executed\b': 'trade command sent',
            r'\border placed\b': 'order command sent',
            r'\bposition opened\b': 'position command sent',
            r'\bexecuted the trade\b': 'sent the trade command',
            r'\bplaced the order\b': 'sent the order command',
        }
        
        sanitized = text
        for pattern, replacement in replacements.items():
            sanitized = re.sub(pattern, replacement, sanitized, flags=re.IGNORECASE)
        
        logger.warning(f"[VALIDATOR] Sanitized unconfirmed success language from response")
        return sanitized


def get_realtime_trading_status() -> Dict[str, Any]:
    """
    Fetch real-time trading status - bypasses status_service cache.
    
    Use this for execution-sensitive queries where 5-minute staleness is unacceptable.
    
    Returns:
        Fresh trading data directly from exchange wrapper / paper ledger
    """
    from exchange_manager import get_exchange, get_mode_str
    from account_state import get_balances, get_trade_history
    
    mode = get_mode_str()
    ex = get_exchange()
    
    try:
        # Fetch fresh data (no caching)
        open_orders = ex.fetch_open_orders()
        balances = get_balances()
        recent_trades = get_trade_history()[-50:] if get_trade_history() else []
        
        # Calculate equity
        total_equity = 0.0
        if balances:
            for currency, bal in balances.items():
                if isinstance(bal, dict):
                    total_equity += bal.get('usd_value', 0)
        
        return {
            'mode': mode,
            'timestamp': __import__('time').time(),
            'source': 'realtime_fetch',
            'open_orders': open_orders,
            'balances': balances,
            'total_equity_usd': total_equity,
            'recent_trades': recent_trades,
            'order_count': len(open_orders),
            'error': None
        }
    
    except Exception as e:
        logger.error(f"[REALTIME-STATUS] Failed to fetch: {e}")
        return {
            'mode': mode,
            'timestamp': __import__('time').time(),
            'source': 'realtime_fetch',
            'error': str(e),
            'open_orders': [],
            'balances': {},
            'recent_trades': [],
        }
