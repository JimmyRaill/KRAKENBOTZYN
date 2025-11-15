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
    order_ids: List[str] = None
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
        Handles both success and error cases.
        """
        # Check for error patterns
        has_error = any(pattern in result_str for pattern in [
            '-ERR', 'failed', 'FAIL', 'Error', 'error'
        ])
        
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
            # Trading commands need explicit success keywords
            has_success = any(pattern in result_str for pattern in [
                'BRACKET OK', 'OK', 'SUCCESS', 'executed', 'FILLED'
            ]) and not has_error
        
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
    SUCCESS_PATTERNS = [
        r'\bsuccessfully executed\b',
        r'\btrade executed\b',
        r'\border placed\b',
        r'\bposition opened\b',
        r'\bexecuted.*trade\b',
        r'\bopened.*position\b',
        r'\bplaced.*order\b',
        r'\bcompleted.*trade\b',
        r'\bfilled.*order\b',
    ]
    
    # Error/failure patterns
    ERROR_PATTERNS = [
        r'-ERR',
        r'\bfailed\b',
        r'\berror\b',
        r'\bcannot\b',
        r'\bunable to\b',
        r'\binsufficient\b',
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
                "⚠️ Internal consistency error: I cannot confirm that any trade was executed. "
                "Please check your open orders and balances directly using the 'open' and 'bal' commands."
            )
            return False, error, corrected
        
        # Check if any tool reported success
        any_success = any(result.get('success', False) for result in trade_tools)
        any_error = any(result.get('error') is not None for result in trade_tools)
        
        if any_error and llm_claims_success:
            # Tool failed but LLM claims success
            error = f"LLM claimed success but tool showed failure: {trade_tools[0].get('error', 'unknown error')}"
            logger.error(f"[LLM-HALLUCINATION] {error}")
            corrected = (
                "⚠️ The trade execution encountered an error. "
                f"Details: {trade_tools[0].get('raw_message', 'Check logs for details')}"
            )
            return False, error, corrected
        
        if not any_success and llm_claims_success:
            # No tool confirmed success but LLM claims it
            error = "LLM claimed success but no tool result confirmed execution"
            logger.error(f"[LLM-HALLUCINATION] {error}")
            corrected = (
                "⚠️ I cannot confirm the execution status. "
                "Please verify your open orders with the 'open' command."
            )
            return False, error, corrected
        
        # Validation passed - LLM claim matches tool results
        logger.info("[VALIDATOR] ✓ LLM response validated against tool results")
        return True, None, None
    
    @classmethod
    def _detect_success_claim(cls, text: str) -> bool:
        """Detect if LLM is claiming successful trade execution"""
        text_lower = text.lower()
        return any(re.search(pattern, text_lower) for pattern in cls.SUCCESS_PATTERNS)
    
    @classmethod
    def _extract_trade_tool_results(cls, tool_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Extract trade-related tool results and parse them.
        
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
