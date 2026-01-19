# lithood/types.py
"""Data types for the trading bot."""

from dataclasses import dataclass, field
from decimal import Decimal
from datetime import datetime
from enum import Enum
from typing import Optional


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(Enum):
    PENDING = "pending"
    FILLED = "filled"
    CANCELLED = "cancelled"
    PARTIALLY_FILLED = "partially_filled"


class OrderType(Enum):
    LIMIT = 0
    MARKET = 1


class TimeInForce(Enum):
    IOC = 0  # Immediate or cancel
    GTC = 1  # Good till time
    POST_ONLY = 2


class MarketType(Enum):
    SPOT = "spot"
    PERP = "perp"


@dataclass
class Market:
    """Market information from exchange."""
    symbol: str
    market_id: int
    market_type: MarketType
    base_asset_id: int
    quote_asset_id: int
    min_base_amount: Decimal
    min_quote_amount: Decimal
    size_decimals: int
    price_decimals: int
    taker_fee: Decimal
    maker_fee: Decimal


@dataclass
class Order:
    """Order information."""
    id: str
    market_id: int
    side: OrderSide
    price: Decimal
    size: Decimal
    status: OrderStatus
    order_type: OrderType
    grid_level: Optional[int] = None
    created_at: datetime = field(default_factory=datetime.now)
    filled_at: Optional[datetime] = None
    filled_size: Decimal = Decimal("0")


@dataclass
class Position:
    """Perp position information."""
    market_id: int
    size: Decimal  # Negative for short
    entry_price: Decimal
    unrealized_pnl: Decimal
    liquidation_price: Optional[Decimal] = None


@dataclass
class Account:
    """Account balance information."""
    index: int
    l1_address: str
    collateral: Decimal
    available_balance: Decimal
    positions: list[Position] = field(default_factory=list)


@dataclass
class FundingRate:
    """Funding rate information."""
    market_id: int
    rate: Decimal  # Hourly rate
    timestamp: datetime
