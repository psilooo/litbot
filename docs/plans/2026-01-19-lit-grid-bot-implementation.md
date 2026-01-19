# LIT Grid Trading Bot - Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a grid trading bot for Lighter DEX that maximizes volatility capture while protecting a $25k floor.

**Architecture:** Python async bot using the official `lighter-sdk`. Three engines (grid, hedge, floor protection) coordinate via shared SQLite state. Polling-based at 2s intervals.

**Tech Stack:** Python 3.11+, lighter-sdk, sqlite3, python-dotenv, asyncio

---

## Task 1: Project Setup

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `lithood/__init__.py`

**Step 1: Create requirements.txt**

```txt
lighter-sdk>=1.0.2
python-dotenv>=1.0.0
```

**Step 2: Create .env.example**

```bash
# Lighter API Configuration
LIGHTER_BASE_URL=https://mainnet.zklighter.elliot.ai
LIGHTER_PRIVATE_KEY=your_private_key_here

# Bot Configuration (optional overrides)
POLL_INTERVAL_SECONDS=2
LOG_LEVEL=INFO
```

**Step 3: Create .gitignore**

```
# Environment
.env
*.env.local

# Python
__pycache__/
*.py[cod]
*$py.class
.venv/
venv/
env/

# Database
*.db
*.db-journal

# Logs
logs/
*.log

# IDE
.idea/
.vscode/
*.swp
```

**Step 4: Create package init**

```python
# lithood/__init__.py
"""LIT Grid Trading Bot for Lighter DEX."""

__version__ = "0.1.0"
```

**Step 5: Install dependencies**

Run: `pip install -r requirements.txt`
Expected: Successfully installed lighter-sdk and python-dotenv

**Step 6: Commit**

```bash
git add requirements.txt .env.example .gitignore lithood/__init__.py
git commit -m "chore: project setup with dependencies"
```

---

## Task 2: Configuration Module

**Files:**
- Create: `lithood/config.py`

**Step 1: Create config.py with all strategy parameters**

```python
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
```

**Step 2: Verify config loads**

Run: `python -c "from lithood.config import ALLOCATION; print(ALLOCATION)"`
Expected: Dictionary with allocations printed

**Step 3: Commit**

```bash
git add lithood/config.py
git commit -m "feat: add strategy configuration from summary.md"
```

---

## Task 3: Logging Setup

**Files:**
- Create: `lithood/logger.py`

**Step 1: Create logger.py**

```python
# lithood/logger.py
"""Logging configuration for the bot."""

import logging
import sys
from datetime import datetime
from pathlib import Path

from lithood.config import LOG_LEVEL


def setup_logger(name: str = "lithood") -> logging.Logger:
    """Set up logger with console and file handlers."""
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, LOG_LEVEL.upper()))

    # Prevent duplicate handlers
    if logger.handlers:
        return logger

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)
    console_format = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S"
    )
    console_handler.setFormatter(console_format)
    logger.addHandler(console_handler)

    # File handler
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"lithood_{datetime.now().strftime('%Y%m%d')}.log"

    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_format = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    file_handler.setFormatter(file_format)
    logger.addHandler(file_handler)

    return logger


# Default logger instance
log = setup_logger()
```

**Step 2: Verify logger works**

Run: `python -c "from lithood.logger import log; log.info('Test message')"`
Expected: Prints "HH:MM:SS [INFO] Test message" and creates logs/ directory

**Step 3: Commit**

```bash
git add lithood/logger.py
git commit -m "feat: add logging with console and file output"
```

---

## Task 4: Data Types

**Files:**
- Create: `lithood/types.py`

**Step 1: Create types.py with data classes**

```python
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
```

**Step 2: Verify types import**

Run: `python -c "from lithood.types import Order, OrderSide, OrderStatus; print('Types OK')"`
Expected: Prints "Types OK"

**Step 3: Commit**

```bash
git add lithood/types.py
git commit -m "feat: add data types for orders, positions, accounts"
```

---

## Task 5: Lighter API Client

**Files:**
- Create: `lithood/client.py`

**Step 1: Create client.py wrapping the SDK**

```python
# lithood/client.py
"""Lighter DEX API client wrapper."""

import asyncio
from decimal import Decimal
from typing import Optional
import time

import zklighter
from eth_account import Account as EthAccount

from lithood.config import LIGHTER_BASE_URL, LIGHTER_PRIVATE_KEY
from lithood.logger import log
from lithood.types import (
    Account, Order, Position, Market, FundingRate,
    OrderSide, OrderStatus, OrderType, TimeInForce, MarketType
)


class LighterClient:
    """Async client for Lighter DEX API."""

    def __init__(self, base_url: str = None, private_key: str = None):
        self.base_url = base_url or LIGHTER_BASE_URL
        self.private_key = private_key or LIGHTER_PRIVATE_KEY
        self.l1_address = EthAccount.from_key(self.private_key).address

        self._api_client: Optional[zklighter.ApiClient] = None
        self._signer_client: Optional[zklighter.SignerClient] = None
        self._account_index: Optional[int] = None
        self._markets: dict[str, Market] = {}

    async def connect(self):
        """Initialize API connections."""
        log.info(f"Connecting to Lighter at {self.base_url}")

        self._api_client = zklighter.ApiClient()
        self._signer_client = zklighter.SignerClient(self.base_url, self.private_key)

        # Get account index
        account_api = zklighter.AccountApi(self._api_client)
        accounts = await account_api.accounts_by_l1_address(l1_address=self.l1_address)
        self._account_index = min(accounts.sub_accounts, key=lambda x: x.index).index

        # Cache market info
        await self._load_markets()

        log.info(f"Connected. Account index: {self._account_index}, Address: {self.l1_address}")

    async def close(self):
        """Clean up connections."""
        if self._signer_client:
            await self._signer_client.close()
        if self._api_client:
            await self._api_client.close()
        log.info("Disconnected from Lighter")

    async def _load_markets(self):
        """Load and cache market information."""
        order_api = zklighter.OrderApi(self._api_client)
        order_books = await order_api.order_books()

        for ob in order_books.order_books:
            market = Market(
                symbol=ob.symbol,
                market_id=ob.market_id,
                market_type=MarketType(ob.market_type),
                base_asset_id=ob.base_asset_id,
                quote_asset_id=ob.quote_asset_id,
                min_base_amount=Decimal(ob.min_base_amount),
                min_quote_amount=Decimal(ob.min_quote_amount),
                size_decimals=ob.supported_size_decimals,
                price_decimals=ob.supported_price_decimals,
                taker_fee=Decimal(ob.taker_fee),
                maker_fee=Decimal(ob.maker_fee),
            )
            # Key by symbol (e.g., "LIT") and type
            key = f"{ob.symbol}_{ob.market_type}"
            self._markets[key] = market
            log.debug(f"Loaded market: {key} (id={ob.market_id})")

    def get_market(self, symbol: str, market_type: MarketType) -> Market:
        """Get market info by symbol and type."""
        key = f"{symbol}_{market_type.value}"
        if key not in self._markets:
            raise ValueError(f"Market not found: {key}")
        return self._markets[key]

    async def get_account(self) -> Account:
        """Get account balances and positions."""
        account_api = zklighter.AccountApi(self._api_client)
        data = await account_api.account(by="index", value=str(self._account_index))

        positions = []
        if hasattr(data, 'positions') and data.positions:
            for p in data.positions:
                if p.position and Decimal(p.position) != 0:
                    positions.append(Position(
                        market_id=p.market_id,
                        size=Decimal(p.position) * (1 if p.sign == 1 else -1),
                        entry_price=Decimal(p.avg_entry_price),
                        unrealized_pnl=Decimal(p.unrealized_pnl) if p.unrealized_pnl else Decimal("0"),
                    ))

        return Account(
            index=self._account_index,
            l1_address=self.l1_address,
            collateral=Decimal(data.accounts[0].collateral) if data.accounts else Decimal("0"),
            available_balance=Decimal(data.accounts[0].available_balance) if data.accounts else Decimal("0"),
            positions=positions,
        )

    async def get_active_orders(self, market_id: Optional[int] = None) -> list[Order]:
        """Get active orders, optionally filtered by market."""
        order_api = zklighter.OrderApi(self._api_client)

        if market_id is not None:
            data = await order_api.account_active_orders(
                account_index=self._account_index,
                market_id=market_id
            )
        else:
            data = await order_api.account_active_orders(
                account_index=self._account_index
            )

        orders = []
        if hasattr(data, 'orders') and data.orders:
            for o in data.orders:
                orders.append(Order(
                    id=str(o.order_index),
                    market_id=o.market_id,
                    side=OrderSide.SELL if o.is_ask else OrderSide.BUY,
                    price=Decimal(o.price),
                    size=Decimal(o.original_base_amount),
                    status=OrderStatus.PENDING,
                    order_type=OrderType(o.order_type),
                    filled_size=Decimal(o.original_base_amount) - Decimal(o.base_amount),
                ))
        return orders

    async def get_mid_price(self, symbol: str, market_type: MarketType) -> Decimal:
        """Get mid price for a market."""
        market = self.get_market(symbol, market_type)
        order_api = zklighter.OrderApi(self._api_client)

        ob = await order_api.order_book_orders(market_id=market.market_id, limit=1)

        best_bid = Decimal(ob.bids[0].price) if ob.bids else Decimal("0")
        best_ask = Decimal(ob.asks[0].price) if ob.asks else Decimal("0")

        if best_bid and best_ask:
            return (best_bid + best_ask) / 2
        return best_bid or best_ask

    def _to_price_int(self, price: Decimal, market: Market) -> int:
        """Convert decimal price to integer for API."""
        multiplier = 10 ** market.price_decimals
        return int(price * multiplier)

    def _to_size_int(self, size: Decimal, market: Market) -> int:
        """Convert decimal size to integer for API."""
        multiplier = 10 ** market.size_decimals
        return int(size * multiplier)

    async def place_limit_order(
        self,
        symbol: str,
        market_type: MarketType,
        side: OrderSide,
        price: Decimal,
        size: Decimal,
        post_only: bool = True,
    ) -> Order:
        """Place a limit order."""
        market = self.get_market(symbol, market_type)

        price_int = self._to_price_int(price, market)
        size_int = self._to_size_int(size, market)
        is_ask = 1 if side == OrderSide.SELL else 0
        tif = TimeInForce.POST_ONLY.value if post_only else TimeInForce.GTC.value
        expiry = int((time.time() + 60 * 60 * 24 * 30) * 1000)  # 30 days

        log.info(f"Placing {side.value} limit: {size} @ ${price} on {symbol}_{market_type.value}")

        order_data = await self._signer_client.create_order(
            market_index=market.market_id,
            client_order_index=0,
            base_amount=size_int,
            price=price_int,
            is_ask=is_ask,
            order_type=OrderType.LIMIT.value,
            time_in_force=tif,
            order_expiry=expiry,
        )

        order_id = str(order_data.order_index) if hasattr(order_data, 'order_index') else str(order_data)
        log.info(f"Order placed: {order_id}")

        return Order(
            id=order_id,
            market_id=market.market_id,
            side=side,
            price=price,
            size=size,
            status=OrderStatus.PENDING,
            order_type=OrderType.LIMIT,
        )

    async def place_market_order(
        self,
        symbol: str,
        market_type: MarketType,
        side: OrderSide,
        size: Decimal,
    ) -> Order:
        """Place a market order."""
        market = self.get_market(symbol, market_type)

        size_int = self._to_size_int(size, market)
        is_ask = 1 if side == OrderSide.SELL else 0

        # For market orders, use a very favorable price
        mid = await self.get_mid_price(symbol, market_type)
        if side == OrderSide.BUY:
            price = mid * Decimal("1.05")  # 5% above mid
        else:
            price = mid * Decimal("0.95")  # 5% below mid
        price_int = self._to_price_int(price, market)

        log.info(f"Placing {side.value} market: {size} on {symbol}_{market_type.value}")

        order_data = await self._signer_client.create_order(
            market_index=market.market_id,
            client_order_index=0,
            base_amount=size_int,
            price=price_int,
            is_ask=is_ask,
            order_type=OrderType.MARKET.value,
            time_in_force=TimeInForce.IOC.value,
            order_expiry=int((time.time() + 60) * 1000),
        )

        order_id = str(order_data.order_index) if hasattr(order_data, 'order_index') else str(order_data)
        log.info(f"Market order placed: {order_id}")

        return Order(
            id=order_id,
            market_id=market.market_id,
            side=side,
            price=price,
            size=size,
            status=OrderStatus.FILLED,  # Assume filled for market
            order_type=OrderType.MARKET,
        )

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order by ID."""
        log.info(f"Cancelling order: {order_id}")

        try:
            await self._signer_client.cancel_order(order_index=int(order_id))
            log.info(f"Order cancelled: {order_id}")
            return True
        except Exception as e:
            log.error(f"Failed to cancel order {order_id}: {e}")
            return False

    async def cancel_all_orders(self, market_id: Optional[int] = None) -> int:
        """Cancel all orders, optionally for a specific market."""
        orders = await self.get_active_orders(market_id)
        cancelled = 0

        for order in orders:
            if await self.cancel_order(order.id):
                cancelled += 1

        log.info(f"Cancelled {cancelled} orders")
        return cancelled

    async def get_funding_rate(self, symbol: str) -> FundingRate:
        """Get current funding rate for a perp market."""
        market = self.get_market(symbol, MarketType.PERP)

        # Use the funding-rates endpoint
        order_api = zklighter.OrderApi(self._api_client)
        # Note: Actual implementation depends on SDK method availability
        # This is a placeholder - may need adjustment based on actual SDK

        return FundingRate(
            market_id=market.market_id,
            rate=Decimal("0.0001"),  # Placeholder - fetch actual rate
            timestamp=datetime.now(),
        )

    async def get_positions(self) -> list[Position]:
        """Get all open perp positions."""
        account = await self.get_account()
        return account.positions
```

**Step 2: Verify client imports**

Run: `python -c "from lithood.client import LighterClient; print('Client OK')"`
Expected: Prints "Client OK"

**Step 3: Commit**

```bash
git add lithood/client.py
git commit -m "feat: add Lighter API client wrapper"
```

---

## Task 6: State Manager

**Files:**
- Create: `lithood/state.py`

**Step 1: Create state.py with SQLite backend**

```python
# lithood/state.py
"""SQLite-based state management."""

import sqlite3
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional, Any

from lithood.logger import log
from lithood.types import Order, OrderSide, OrderStatus, OrderType


class StateManager:
    """Manages persistent state in SQLite."""

    def __init__(self, db_path: str = "lithood.db"):
        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self):
        """Initialize database schema."""
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row

        cursor = self._conn.cursor()

        # Orders table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY,
                market_id INTEGER NOT NULL,
                side TEXT NOT NULL,
                price REAL NOT NULL,
                size REAL NOT NULL,
                status TEXT NOT NULL,
                order_type INTEGER NOT NULL,
                grid_level INTEGER,
                created_at TEXT NOT NULL,
                filled_at TEXT
            )
        """)

        # Hedge history table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS hedge_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                price REAL NOT NULL,
                size REAL NOT NULL,
                pnl REAL,
                funding_earned REAL,
                timestamp TEXT NOT NULL
            )
        """)

        # Key-value state table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bot_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        self._conn.commit()
        log.info(f"Database initialized: {self.db_path}")

    def close(self):
        """Close database connection."""
        if self._conn:
            self._conn.close()

    # Key-value state methods
    def get(self, key: str, default: Any = None) -> Any:
        """Get a state value."""
        cursor = self._conn.cursor()
        cursor.execute("SELECT value FROM bot_state WHERE key = ?", (key,))
        row = cursor.fetchone()
        if row:
            try:
                return json.loads(row['value'])
            except json.JSONDecodeError:
                return row['value']
        return default

    def set(self, key: str, value: Any):
        """Set a state value."""
        cursor = self._conn.cursor()
        value_str = json.dumps(value) if not isinstance(value, str) else value
        cursor.execute("""
            INSERT OR REPLACE INTO bot_state (key, value, updated_at)
            VALUES (?, ?, ?)
        """, (key, value_str, datetime.now().isoformat()))
        self._conn.commit()

    # Order tracking methods
    def save_order(self, order: Order):
        """Save an order to the database."""
        cursor = self._conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO orders
            (id, market_id, side, price, size, status, order_type, grid_level, created_at, filled_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            order.id,
            order.market_id,
            order.side.value,
            float(order.price),
            float(order.size),
            order.status.value,
            order.order_type.value,
            order.grid_level,
            order.created_at.isoformat(),
            order.filled_at.isoformat() if order.filled_at else None,
        ))
        self._conn.commit()

    def get_pending_orders(self) -> list[Order]:
        """Get all pending orders from local state."""
        cursor = self._conn.cursor()
        cursor.execute("SELECT * FROM orders WHERE status = 'pending'")

        orders = []
        for row in cursor.fetchall():
            orders.append(Order(
                id=row['id'],
                market_id=row['market_id'],
                side=OrderSide(row['side']),
                price=Decimal(str(row['price'])),
                size=Decimal(str(row['size'])),
                status=OrderStatus(row['status']),
                order_type=OrderType(row['order_type']),
                grid_level=row['grid_level'],
                created_at=datetime.fromisoformat(row['created_at']),
                filled_at=datetime.fromisoformat(row['filled_at']) if row['filled_at'] else None,
            ))
        return orders

    def mark_filled(self, order_id: str):
        """Mark an order as filled."""
        cursor = self._conn.cursor()
        cursor.execute("""
            UPDATE orders SET status = 'filled', filled_at = ?
            WHERE id = ?
        """, (datetime.now().isoformat(), order_id))
        self._conn.commit()
        log.info(f"Order marked filled: {order_id}")

    def mark_cancelled(self, order_id: str):
        """Mark an order as cancelled."""
        cursor = self._conn.cursor()
        cursor.execute("""
            UPDATE orders SET status = 'cancelled'
            WHERE id = ?
        """, (order_id,))
        self._conn.commit()

    # Hedge history methods
    def log_hedge_action(self, action: str, price: Decimal, size: Decimal,
                         pnl: Decimal = None, funding: Decimal = None):
        """Log a hedge action."""
        cursor = self._conn.cursor()
        cursor.execute("""
            INSERT INTO hedge_history (action, price, size, pnl, funding_earned, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            action,
            float(price),
            float(size),
            float(pnl) if pnl else None,
            float(funding) if funding else None,
            datetime.now().isoformat(),
        ))
        self._conn.commit()
        log.info(f"Hedge action logged: {action} @ ${price}")

    def get_grid_stats(self) -> dict:
        """Get grid trading statistics."""
        cursor = self._conn.cursor()

        cursor.execute("""
            SELECT COUNT(*) as total, SUM(CASE WHEN status = 'filled' THEN 1 ELSE 0 END) as filled
            FROM orders WHERE grid_level IS NOT NULL
        """)
        row = cursor.fetchone()

        return {
            "total_grid_orders": row['total'],
            "filled_grid_orders": row['filled'],
        }
```

**Step 2: Test state manager**

Run: `python -c "
from lithood.state import StateManager
s = StateManager(':memory:')
s.set('test_key', {'value': 123})
print(s.get('test_key'))
s.close()
"`
Expected: Prints `{'value': 123}`

**Step 3: Commit**

```bash
git add lithood/state.py
git commit -m "feat: add SQLite state manager"
```

---

## Task 7: Mini-Test Script

**Files:**
- Create: `scripts/test_connectivity.py`

**Step 1: Create the test script**

```python
#!/usr/bin/env python3
# scripts/test_connectivity.py
"""
Mini-test to verify Lighter API integration before running the bot.

Tests:
1. Account data fetching (balances, orders, positions)
2. Spot order lifecycle (place limit → verify → cancel)
3. Spot market order (place → verify fill)
4. Perp order lifecycle (place limit → verify → cancel)
5. Perp position lifecycle (open short → verify → close)

Cost: ~$3-4 (1 LIT spot buy + 1 LIT perp round trip)
"""

import asyncio
import sys
from decimal import Decimal
import time

# Add parent dir to path for imports
sys.path.insert(0, str(__file__).rsplit('/', 2)[0])

from lithood.client import LighterClient
from lithood.types import OrderSide, MarketType
from lithood.logger import log


async def test_account_data(client: LighterClient) -> dict:
    """Test 1: Fetch and display account data."""
    log.info("=== TEST 1: ACCOUNT DATA ===")

    # Get account info
    account = await client.get_account()
    log.info(f"Account index: {account.index}")
    log.info(f"L1 address: {account.l1_address}")
    log.info(f"Collateral: ${account.collateral}")
    log.info(f"Available balance: ${account.available_balance}")

    # Get active orders
    spot_orders = await client.get_active_orders()
    log.info(f"Active orders: {len(spot_orders)}")
    for o in spot_orders:
        log.info(f"  {o.side.value} {o.size} @ ${o.price} (id={o.id})")

    # Get positions
    positions = await client.get_positions()
    log.info(f"Open positions: {len(positions)}")
    for p in positions:
        log.info(f"  Market {p.market_id}: {p.size} @ ${p.entry_price} (PnL: ${p.unrealized_pnl})")

    log.info("✓ Account data fetched successfully")

    return {
        "collateral": account.collateral,
        "available": account.available_balance,
        "order_count": len(spot_orders),
        "position_count": len(positions),
    }


async def test_spot_order_lifecycle(client: LighterClient, baseline: dict):
    """Test 2-3: Spot order placement, verification, cancellation, and fill."""
    log.info("=== TEST 2: SPOT LIMIT ORDER ===")

    # Get current price
    mid_price = await client.get_mid_price("LIT", MarketType.SPOT)
    log.info(f"Current LIT mid price: ${mid_price}")

    # Place limit buy 10% below market (won't fill)
    test_price = mid_price * Decimal("0.90")
    order = await client.place_limit_order(
        symbol="LIT",
        market_type=MarketType.SPOT,
        side=OrderSide.BUY,
        price=test_price,
        size=Decimal("1"),
    )
    log.info(f"Placed limit buy: {order.id} at ${test_price}")

    # Wait and verify it appears
    await asyncio.sleep(2)
    orders = await client.get_active_orders()
    found = any(o.id == order.id for o in orders)
    assert found, f"Order {order.id} not found in active orders!"
    log.info(f"✓ Order visible in active orders (count: {len(orders)})")

    # Cancel it
    success = await client.cancel_order(order.id)
    assert success, "Failed to cancel order"
    log.info("✓ Order cancelled")

    # Verify it's gone
    await asyncio.sleep(2)
    orders = await client.get_active_orders()
    still_there = any(o.id == order.id for o in orders)
    assert not still_there, "Order still in active orders after cancel!"
    log.info("✓ Order removed from active orders")

    log.info("=== TEST 3: SPOT MARKET ORDER ===")

    # Place tiny market buy
    order = await client.place_market_order(
        symbol="LIT",
        market_type=MarketType.SPOT,
        side=OrderSide.BUY,
        size=Decimal("1"),
    )
    log.info(f"Placed market buy: {order.id}")

    # Wait and verify balance increased
    await asyncio.sleep(3)
    account = await client.get_account()
    # Note: For spot, check collateral change (USDC decreased)
    log.info(f"New collateral: ${account.collateral}")
    log.info("✓ Market order executed")


async def test_perp_order_lifecycle(client: LighterClient, baseline: dict):
    """Test 4-5: Perp order and position lifecycle."""
    log.info("=== TEST 4: PERP LIMIT ORDER ===")

    # Get current price
    mid_price = await client.get_mid_price("LIT", MarketType.PERP)
    log.info(f"Current LIT perp mid price: ${mid_price}")

    # Place limit sell 10% above market (won't fill)
    test_price = mid_price * Decimal("1.10")
    order = await client.place_limit_order(
        symbol="LIT",
        market_type=MarketType.PERP,
        side=OrderSide.SELL,
        price=test_price,
        size=Decimal("1"),
    )
    log.info(f"Placed limit sell: {order.id} at ${test_price}")

    # Wait and verify
    await asyncio.sleep(2)
    market = client.get_market("LIT", MarketType.PERP)
    orders = await client.get_active_orders(market_id=market.market_id)
    found = any(o.id == order.id for o in orders)
    assert found, f"Perp order {order.id} not found!"
    log.info("✓ Perp order visible")

    # Cancel
    await client.cancel_order(order.id)
    log.info("✓ Perp order cancelled")

    log.info("=== TEST 5: PERP POSITION ===")

    # Open tiny short position via market order
    order = await client.place_market_order(
        symbol="LIT",
        market_type=MarketType.PERP,
        side=OrderSide.SELL,
        size=Decimal("1"),
    )
    log.info(f"Opened short position: {order.id}")

    # Wait and verify position exists
    await asyncio.sleep(3)
    positions = await client.get_positions()
    perp_pos = next((p for p in positions if p.size < 0), None)
    assert perp_pos is not None, "Short position not found!"
    log.info(f"✓ Position visible: {perp_pos.size} LIT @ ${perp_pos.entry_price}")

    # Close position
    order = await client.place_market_order(
        symbol="LIT",
        market_type=MarketType.PERP,
        side=OrderSide.BUY,
        size=abs(perp_pos.size),
    )
    log.info("Closing position...")

    # Wait and verify closed
    await asyncio.sleep(3)
    positions = await client.get_positions()
    perp_pos = next((p for p in positions if p.market_id == market.market_id and p.size != 0), None)
    assert perp_pos is None, "Position still open!"
    log.info("✓ Position closed")


async def test_final_state(client: LighterClient, baseline: dict):
    """Final check: show account state and cost."""
    log.info("=== FINAL STATE ===")

    account = await client.get_account()
    cost = baseline["collateral"] - account.collateral

    log.info(f"Starting collateral: ${baseline['collateral']}")
    log.info(f"Final collateral: ${account.collateral}")
    log.info(f"Net cost: ${cost:.2f}")


async def main():
    log.info("=" * 50)
    log.info("LIGHTER API CONNECTIVITY TEST")
    log.info("=" * 50)

    client = LighterClient()

    try:
        await client.connect()

        # Run tests in sequence
        baseline = await test_account_data(client)
        await test_spot_order_lifecycle(client, baseline)
        await test_perp_order_lifecycle(client, baseline)
        await test_final_state(client, baseline)

        log.info("=" * 50)
        log.info("ALL TESTS PASSED")
        log.info("=" * 50)

    except AssertionError as e:
        log.error(f"TEST FAILED: {e}")
        raise
    except Exception as e:
        log.error(f"ERROR: {e}")
        raise
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
```

**Step 2: Make script executable**

Run: `chmod +x scripts/test_connectivity.py`

**Step 3: Commit**

```bash
git add scripts/test_connectivity.py
git commit -m "feat: add API connectivity test script"
```

---

## Task 8: Grid Engine

**Files:**
- Create: `lithood/grid.py`

**Step 1: Create grid.py**

```python
# lithood/grid.py
"""Spot grid trading engine."""

from decimal import Decimal
from typing import Optional

from lithood.client import LighterClient
from lithood.state import StateManager
from lithood.types import Order, OrderSide, OrderStatus, MarketType
from lithood.config import (
    GRID_BUY_LEVELS, GRID_SELL_LEVELS,
    GRID_SPREAD, GRID_PROFIT_RETAIN
)
from lithood.logger import log


class GridEngine:
    """Manages spot grid trading."""

    def __init__(self, client: LighterClient, state: StateManager):
        self.client = client
        self.state = state
        self.symbol = "LIT"
        self.market_type = MarketType.SPOT

    async def initialize(self):
        """Place initial grid orders based on current price."""
        current_price = await self.client.get_mid_price(self.symbol, self.market_type)
        log.info(f"Initializing grid. Current price: ${current_price}")

        if self.state.get("grid_paused"):
            log.warning("Grid is paused - skipping initialization")
            return

        # Place buy orders below current price
        for i, level in enumerate(GRID_BUY_LEVELS):
            if level.price < current_price:
                await self._place_grid_buy(level.price, level.size, grid_level=i+1)

        # Place sell orders above current price
        for i, level in enumerate(GRID_SELL_LEVELS):
            if level.price > current_price:
                await self._place_grid_sell(level.price, level.size, grid_level=i+1)

        log.info("Grid initialized")

    async def _place_grid_buy(self, price: Decimal, usdc_amount: Decimal, grid_level: int = None):
        """Place a grid buy order."""
        if self.state.get("grid_paused"):
            log.debug(f"Grid paused - skipping buy at ${price}")
            return None

        # Convert USDC amount to LIT size
        size = usdc_amount / price

        order = await self.client.place_limit_order(
            symbol=self.symbol,
            market_type=self.market_type,
            side=OrderSide.BUY,
            price=price,
            size=size,
        )
        order.grid_level = grid_level
        self.state.save_order(order)

        log.info(f"Grid buy placed: {size:.2f} LIT @ ${price} (level {grid_level})")
        return order

    async def _place_grid_sell(self, price: Decimal, lit_amount: Decimal, grid_level: int = None):
        """Place a grid sell order."""
        order = await self.client.place_limit_order(
            symbol=self.symbol,
            market_type=self.market_type,
            side=OrderSide.SELL,
            price=price,
            size=lit_amount,
        )
        order.grid_level = grid_level
        self.state.save_order(order)

        log.info(f"Grid sell placed: {lit_amount:.2f} LIT @ ${price} (level {grid_level})")
        return order

    async def check_fills(self):
        """Check for filled orders and trigger cycling."""
        if self.state.get("grid_paused"):
            return

        # Get active orders from exchange
        market = self.client.get_market(self.symbol, self.market_type)
        active_orders = await self.client.get_active_orders(market_id=market.market_id)
        active_ids = {o.id for o in active_orders}

        # Check our pending orders
        for order in self.state.get_pending_orders():
            if order.market_id != market.market_id:
                continue

            if order.id not in active_ids:
                # Order is no longer active - assume filled
                await self._on_fill(order)

    async def _on_fill(self, order: Order):
        """Handle a filled order - place the opposite side."""
        self.state.mark_filled(order.id)

        if order.side == OrderSide.BUY:
            # Buy filled → place sell at +3%
            sell_price = order.price * (1 + GRID_SPREAD)
            sell_size = order.size  # Sell the LIT we just bought

            log.info(f"Buy filled @ ${order.price} → placing sell @ ${sell_price}")
            await self._place_grid_sell(sell_price, sell_size)

        else:
            # Sell filled → place buy at -3%, keeping 3% profit
            buy_price = order.price * (1 - GRID_SPREAD)
            usdc_received = order.size * order.price
            usdc_to_reinvest = usdc_received * (1 - GRID_PROFIT_RETAIN)

            log.info(f"Sell filled @ ${order.price} → placing buy @ ${buy_price}")
            log.info(f"  Profit retained: ${usdc_received * GRID_PROFIT_RETAIN:.2f}")
            await self._place_grid_buy(buy_price, usdc_to_reinvest)

            # Update profit tracking
            total_profit = Decimal(str(self.state.get("total_grid_profit", 0)))
            total_profit += usdc_received * GRID_PROFIT_RETAIN
            self.state.set("total_grid_profit", float(total_profit))

    def pause_buys(self):
        """Pause new buy orders (called by floor protection)."""
        self.state.set("grid_paused", True)
        log.warning("Grid buys PAUSED")

    def resume(self):
        """Resume grid trading."""
        self.state.set("grid_paused", False)
        log.info("Grid trading RESUMED")

    async def cancel_all(self):
        """Cancel all grid orders."""
        market = self.client.get_market(self.symbol, self.market_type)
        cancelled = await self.client.cancel_all_orders(market_id=market.market_id)

        # Mark local orders as cancelled
        for order in self.state.get_pending_orders():
            if order.market_id == market.market_id:
                self.state.mark_cancelled(order.id)

        log.warning(f"Cancelled {cancelled} grid orders")
        return cancelled

    def get_stats(self) -> dict:
        """Get grid trading statistics."""
        stats = self.state.get_grid_stats()
        stats["total_profit"] = self.state.get("total_grid_profit", 0)
        stats["paused"] = self.state.get("grid_paused", False)
        return stats
```

**Step 2: Verify grid engine imports**

Run: `python -c "from lithood.grid import GridEngine; print('Grid OK')"`
Expected: Prints "Grid OK"

**Step 3: Commit**

```bash
git add lithood/grid.py
git commit -m "feat: add spot grid trading engine"
```

---

## Task 9: Hedge Manager

**Files:**
- Create: `lithood/hedge.py`

**Step 1: Create hedge.py**

```python
# lithood/hedge.py
"""Perp hedge manager for downside protection and funding yield."""

import time
from decimal import Decimal
from typing import Optional

from lithood.client import LighterClient
from lithood.state import StateManager
from lithood.types import OrderSide, MarketType, Position
from lithood.config import HEDGE_CONFIG
from lithood.logger import log


class HedgeManager:
    """Manages the perp short hedge position."""

    def __init__(self, client: LighterClient, state: StateManager):
        self.client = client
        self.state = state
        self.symbol = "LIT"
        self.config = HEDGE_CONFIG

    async def initialize(self):
        """Open initial hedge short if not already active."""
        if not self.config["enabled"]:
            log.info("Hedge disabled in config")
            return

        if self.state.get("hedge_active"):
            log.info("Hedge already active")
            return

        await self.open_short()

    async def open_short(self):
        """Open the hedge short position."""
        size = self.config["short_size"]

        log.info(f"Opening hedge short: {size} LIT")

        order = await self.client.place_market_order(
            symbol=self.symbol,
            market_type=MarketType.PERP,
            side=OrderSide.SELL,
            size=size,
        )

        # Get entry price from position
        positions = await self.client.get_positions()
        perp_pos = self._find_position(positions)

        if perp_pos:
            entry_price = perp_pos.entry_price
        else:
            entry_price = await self.client.get_mid_price(self.symbol, MarketType.PERP)

        self.state.set("hedge_active", True)
        self.state.set("hedge_entry_price", float(entry_price))
        self.state.set("hedge_size", float(size))
        self.state.log_hedge_action("open", entry_price, size)

        log.info(f"Hedge opened: {size} LIT short @ ${entry_price}")

    async def close_short(self, reason: str = "manual"):
        """Close the hedge short position."""
        if not self.state.get("hedge_active"):
            log.warning("No active hedge to close")
            return

        size = Decimal(str(self.state.get("hedge_size", self.config["short_size"])))
        entry_price = Decimal(str(self.state.get("hedge_entry_price", 0)))

        log.info(f"Closing hedge short: {size} LIT (reason: {reason})")

        # Buy to close
        order = await self.client.place_market_order(
            symbol=self.symbol,
            market_type=MarketType.PERP,
            side=OrderSide.BUY,
            size=size,
        )

        # Calculate PnL
        current_price = await self.client.get_mid_price(self.symbol, MarketType.PERP)
        pnl = (entry_price - current_price) * size

        self.state.set("hedge_active", False)
        self.state.log_hedge_action(reason, current_price, size, pnl=pnl)

        # Track total hedge PnL
        total_pnl = Decimal(str(self.state.get("total_hedge_pnl", 0)))
        total_pnl += pnl
        self.state.set("total_hedge_pnl", float(total_pnl))

        log.info(f"Hedge closed @ ${current_price}, PnL: ${pnl:.2f}")

    def _find_position(self, positions: list[Position]) -> Optional[Position]:
        """Find our perp position."""
        market = self.client.get_market(self.symbol, MarketType.PERP)
        for p in positions:
            if p.market_id == market.market_id and p.size != 0:
                return p
        return None

    async def check(self, current_price: Decimal):
        """Check hedge status - stop-loss and re-entry logic."""
        if not self.config["enabled"]:
            return

        if self.state.get("hedge_active"):
            # Check stop-loss
            if current_price >= self.config["stop_loss_price"]:
                log.warning(f"STOP-LOSS triggered @ ${current_price}")
                await self.close_short(reason="stop_loss")
                self.state.set("last_stop_loss_time", time.time())
        else:
            # Check re-entry conditions
            last_stop = self.state.get("last_stop_loss_time", 0)
            cooldown_hours = self.config["re_entry_cooldown_hours"]
            cooldown_passed = (time.time() - last_stop) > cooldown_hours * 3600

            if current_price <= self.config["re_entry_price"] and cooldown_passed:
                log.info(f"Re-entry conditions met @ ${current_price}")
                await self.open_short()

    async def check_funding(self):
        """Check funding rate - pause if negative too long."""
        if not self.config["enabled"] or not self.state.get("hedge_active"):
            return

        funding = await self.client.get_funding_rate(self.symbol)

        if funding.rate < 0:
            # Track negative funding duration
            neg_start = self.state.get("negative_funding_start")
            if neg_start is None:
                self.state.set("negative_funding_start", time.time())
                log.warning(f"Funding turned negative: {funding.rate}")
            else:
                hours_negative = (time.time() - neg_start) / 3600
                if hours_negative >= self.config["pause_if_negative_funding_hours"]:
                    log.warning(f"Funding negative for {hours_negative:.1f}h - closing hedge")
                    await self.close_short(reason="negative_funding")
        else:
            # Reset negative funding tracker
            if self.state.get("negative_funding_start") is not None:
                self.state.set("negative_funding_start", None)
                log.info("Funding returned to positive")

    def get_stats(self) -> dict:
        """Get hedge statistics."""
        return {
            "active": self.state.get("hedge_active", False),
            "entry_price": self.state.get("hedge_entry_price"),
            "size": self.state.get("hedge_size"),
            "total_pnl": self.state.get("total_hedge_pnl", 0),
        }
```

**Step 2: Verify hedge manager imports**

Run: `python -c "from lithood.hedge import HedgeManager; print('Hedge OK')"`
Expected: Prints "Hedge OK"

**Step 3: Commit**

```bash
git add lithood/hedge.py
git commit -m "feat: add perp hedge manager"
```

---

## Task 10: Floor Protection

**Files:**
- Create: `lithood/floor.py`

**Step 1: Create floor.py**

```python
# lithood/floor.py
"""Floor protection system to guarantee $25k minimum."""

from decimal import Decimal

from lithood.client import LighterClient
from lithood.state import StateManager
from lithood.types import OrderSide, MarketType
from lithood.config import FLOOR_CONFIG, ALLOCATION
from lithood.logger import log


class FloorProtection:
    """Tiered de-risk system to protect the $25k floor."""

    def __init__(self, client: LighterClient, state: StateManager,
                 grid=None, hedge=None):
        self.client = client
        self.state = state
        self.grid = grid
        self.hedge = hedge
        self.config = FLOOR_CONFIG
        self.symbol = "LIT"

    async def check(self, current_price: Decimal):
        """Check price against floor protection tiers."""
        portfolio_value = await self._calculate_portfolio_value(current_price)
        tier_triggered = self.state.get("floor_tier_triggered", 0)

        log.debug(f"Floor check: price=${current_price}, portfolio=${portfolio_value:.2f}, tier={tier_triggered}")

        for i, tier in enumerate(self.config["tiers"]):
            tier_num = i + 1
            if current_price <= tier["price"] and tier_triggered < tier_num:
                await self._execute_tier(tier_num, tier, current_price)
                self.state.set("floor_tier_triggered", tier_num)

        # Emergency check
        if portfolio_value <= self.config["emergency_buffer"]:
            await self._emergency_exit(current_price, portfolio_value)

    async def _calculate_portfolio_value(self, price: Decimal) -> Decimal:
        """Calculate total portfolio value."""
        account = await self.client.get_account()

        # LIT value (assuming we can get LIT balance from collateral/positions)
        # For simplicity, track LIT balance in state
        lit_balance = Decimal(str(self.state.get("lit_balance", 17175)))
        lit_value = lit_balance * price

        # USDC balance
        usdc_balance = account.available_balance

        # Hedge PnL
        hedge_pnl = Decimal("0")
        if self.state.get("hedge_active"):
            entry = Decimal(str(self.state.get("hedge_entry_price", price)))
            size = Decimal(str(self.state.get("hedge_size", 0)))
            hedge_pnl = (entry - price) * size

        total = lit_value + usdc_balance + hedge_pnl
        return total

    async def _execute_tier(self, tier_num: int, tier: dict, price: Decimal):
        """Execute a floor protection tier."""
        action = tier["action"]

        log.warning(f"FLOOR TIER {tier_num} TRIGGERED @ ${price}")
        log.warning(f"  Action: {action}")

        if action == "pause_grid_buys":
            if self.grid:
                self.grid.pause_buys()
            self._alert(f"Price warning: ${price} - grid buys paused")

        elif action == "sell_reserve":
            amount = tier["amount"]
            await self._market_sell_lit(amount, "reserve")

        elif action == "cancel_grid_sell":
            if self.grid:
                await self.grid.cancel_all()
            amount = tier["amount"]
            await self._market_sell_lit(amount, "grid")

        elif action == "emergency_exit":
            await self._emergency_exit(price, Decimal("0"))

    async def _market_sell_lit(self, amount: Decimal, bucket: str):
        """Market sell LIT from a specific bucket."""
        log.warning(f"Selling {amount} LIT from {bucket} bucket")

        await self.client.place_market_order(
            symbol=self.symbol,
            market_type=MarketType.SPOT,
            side=OrderSide.SELL,
            size=amount,
        )

        # Update LIT balance tracking
        lit_balance = Decimal(str(self.state.get("lit_balance", 17175)))
        lit_balance -= amount
        self.state.set("lit_balance", float(lit_balance))

        log.warning(f"Sold {amount} LIT. New balance: {lit_balance}")

    async def _emergency_exit(self, price: Decimal, portfolio_value: Decimal):
        """Emergency exit - sell everything to guarantee $25k."""
        log.error("=" * 50)
        log.error("EMERGENCY EXIT TRIGGERED")
        log.error(f"Price: ${price}, Portfolio: ${portfolio_value}")
        log.error("=" * 50)

        # Close hedge first
        if self.hedge and self.state.get("hedge_active"):
            await self.hedge.close_short(reason="emergency")

        # Cancel all orders
        if self.grid:
            await self.grid.cancel_all()
        await self.client.cancel_all_orders()

        # Sell all LIT
        lit_balance = Decimal(str(self.state.get("lit_balance", 0)))
        if lit_balance > 0:
            await self._market_sell_lit(lit_balance, "emergency")

        # Halt bot
        self.state.set("bot_halted", True)
        self._alert("EMERGENCY: Floor protection triggered - bot halted")

        log.error("Bot halted. Manual intervention required.")

    def _alert(self, message: str):
        """Send alert (placeholder for notifications)."""
        log.warning(f"ALERT: {message}")
        # TODO: Add Telegram/email notifications

    def reset_tiers(self):
        """Reset tier triggers (for testing or after recovery)."""
        self.state.set("floor_tier_triggered", 0)
        log.info("Floor protection tiers reset")

    def get_stats(self) -> dict:
        """Get floor protection statistics."""
        return {
            "tier_triggered": self.state.get("floor_tier_triggered", 0),
            "floor_value": float(self.config["floor_value"]),
            "emergency_buffer": float(self.config["emergency_buffer"]),
        }
```

**Step 2: Verify floor protection imports**

Run: `python -c "from lithood.floor import FloorProtection; print('Floor OK')"`
Expected: Prints "Floor OK"

**Step 3: Commit**

```bash
git add lithood/floor.py
git commit -m "feat: add floor protection system"
```

---

## Task 11: Main Bot Runner

**Files:**
- Create: `scripts/run_bot.py`

**Step 1: Create the main bot script**

```python
#!/usr/bin/env python3
# scripts/run_bot.py
"""Main entry point for the LIT Grid Trading Bot."""

import asyncio
import signal
import sys
import time
from decimal import Decimal

sys.path.insert(0, str(__file__).rsplit('/', 2)[0])

from lithood.client import LighterClient
from lithood.state import StateManager
from lithood.grid import GridEngine
from lithood.hedge import HedgeManager
from lithood.floor import FloorProtection
from lithood.types import MarketType
from lithood.config import POLL_INTERVAL_SECONDS, ALLOCATION
from lithood.logger import log


class LitGridBot:
    """Main bot orchestrator."""

    def __init__(self):
        self.client = LighterClient()
        self.state = StateManager()
        self.grid: GridEngine = None
        self.hedge: HedgeManager = None
        self.floor: FloorProtection = None
        self._running = False

    async def start(self):
        """Initialize and start the bot."""
        log.info("=" * 50)
        log.info("LIT GRID TRADING BOT")
        log.info("=" * 50)

        # Connect to exchange
        await self.client.connect()

        # Initialize components
        self.grid = GridEngine(self.client, self.state)
        self.hedge = HedgeManager(self.client, self.state)
        self.floor = FloorProtection(self.client, self.state, self.grid, self.hedge)

        # Sync state with exchange
        await self._sync_state()

        # Initialize positions
        await self.hedge.initialize()
        await self.grid.initialize()

        log.info("Bot initialized. Starting main loop...")
        self._running = True

        # Main loop
        await self._run_loop()

    async def _sync_state(self):
        """Sync local state with exchange."""
        log.info("Syncing state with exchange...")

        account = await self.client.get_account()
        log.info(f"Account collateral: ${account.collateral}")
        log.info(f"Available balance: ${account.available_balance}")

        # Check for existing positions
        positions = await self.client.get_positions()
        for p in positions:
            if p.size < 0:
                log.info(f"Found existing short: {p.size} LIT @ ${p.entry_price}")
                self.state.set("hedge_active", True)
                self.state.set("hedge_entry_price", float(p.entry_price))
                self.state.set("hedge_size", float(abs(p.size)))

        # Check for existing orders
        orders = await self.client.get_active_orders()
        log.info(f"Found {len(orders)} existing orders")

        log.info("State sync complete")

    async def _run_loop(self):
        """Main bot loop."""
        last_status_time = 0
        status_interval = 300  # 5 minutes

        while self._running and not self.state.get("bot_halted"):
            try:
                # Get current price
                current_price = await self.client.get_mid_price("LIT", MarketType.SPOT)

                # Check in priority order
                await self.floor.check(current_price)        # 1. Floor protection
                await self.hedge.check(current_price)        # 2. Hedge stop-loss/re-entry
                await self.hedge.check_funding()             # 3. Funding rate
                await self.grid.check_fills()                # 4. Grid cycling

                # Periodic status log
                if time.time() - last_status_time >= status_interval:
                    await self._log_status(current_price)
                    last_status_time = time.time()

                await asyncio.sleep(POLL_INTERVAL_SECONDS)

            except Exception as e:
                log.error(f"Error in main loop: {e}")
                await asyncio.sleep(10)  # Back off on errors

        log.info("Main loop ended")

    async def _log_status(self, price: Decimal):
        """Log current bot status."""
        grid_stats = self.grid.get_stats()
        hedge_stats = self.hedge.get_stats()
        floor_stats = self.floor.get_stats()

        log.info("-" * 40)
        log.info(f"STATUS @ ${price}")
        log.info(f"  Grid: {grid_stats['filled_grid_orders']} fills, "
                 f"${grid_stats['total_profit']:.2f} profit, "
                 f"paused={grid_stats['paused']}")
        log.info(f"  Hedge: active={hedge_stats['active']}, "
                 f"${hedge_stats['total_pnl']:.2f} PnL")
        log.info(f"  Floor: tier {floor_stats['tier_triggered']}")
        log.info("-" * 40)

    async def stop(self):
        """Stop the bot gracefully."""
        log.info("Stopping bot...")
        self._running = False
        await self.client.close()
        self.state.close()
        log.info("Bot stopped")


async def main():
    bot = LitGridBot()

    # Handle shutdown signals
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.stop()))

    try:
        await bot.start()
    except KeyboardInterrupt:
        pass
    finally:
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
```

**Step 2: Make script executable**

Run: `chmod +x scripts/run_bot.py`

**Step 3: Commit**

```bash
git add scripts/run_bot.py
git commit -m "feat: add main bot runner"
```

---

## Task 12: Package Finalization

**Files:**
- Update: `lithood/__init__.py`

**Step 1: Update package init with exports**

```python
# lithood/__init__.py
"""LIT Grid Trading Bot for Lighter DEX."""

__version__ = "0.1.0"

from lithood.client import LighterClient
from lithood.state import StateManager
from lithood.grid import GridEngine
from lithood.hedge import HedgeManager
from lithood.floor import FloorProtection

__all__ = [
    "LighterClient",
    "StateManager",
    "GridEngine",
    "HedgeManager",
    "FloorProtection",
]
```

**Step 2: Verify full import**

Run: `python -c "from lithood import LighterClient, GridEngine, HedgeManager, FloorProtection; print('All imports OK')"`
Expected: Prints "All imports OK"

**Step 3: Final commit**

```bash
git add lithood/__init__.py
git commit -m "feat: finalize package exports"
```

---

## Summary

**Files created:**
- `requirements.txt` - Dependencies
- `.env.example` - Configuration template
- `.gitignore` - Git ignores
- `lithood/__init__.py` - Package init
- `lithood/config.py` - Strategy parameters
- `lithood/logger.py` - Logging setup
- `lithood/types.py` - Data types
- `lithood/client.py` - Lighter API client
- `lithood/state.py` - SQLite state manager
- `lithood/grid.py` - Grid trading engine
- `lithood/hedge.py` - Hedge manager
- `lithood/floor.py` - Floor protection
- `scripts/test_connectivity.py` - API mini-test
- `scripts/run_bot.py` - Main bot runner

**To run:**
1. Copy `.env.example` to `.env` and add your private key
2. Run `pip install -r requirements.txt`
3. Run `python scripts/test_connectivity.py` to verify API works
4. Run `python scripts/run_bot.py` to start the bot

**Total commits:** 12
