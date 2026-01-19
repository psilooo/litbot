# lithood/config.py
"""Strategy configuration from summary.md."""

import os
from decimal import Decimal
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

# Environment
LIGHTER_BASE_URL = os.getenv("LIGHTER_BASE_URL", "https://mainnet.zklighter.elliot.ai")
LIGHTER_PRIVATE_KEY = os.getenv("LIGHTER_PRIVATE_KEY", "")
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "2"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Proxy Configuration
PROXY_HOST = os.getenv("PROXY_HOST", "")
PROXY_PORT = os.getenv("PROXY_PORT", "")
PROXY_USERNAME = os.getenv("PROXY_USERNAME", "")
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD", "")

def get_proxy_url() -> str:
    """Build proxy URL from components. Returns empty string if not configured."""
    if not PROXY_HOST or not PROXY_PORT:
        return ""
    if PROXY_USERNAME and PROXY_PASSWORD:
        return f"http://{PROXY_USERNAME}:{PROXY_PASSWORD}@{PROXY_HOST}:{PROXY_PORT}"
    return f"http://{PROXY_HOST}:{PROXY_PORT}"

PROXY_URL = get_proxy_url()

# Set proxy environment variables for aiohttp/requests to pick up
if PROXY_URL:
    os.environ["HTTP_PROXY"] = PROXY_URL
    os.environ["HTTPS_PROXY"] = PROXY_URL


@dataclass
class GridLevel:
    """Single grid level configuration."""
    price: Decimal
    size: Decimal  # LIT for sells, USDC for buys


# Capital Allocation (from summary.md)
ALLOCATION = {
    "core_lit": Decimal("8000"),
    "grid_sell_lit": Decimal("5000"),
    "reserve_lit": Decimal("4175"),
    "grid_buy_usdc": Decimal("5000"),
    "hedge_margin_usdc": Decimal("1500"),
    "cash_reserve_usdc": Decimal("800"),
}

# Grid Buy Levels (from summary.md)
GRID_BUY_LEVELS = [
    GridLevel(price=Decimal("1.655"), size=Decimal("550")),
    GridLevel(price=Decimal("1.630"), size=Decimal("550")),
    GridLevel(price=Decimal("1.605"), size=Decimal("600")),
    GridLevel(price=Decimal("1.580"), size=Decimal("600")),
    GridLevel(price=Decimal("1.555"), size=Decimal("650")),
    GridLevel(price=Decimal("1.530"), size=Decimal("650")),
    GridLevel(price=Decimal("1.505"), size=Decimal("700")),
    GridLevel(price=Decimal("1.480"), size=Decimal("700")),
]

# Grid Sell Levels (from summary.md)
GRID_SELL_LEVELS = [
    GridLevel(price=Decimal("1.705"), size=Decimal("600")),
    GridLevel(price=Decimal("1.730"), size=Decimal("600")),
    GridLevel(price=Decimal("1.755"), size=Decimal("600")),
    GridLevel(price=Decimal("1.780"), size=Decimal("650")),
    GridLevel(price=Decimal("1.805"), size=Decimal("650")),
    GridLevel(price=Decimal("1.830"), size=Decimal("700")),
    GridLevel(price=Decimal("1.855"), size=Decimal("700")),
    GridLevel(price=Decimal("1.880"), size=Decimal("500")),
]

# Grid Parameters
GRID_SPREAD = Decimal("0.03")  # 3% spread for cycling
GRID_PROFIT_RETAIN = Decimal("0.03")  # 3% kept as profit each cycle

# Hedge Parameters (from summary.md)
HEDGE_CONFIG = {
    "enabled": True,
    "short_size": Decimal("3000"),
    "margin_usdc": Decimal("1500"),
    "leverage": Decimal("3.36"),
    "stop_loss_price": Decimal("1.95"),
    "re_entry_price": Decimal("1.75"),
    "re_entry_cooldown_hours": 24,
    "pause_if_negative_funding_hours": 24,
}

# Floor Protection Tiers (from summary.md)
FLOOR_CONFIG = {
    "floor_value": Decimal("25000"),
    "emergency_buffer": Decimal("26000"),
    "tiers": [
        {"price": Decimal("1.50"), "action": "pause_grid_buys"},
        {"price": Decimal("1.40"), "action": "sell_reserve", "amount": Decimal("2000")},
        {"price": Decimal("1.30"), "action": "sell_reserve", "amount": Decimal("2175")},
        {"price": Decimal("1.20"), "action": "cancel_grid_sell", "amount": Decimal("3000")},
        {"price": Decimal("1.10"), "action": "emergency_exit"},
    ],
}

# Upside Targets (from summary.md)
UPSIDE_TARGETS = {
    Decimal("2.50"): Decimal("1000"),
    Decimal("3.00"): Decimal("1500"),
    Decimal("3.50"): Decimal("2000"),
    Decimal("4.00"): Decimal("2000"),
    Decimal("4.50"): Decimal("1500"),
}

# Limits
LIMITS = {
    "max_lit": Decimal("22000"),
    "min_lit": Decimal("8000"),
    "max_short": Decimal("3000"),
    "min_usdc": Decimal("800"),
}
