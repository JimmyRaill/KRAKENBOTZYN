import ccxt
from loguru import logger
from tenacity import retry, wait_random_exponential, stop_after_attempt
from typing import Any, Dict, Optional

from config import KRAKEN_API_KEY, KRAKEN_API_SECRET, VALIDATE_ONLY


def _to_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


class KrakenBot:

    def __init__(self) -> None:
        self.ex = ccxt.kraken({
            "apiKey": KRAKEN_API_KEY,
            "secret": KRAKEN_API_SECRET,
            "enableRateLimit": True,
        })
        logger.add("krakenbot.log", rotation="1 MB", retention=3)

    @retry(wait=wait_random_exponential(min=1, max=8),
           stop=stop_after_attempt(3))
    def price(self, symbol: str) -> float:
        ticker: Any = self.ex.fetch_ticker(
            symbol)  # ccxt returns a mapping-like object
        last = ticker.get("last", None)
        px = _to_float(last, 0.0)
        if px <= 0:
            bid = _to_float(ticker.get("bid"), 0.0)
            ask = _to_float(ticker.get("ask"), 0.0)
            px = (bid + ask) / 2.0 if (bid > 0 and ask > 0) else 0.0
        if px <= 0:
            raise ValueError("No valid market price available.")
        return px

    @retry(wait=wait_random_exponential(min=1, max=8),
           stop=stop_after_attempt(3))
    def balance(self) -> Dict[str, Any]:
        b: Any = self.ex.fetch_balance()
        return b if isinstance(b, dict) else dict(b)

    def _with_validate(self, params: Optional[Dict[str,
                                                   Any]]) -> Dict[str, Any]:
        p: Dict[str, Any] = dict(params) if params else {}
        if VALIDATE_ONLY:
            p["validate"] = True  # Kraken dry-run
        return p

    @retry(wait=wait_random_exponential(min=1, max=8),
           stop=stop_after_attempt(3))
    def market_buy_usd(self, symbol: str, usd_amount: float) -> Dict[str, Any]:
        if usd_amount <= 0:
            raise ValueError("usd_amount must be > 0")
        price = self.price(symbol)
        amount = usd_amount / price
        # clamp to exchange precision (string -> float)
        amount_prec_str = self.ex.amount_to_precision(symbol, amount)
        if amount_prec_str is None:
            raise ValueError("amount_to_precision returned None")
        amount_prec = _to_float(amount_prec_str, 0.0)
        if amount_prec <= 0:
            raise ValueError("Rounded amount is zero.")
        params = self._with_validate({})
        return self.ex.create_order(symbol, "market", "buy", amount_prec, None,
                                    params)  # type: ignore[arg-type]

    @retry(wait=wait_random_exponential(min=1, max=8),
           stop=stop_after_attempt(3))
    def market_sell_all(self, symbol: str) -> Dict[str, Any]:
        bal = self.balance()
        base = symbol.split("/")[0]
        free = 0.0
        try:
            free_map = bal.get("free") or {}
            if isinstance(free_map, dict) and base in free_map:
                free = _to_float(free_map.get(base), 0.0)
            if free <= 0:
                tot_map = bal.get(base)
                if isinstance(tot_map, dict):
                    free = _to_float(tot_map.get("total"), 0.0)
                else:
                    free = _to_float(bal.get(base), 0.0)
        except Exception:
            free = 0.0

        if free <= 0:
            raise ValueError(f"No free {base} balance to sell.")

        amount_prec_str = self.ex.amount_to_precision(symbol, free)
        if amount_prec_str is None:
            raise ValueError("amount_to_precision returned None")
        amount_prec = _to_float(amount_prec_str, 0.0)
        if amount_prec <= 0:
            raise ValueError("Rounded amount is zero.")
        params = self._with_validate({})
        return self.ex.create_order(symbol, "market", "sell", amount_prec,
                                    None, params)  # type: ignore[arg-type]
