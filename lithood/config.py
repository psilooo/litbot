# lithood/config.py
"""Strategy configuration - v2 with dynamic grid generation."""

import os
from decimal import Decimal
from dataclasses import dataclass
from typing import List
from dotenv import load_dotenv

load_dotenv()

# Environment
LIGHTER_BASE_URL = os.getenv("LIGHTER_BASE_URL", "https://mainnet.zklighter.elliot.ai")
LIGHTER_PRIVATE_KEY = os.getenv("LIGHTER_PRIVATE_KEY", "")  # Wallet key (for setup)
LIGHTER_API_KEY_PRIVATE = os.getenv("LIGHTER_API_KEY_PRIVATE", "")  # API key (for trading)
LIGHTER_API_KEY_INDEX = int(os.getenv("LIGHTER_API_KEY_INDEX", "3"))  # API key slot (3-254)
LIGHTER_ACCOUNT_INDEX = os.getenv("LIGHTER_ACCOUNT_INDEX", "")  # Your account index
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
class GridPair:
    """A paired grid level with designated buy and sell prices."""
    pair_id: int
    buy_price: Decimal
    sell_price: Decimal
    spread_pct: Decimal
    usdc_size: Decimal  # USDC per buy order
    lit_size: Decimal   # LIT per sell order


# Capital Allocation
ALLOCATION = {
    "core_lit": Decimal("11175"),       # 8000 + 3175 from reserve
    "grid_sell_lit": Decimal("5000"),
    "reserve_lit": Decimal("1000"),     # Moonbag - no sell orders
    "grid_buy_usdc": Decimal("6500"),
    "hedge_margin_usdc": Decimal("0"),  # Hedge disabled
    "cash_reserve_usdc": Decimal("800"),
}

# Market Symbols
SPOT_SYMBOL = "LIT/USDC"  # Spot market symbol (pair format)
PERP_SYMBOL = "LIT"       # Perp market symbol (base asset)

# Grid Configuration - Fixed levels with matching cycle spread
# Level spacing = cycle spread = 2%, so counter-orders land on grid levels (no drift)
GRID_CONFIG = {
    "num_buy_levels": 10,
    "num_sell_levels": 10,
    "total_buy_usdc": Decimal("6500"),      # Total USDC for all buy orders
    "total_sell_lit": Decimal("4000"),      # Total LIT for all sell orders
    "level_spacing_pct": Decimal("0.02"),   # 2% spacing between grid levels
    "cycle_spread_pct": Decimal("0.02"),    # 2% spread when cycling (MUST equal level_spacing)
    "profit_retention": Decimal("0.02"),    # 2% kept as profit each cycle
}


@dataclass
class GridLevel:
    """A single grid level (buy or sell)."""
    level_id: int
    price: Decimal
    size: Decimal  # LIT for sells, calculated from USDC for buys
    side: str  # "buy" or "sell"


def generate_grid_levels(entry_price: Decimal) -> tuple[List[GridLevel], List[GridLevel]]:
    """Generate grid levels with fixed 2% spacing.

    Returns:
        (buy_levels, sell_levels) - separate lists for each side
    """
    config = GRID_CONFIG
    spacing = config["level_spacing_pct"]

    # Generate buy levels (below entry) - each level is 2% below the previous
    buy_levels = []
    usdc_per_level = config["total_buy_usdc"] / config["num_buy_levels"]

    for i in range(config["num_buy_levels"]):
        # Level 1 is 2% below entry, level 2 is 4% below, etc.
        multiplier = (1 - spacing) ** (i + 1)
        price = entry_price * multiplier
        size = usdc_per_level / price  # LIT amount

        buy_levels.append(GridLevel(
            level_id=i + 1,
            price=price.quantize(Decimal("0.0001")),
            size=size.quantize(Decimal("0.01")),
            side="buy",
        ))

    # Generate sell levels (above entry) - each level is 2% above the previous
    sell_levels = []
    lit_per_level = config["total_sell_lit"] / config["num_sell_levels"]

    for i in range(config["num_sell_levels"]):
        # Level 1 is 2% above entry, level 2 is 4% above, etc.
        multiplier = (1 + spacing) ** (i + 1)
        price = entry_price * multiplier

        sell_levels.append(GridLevel(
            level_id=i + 1,
            price=price.quantize(Decimal("0.0001")),
            size=lit_per_level.quantize(Decimal("0.01")),
            side="sell",
        ))

    return buy_levels, sell_levels


def generate_full_grid_ladder(entry_price: Decimal, num_levels: int = 25) -> List[Decimal]:
    """Generate a continuous price ladder for snapping.

    Creates levels both above and below entry, centered on entry price.
    This ensures counter-orders can always snap to a valid grid level,
    even if they fall in the "dead zone" between initial buys and sells.
    """
    spacing = GRID_CONFIG["level_spacing_pct"]
    ladder = [entry_price.quantize(Decimal("0.0001"))]

    # Generate levels below entry
    for i in range(1, num_levels + 1):
        price = entry_price * ((1 - spacing) ** i)
        ladder.append(price.quantize(Decimal("0.0001")))

    # Generate levels above entry
    for i in range(1, num_levels + 1):
        price = entry_price * ((1 + spacing) ** i)
        ladder.append(price.quantize(Decimal("0.0001")))

    return sorted(ladder)


# Legacy function for backward compatibility
def generate_grid_pairs(entry_price: Decimal) -> List[GridPair]:
    """Legacy function - generates paired levels for backward compatibility."""
    buy_levels, sell_levels = generate_grid_levels(entry_price)

    # Create pairs by matching buy and sell levels
    pairs = []
    spread = GRID_CONFIG["cycle_spread_pct"]

    for i, buy in enumerate(buy_levels):
        sell = sell_levels[i] if i < len(sell_levels) else None
        pairs.append(GridPair(
            pair_id=i + 1,
            buy_price=buy.price,
            sell_price=sell.price if sell else buy.price * (1 + spread),
            spread_pct=spread,
            usdc_size=GRID_CONFIG["total_buy_usdc"] / GRID_CONFIG["num_buy_levels"],
            lit_size=sell.size if sell else buy.size,  # Use sell level's size for initial sells
        ))

    return pairs


# Grid Parameters (legacy - for compatibility)
GRID_SPREAD = Decimal("0.02")  # Must match level_spacing_pct
GRID_PROFIT_RETAIN = GRID_CONFIG["profit_retention"]

# Hedge Parameters - DISABLED (capital moved to grid buys)
HEDGE_CONFIG = {
    "enabled": False,  # Hedge disabled - ranging market, avoid repeated stop-outs
    "short_size": Decimal("0"),
    "margin_usdc": Decimal("0"),
    "leverage": Decimal("4"),
    "stop_loss_pct": Decimal("0.16"),
    "reentry_pullback_pct": Decimal("0.05"),
    "re_entry_cooldown_hours": 24,
    "pause_if_negative_funding_hours": 24,
}

# Floor Protection - Value-based only (no price tiers)
FLOOR_CONFIG = {
    "floor_value": Decimal("25000"),
    "emergency_buffer": Decimal("25500"),  # Trigger emergency at $25.5k portfolio value
}

# Core Position Sell Targets (limit orders at startup)
# Total: 11,175 LIT (original 8,000 + 3,175 from reserve)
CORE_TARGETS = [
    {"price": Decimal("2.00"), "lit": Decimal("1500")},   # New tier
    {"price": Decimal("2.25"), "lit": Decimal("1675")},   # New tier
    {"price": Decimal("2.50"), "lit": Decimal("1000")},
    {"price": Decimal("3.00"), "lit": Decimal("1500")},
    {"price": Decimal("3.50"), "lit": Decimal("2000")},
    {"price": Decimal("4.00"), "lit": Decimal("2000")},
    {"price": Decimal("4.50"), "lit": Decimal("1500")},
]

# Limits
LIMITS = {
    "max_lit": Decimal("22000"),
    "min_lit": Decimal("8000"),
    "max_short": Decimal("3000"),
    "min_usdc": Decimal("800"),
}
