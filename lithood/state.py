# lithood/state.py
"""SQLite-based state manager for the trading bot."""

import json
import logging
import sqlite3
import threading
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from lithood.types import Order, OrderSide, OrderStatus, OrderType

logger = logging.getLogger(__name__)


class InvalidStateTransitionError(Exception):
    """Raised when an invalid order state transition is attempted."""

    pass


class DecimalEncoder(json.JSONEncoder):
    """JSON encoder that handles Decimal types."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, Decimal):
            return str(obj)
        return super().default(obj)


def decimal_decoder(dct: dict) -> dict:
    """Decode strings that look like decimals back to Decimal."""
    result = {}
    for key, value in dct.items():
        if isinstance(value, str):
            try:
                result[key] = Decimal(value)
            except InvalidOperation:
                result[key] = value
        else:
            result[key] = value
    return result


class StateManager:
    """SQLite-based state persistence for the trading bot.

    Manages three tables:
    - orders: Track all grid and hedge orders
    - hedge_history: Track hedge position actions
    - bot_state: Key-value store for arbitrary state
    """

    # Valid state transitions for orders
    VALID_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
        OrderStatus.PENDING: {
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.PARTIALLY_FILLED,
        },
        OrderStatus.PARTIALLY_FILLED: {
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
        },
        OrderStatus.FILLED: set(),  # Terminal state - no transitions allowed
        OrderStatus.CANCELLED: set(),  # Terminal state - no transitions allowed
    }

    def __init__(self, db_path: str = "bot_state.db") -> None:
        """Initialize SQLite connection and create tables.

        Args:
            db_path: Path to SQLite database file. Use ':memory:' for testing.
        """
        self.db_path = db_path
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # Enable WAL mode for better concurrent access
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def _create_tables(self) -> None:
        """Create database tables if they don't exist."""
        with self._lock:
            try:
                cursor = self.conn.cursor()

                # Orders table - tracks all orders
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS orders (
                        id TEXT PRIMARY KEY,
                        market_id INTEGER NOT NULL,
                        side TEXT NOT NULL,
                        price TEXT NOT NULL,
                        size TEXT NOT NULL,
                        status TEXT NOT NULL,
                        order_type INTEGER NOT NULL,
                        grid_level INTEGER,
                        created_at TEXT NOT NULL,
                        filled_at TEXT,
                        filled_size TEXT DEFAULT '0'
                    )
                """)

                # Hedge history table - tracks hedge actions
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS hedge_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        action TEXT NOT NULL,
                        price TEXT NOT NULL,
                        size TEXT NOT NULL,
                        pnl TEXT,
                        funding_earned TEXT,
                        timestamp TEXT NOT NULL
                    )
                """)

                # Bot state table - key-value store
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS bot_state (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)

                # Create indexes for common queries
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_orders_status
                    ON orders(status)
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_orders_market_side
                    ON orders(market_id, side)
                """)

                self.conn.commit()
            except sqlite3.Error as e:
                logger.error("Failed to create database tables: %s", e)
                raise

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            if self.conn:
                self.conn.close()

    # -------------------------------------------------------------------------
    # Key-Value Store Methods
    # -------------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from the key-value store.

        Args:
            key: The key to retrieve
            default: Default value if key doesn't exist

        Returns:
            The stored value (JSON decoded) or default
        """
        with self._lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute("SELECT value FROM bot_state WHERE key = ?", (key,))
                row = cursor.fetchone()

                if row is None:
                    return default

                try:
                    return json.loads(row["value"], object_hook=decimal_decoder)
                except json.JSONDecodeError:
                    return row["value"]
            except sqlite3.Error as e:
                logger.error("Failed to get key '%s': %s", key, e)
                return default

    def set(self, key: str, value: Any) -> None:
        """Set a value in the key-value store.

        Args:
            key: The key to store
            value: The value to store (will be JSON encoded)
        """
        with self._lock:
            try:
                cursor = self.conn.cursor()
                json_value = json.dumps(value, cls=DecimalEncoder)
                timestamp = datetime.now().isoformat()

                cursor.execute(
                    """
                    INSERT OR REPLACE INTO bot_state (key, value, updated_at)
                    VALUES (?, ?, ?)
                    """,
                    (key, json_value, timestamp),
                )
                self.conn.commit()
            except sqlite3.Error as e:
                logger.error("Failed to set key '%s': %s", key, e)
                raise

    # -------------------------------------------------------------------------
    # Order Methods
    # -------------------------------------------------------------------------

    def save_order(self, order: Order) -> None:
        """Save or update an order in the database.

        Args:
            order: The Order object to save
        """
        with self._lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO orders
                    (id, market_id, side, price, size, status, order_type,
                     grid_level, created_at, filled_at, filled_size)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        order.id,
                        order.market_id,
                        order.side.value,
                        str(order.price),
                        str(order.size),
                        order.status.value,
                        order.order_type.value,
                        order.grid_level,
                        order.created_at.isoformat(),
                        order.filled_at.isoformat() if order.filled_at else None,
                        str(order.filled_size),
                    ),
                )
                self.conn.commit()
            except sqlite3.Error as e:
                logger.error("Failed to save order '%s': %s", order.id, e)
                raise

    def get_order(self, order_id: str) -> Optional[Order]:
        """Get an order by ID.

        Args:
            order_id: The order ID to retrieve

        Returns:
            Order object or None if not found
        """
        with self._lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
                row = cursor.fetchone()

                if row is None:
                    return None

                return self._row_to_order(row)
            except sqlite3.Error as e:
                logger.error("Failed to get order '%s': %s", order_id, e)
                return None

    def get_pending_orders(self) -> list[Order]:
        """Get all pending orders.

        Returns:
            List of Order objects with PENDING status
        """
        with self._lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute(
                    "SELECT * FROM orders WHERE status = ?", (OrderStatus.PENDING.value,)
                )
                rows = cursor.fetchall()

                return [self._row_to_order(row) for row in rows]
            except sqlite3.Error as e:
                logger.error("Failed to get pending orders: %s", e)
                return []

    def get_orders_by_status(self, status: OrderStatus) -> list[Order]:
        """Get all orders with a specific status.

        Args:
            status: The OrderStatus to filter by

        Returns:
            List of Order objects with the specified status
        """
        with self._lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute("SELECT * FROM orders WHERE status = ?", (status.value,))
                rows = cursor.fetchall()

                return [self._row_to_order(row) for row in rows]
            except sqlite3.Error as e:
                logger.error("Failed to get orders by status '%s': %s", status.value, e)
                return []

    def _validate_state_transition(
        self, current_status: OrderStatus, new_status: OrderStatus, order_id: str
    ) -> None:
        """Validate that a state transition is allowed.

        Args:
            current_status: The current order status
            new_status: The desired new status
            order_id: The order ID (for error messages)

        Raises:
            InvalidStateTransitionError: If the transition is not allowed
        """
        allowed_transitions = self.VALID_TRANSITIONS.get(current_status, set())
        if new_status not in allowed_transitions:
            raise InvalidStateTransitionError(
                f"Cannot transition order '{order_id}' from {current_status.value} "
                f"to {new_status.value}. Allowed transitions: "
                f"{[s.value for s in allowed_transitions]}"
            )

    def mark_filled(self, order_id: str, filled_size: Optional[Decimal] = None) -> None:
        """Mark an order as filled.

        Args:
            order_id: The order ID to mark as filled
            filled_size: Optional filled size (defaults to order size)

        Raises:
            InvalidStateTransitionError: If the order is already in a terminal state
            ValueError: If the order is not found
        """
        with self._lock:
            try:
                cursor = self.conn.cursor()

                # Get current status to validate transition
                cursor.execute("SELECT status FROM orders WHERE id = ?", (order_id,))
                row = cursor.fetchone()

                if row is None:
                    raise ValueError(f"Order '{order_id}' not found")

                current_status = OrderStatus(row["status"])
                self._validate_state_transition(
                    current_status, OrderStatus.FILLED, order_id
                )

                timestamp = datetime.now().isoformat()

                if filled_size is not None:
                    cursor.execute(
                        """
                        UPDATE orders
                        SET status = ?, filled_at = ?, filled_size = ?
                        WHERE id = ?
                        """,
                        (OrderStatus.FILLED.value, timestamp, str(filled_size), order_id),
                    )
                else:
                    cursor.execute(
                        """
                        UPDATE orders
                        SET status = ?, filled_at = ?, filled_size = size
                        WHERE id = ?
                        """,
                        (OrderStatus.FILLED.value, timestamp, order_id),
                    )
                self.conn.commit()
            except sqlite3.Error as e:
                logger.error("Failed to mark order '%s' as filled: %s", order_id, e)
                raise

    def mark_partially_filled(self, order_id: str, filled_size: Decimal) -> None:
        """Mark an order as partially filled and update its filled size.

        Args:
            order_id: The order ID to update
            filled_size: The amount that has been filled so far

        Raises:
            InvalidStateTransitionError: If the order is already in a terminal state
            ValueError: If the order is not found
        """
        with self._lock:
            try:
                cursor = self.conn.cursor()

                # Get current status to validate transition
                cursor.execute("SELECT status FROM orders WHERE id = ?", (order_id,))
                row = cursor.fetchone()

                if row is None:
                    raise ValueError(f"Order '{order_id}' not found")

                current_status = OrderStatus(row["status"])
                # Only validate if not already partially filled (allow updates)
                if current_status != OrderStatus.PARTIALLY_FILLED:
                    self._validate_state_transition(
                        current_status, OrderStatus.PARTIALLY_FILLED, order_id
                    )

                cursor.execute(
                    """
                    UPDATE orders
                    SET status = ?, filled_size = ?
                    WHERE id = ?
                    """,
                    (OrderStatus.PARTIALLY_FILLED.value, str(filled_size), order_id),
                )
                self.conn.commit()
            except sqlite3.Error as e:
                logger.error(
                    "Failed to mark order '%s' as partially filled: %s", order_id, e
                )
                raise

    def mark_cancelled(self, order_id: str) -> None:
        """Mark an order as cancelled.

        Args:
            order_id: The order ID to mark as cancelled

        Raises:
            InvalidStateTransitionError: If the order is already in a terminal state
            ValueError: If the order is not found
        """
        with self._lock:
            try:
                cursor = self.conn.cursor()

                # Get current status to validate transition
                cursor.execute("SELECT status FROM orders WHERE id = ?", (order_id,))
                row = cursor.fetchone()

                if row is None:
                    raise ValueError(f"Order '{order_id}' not found")

                current_status = OrderStatus(row["status"])
                self._validate_state_transition(
                    current_status, OrderStatus.CANCELLED, order_id
                )

                cursor.execute(
                    "UPDATE orders SET status = ? WHERE id = ?",
                    (OrderStatus.CANCELLED.value, order_id),
                )
                self.conn.commit()
            except sqlite3.Error as e:
                logger.error("Failed to mark order '%s' as cancelled: %s", order_id, e)
                raise

    def _row_to_order(self, row: sqlite3.Row) -> Order:
        """Convert a database row to an Order object.

        Args:
            row: SQLite Row object

        Returns:
            Order object
        """
        return Order(
            id=row["id"],
            market_id=row["market_id"],
            side=OrderSide(row["side"]),
            price=Decimal(row["price"]),
            size=Decimal(row["size"]),
            status=OrderStatus.from_value(row["status"]),
            order_type=OrderType.from_value(row["order_type"]),
            grid_level=row["grid_level"],
            created_at=datetime.fromisoformat(row["created_at"]),
            filled_at=(
                datetime.fromisoformat(row["filled_at"]) if row["filled_at"] else None
            ),
            filled_size=Decimal(row["filled_size"]) if row["filled_size"] else Decimal("0"),
        )

    # -------------------------------------------------------------------------
    # Hedge Methods
    # -------------------------------------------------------------------------

    def log_hedge_action(
        self,
        action: str,
        price: Decimal,
        size: Decimal,
        pnl: Optional[Decimal] = None,
        funding: Optional[Decimal] = None,
    ) -> int:
        """Log a hedge position action.

        Args:
            action: Action type (e.g., 'open', 'close', 'stop_loss', 'funding')
            price: Price at which action occurred
            size: Size of the position
            pnl: Realized PnL if closing
            funding: Funding earned if applicable

        Returns:
            The ID of the inserted record
        """
        with self._lock:
            try:
                cursor = self.conn.cursor()
                timestamp = datetime.now().isoformat()

                cursor.execute(
                    """
                    INSERT INTO hedge_history
                    (action, price, size, pnl, funding_earned, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        action,
                        str(price),
                        str(size),
                        str(pnl) if pnl is not None else None,
                        str(funding) if funding is not None else None,
                        timestamp,
                    ),
                )
                self.conn.commit()
                return cursor.lastrowid or 0
            except sqlite3.Error as e:
                logger.error("Failed to log hedge action '%s': %s", action, e)
                raise

    def get_hedge_history(self, limit: int = 100) -> list[dict]:
        """Get hedge action history.

        Args:
            limit: Maximum number of records to return

        Returns:
            List of hedge history records as dicts
        """
        with self._lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute(
                    """
                    SELECT * FROM hedge_history
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
                rows = cursor.fetchall()

                return [
                    {
                        "id": row["id"],
                        "action": row["action"],
                        "price": Decimal(row["price"]),
                        "size": Decimal(row["size"]),
                        "pnl": Decimal(row["pnl"]) if row["pnl"] else None,
                        "funding_earned": (
                            Decimal(row["funding_earned"])
                            if row["funding_earned"]
                            else None
                        ),
                        "timestamp": datetime.fromisoformat(row["timestamp"]),
                    }
                    for row in rows
                ]
            except sqlite3.Error as e:
                logger.error("Failed to get hedge history: %s", e)
                return []

    def get_total_funding_earned(self) -> Decimal:
        """Get total funding earned from hedge positions.

        Returns:
            Total funding earned as Decimal
        """
        with self._lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute(
                    """
                    SELECT COALESCE(SUM(CAST(funding_earned AS REAL)), 0) as total
                    FROM hedge_history
                    WHERE funding_earned IS NOT NULL
                    """
                )
                row = cursor.fetchone()
                return Decimal(str(row["total"])) if row else Decimal("0")
            except sqlite3.Error as e:
                logger.error("Failed to get total funding earned: %s", e)
                return Decimal("0")

    # -------------------------------------------------------------------------
    # Statistics Methods
    # -------------------------------------------------------------------------

    def get_grid_stats(self) -> dict:
        """Get grid trading statistics.

        Returns:
            Dict with grid stats:
            - total_orders: Total orders placed
            - pending_orders: Currently pending orders
            - filled_orders: Total filled orders
            - buy_fills: Total buy orders filled
            - sell_fills: Total sell orders filled
            - total_volume: Total volume traded (sum of filled sizes)
        """
        with self._lock:
            try:
                cursor = self.conn.cursor()

                # Get order counts by status
                cursor.execute(
                    """
                    SELECT
                        COUNT(*) as total_orders,
                        SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending_orders,
                        SUM(CASE WHEN status = 'filled' THEN 1 ELSE 0 END) as filled_orders,
                        SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) as cancelled_orders
                    FROM orders
                    """
                )
                counts = cursor.fetchone()

                # Get buy/sell fill counts and volume
                cursor.execute(
                    """
                    SELECT
                        SUM(CASE WHEN side = 'buy' AND status = 'filled' THEN 1 ELSE 0 END) as buy_fills,
                        SUM(CASE WHEN side = 'sell' AND status = 'filled' THEN 1 ELSE 0 END) as sell_fills,
                        COALESCE(SUM(CASE WHEN status = 'filled' THEN CAST(filled_size AS REAL) ELSE 0 END), 0) as total_volume
                    FROM orders
                    """
                )
                fills = cursor.fetchone()

                # Get grid cycles (matched buy-sell pairs)
                buy_fills = fills["buy_fills"] or 0
                sell_fills = fills["sell_fills"] or 0
                completed_cycles = min(buy_fills, sell_fills)

                return {
                    "total_orders": counts["total_orders"] or 0,
                    "pending_orders": counts["pending_orders"] or 0,
                    "filled_orders": counts["filled_orders"] or 0,
                    "cancelled_orders": counts["cancelled_orders"] or 0,
                    "buy_fills": buy_fills,
                    "sell_fills": sell_fills,
                    "completed_cycles": completed_cycles,
                    "total_volume": (
                        Decimal(str(fills["total_volume"]))
                        if fills["total_volume"]
                        else Decimal("0")
                    ),
                }
            except sqlite3.Error as e:
                logger.error("Failed to get grid stats: %s", e)
                return {
                    "total_orders": 0,
                    "pending_orders": 0,
                    "filled_orders": 0,
                    "cancelled_orders": 0,
                    "buy_fills": 0,
                    "sell_fills": 0,
                    "completed_cycles": 0,
                    "total_volume": Decimal("0"),
                }

    def get_orders_by_grid_level(self, grid_level: int) -> list[Order]:
        """Get all orders for a specific grid level.

        Args:
            grid_level: The grid level to filter by

        Returns:
            List of Order objects for that grid level
        """
        with self._lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute(
                    "SELECT * FROM orders WHERE grid_level = ? ORDER BY created_at DESC",
                    (grid_level,),
                )
                rows = cursor.fetchall()

                return [self._row_to_order(row) for row in rows]
            except sqlite3.Error as e:
                logger.error("Failed to get orders for grid level %d: %s", grid_level, e)
                return []

    def clear_all(self) -> None:
        """Clear all data from the database. Use with caution!"""
        with self._lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute("DELETE FROM orders")
                cursor.execute("DELETE FROM hedge_history")
                cursor.execute("DELETE FROM bot_state")
                self.conn.commit()
            except sqlite3.Error as e:
                logger.error("Failed to clear all data: %s", e)
                raise
