import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env", override=True)

KRAKEN_API_KEY = os.getenv("KRAKEN_API_KEY", "")
KRAKEN_API_SECRET = os.getenv("KRAKEN_API_SECRET", "")
VALIDATE_ONLY = os.getenv("KRAKEN_VALIDATE_ONLY", "1").lower() in ("1", "true", "yes", "on")
DEFAULT_SYMBOL = "ZEC/USD"
