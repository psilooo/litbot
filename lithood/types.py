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
    IN_PROGRESS = "in-progress"
    PENDING = "pending"
    OPEN = "open"
    FILLED = "filled"
    CANCELLED = "canceled"  # API uses "canceled" spelling
    PARTIALLY_FILLED = "partially_filled"
    # Cancellation reasons
    CANCELED_POST_ONLY = "canceled-post-only"
    CANCELED_TOO_MUCH_SLIPPAGE = "canceled-too-much-slippage"
    CANCELED_IOC = "canceled-ioc"
    CANCELED_FOK = "canceled-fok"
    CANCELED_REDUCE_ONLY = "canceled-reduce-only"
    CANCELED_SELF_TRADE = "canceled-self-trade"
    CANCELED_NOT_ENOUGH_MARGIN = "canceled-not-enough-margin"
    CANCELED_BY_USER = "canceled-by-user"

    @classmethod
    def from_value(cls, value):
        """Convert value to OrderStatus, handling old string values."""
        # Handle old "cancelled" spelling
        if value == "cancelled":
            value = "canceled"
        return cls(value)


class OrderType(Enum):
    LIMIT = "limit"
    MARKET = "market"
    STOP_LOSS = "stop-loss"
    STOP_LOSS_LIMIT = "stop-loss-limit"
    TAKE_PROFIT = "take-profit"
    TAKE_PROFIT_LIMIT = "take-profit-limit"

    @classmethod
    def from_value(cls, value):
        """Convert value to OrderType, handling old integer values."""
        # Map old integer values to new string values
        int_to_str = {0: "limit", 1: "market", 2: "stop-loss", 3: "stop-loss-limit", 4: "take-profit", 5: "take-profit-limit"}
        if isinstance(value, int):
            value = int_to_str.get(value, "limit")
        return cls(value)


class TimeInForce(Enum):
    GTC = "good-till-time"
    IOC = "immediate-or-cancel"
    POST_ONLY = "post-only"

    @classmethod
    def from_value(cls, value):
        """Convert value to TimeInForce, handling old integer values."""
        int_to_str = {0: "immediate-or-cancel", 1: "good-till-time", 2: "post-only"}
        if isinstance(value, int):
            value = int_to_str.get(value, "good-till-time")
        return cls(value)


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
    id: str  # order_index from exchange (used for cancellation)
    market_id: int
    side: OrderSide
    price: Decimal
    size: Decimal
    status: OrderStatus
    order_type: OrderType
    tx_hash: Optional[str] = None  # Transaction hash from order placement
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
class AssetBalance:
    """Spot asset balance."""
    asset_id: int
    balance: Decimal  # Available balance
    locked_balance: Decimal  # In open orders


@dataclass
class Account:
    """Account balance information."""
    index: int
    l1_address: str
    collateral: Decimal
    available_balance: Decimal
    positions: list[Position] = field(default_factory=list)
    assets: list[AssetBalance] = field(default_factory=list)  # Spot balances
    total_asset_value: Decimal = Decimal("0")  # Total portfolio value from exchange


@dataclass
class FundingRate:
    """Funding rate information."""
    market_id: int
    rate: Decimal  # Hourly rate
    timestamp: datetime
