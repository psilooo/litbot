# lithood/grid.py
"""Spot grid trading engine.

IMPORTANT: Order ID handling
-----------------------------
When placing orders via client.place_limit_order(), the returned Order has id=tx_hash.
When querying active orders via client.get_active_orders(), Orders have id=order_index.

These are different values! The grid tracks orders by tx_hash (from placement), but
fill detection compares against order_index (from active orders query).

To handle this mismatch, we store both:
- The tx_hash as the primary key for our local order tracking
- We lookup orders by price/side/market to match fills since we can't rely on ID matching

Future improvement: Use WebSocket fill notifications which include tx_hash for reliable matching.
"""

from decimal import Decimal
from typing import Optional

from lithood.client import LighterClient
from lithood.state import StateManager
from lithood.types import Order, OrderSide, MarketType
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
        if current_price is None:
            log.error("Failed to get mid price - cannot initialize grid")
            return

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

    async def _place_grid_buy(self, price: Decimal, usdc_amount: Decimal, grid_level: Optional[int] = None) -> Optional[Order]:
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
        if order is None:
            log.error(f"Failed to place grid buy at ${price}")
            return None

        order.grid_level = grid_level
        self.state.save_order(order)

        log.info(f"Grid buy placed: {size:.2f} LIT @ ${price} (level {grid_level})")
        return order

    async def _place_grid_sell(self, price: Decimal, lit_amount: Decimal, grid_level: Optional[int] = None) -> Optional[Order]:
        """Place a grid sell order."""
        order = await self.client.place_limit_order(
            symbol=self.symbol,
            market_type=self.market_type,
            side=OrderSide.SELL,
            price=price,
            size=lit_amount,
        )
        if order is None:
            log.error(f"Failed to place grid sell at ${price}")
            return None

        order.grid_level = grid_level
        self.state.save_order(order)

        log.info(f"Grid sell placed: {lit_amount:.2f} LIT @ ${price} (level {grid_level})")
        return order

    async def check_fills(self):
        """Check for filled orders and trigger cycling.

        IMPORTANT: Order ID mismatch handling
        -------------------------------------
        Our locally stored orders have id=tx_hash (from place_limit_order).
        Active orders from the exchange have id=order_index (different value).

        Since IDs don't match, we use price-based matching:
        - Build a set of (price, side) tuples from active exchange orders
        - If a pending local order's (price, side) is not in the active set, it was filled

        LIMITATION: Partial fills and other edge cases
        -----------------------------------------------
        An order may disappear from active orders due to:
        - Full fill (what we assume)
        - Partial fill followed by cancellation
        - Order expiration (TIME_IN_FORCE timeout)
        - User cancellation via another client

        For now, we assume any missing order was fully filled. This is a known
        limitation. Proper handling requires either:
        - Order history API (to check actual fill status)
        - WebSocket fill notifications (preferred for real-time detection)
        """
        if self.state.get("grid_paused"):
            return

        # Get active orders from exchange
        market = self.client.get_market(self.symbol, self.market_type)
        if market is None:
            log.error(f"Market not found: {self.symbol}_{self.market_type.value}")
            return

        active_orders = await self.client.get_active_orders(market_id=market.market_id)

        # Build set of (price, side) for matching since order IDs don't match
        # (local orders use tx_hash, exchange orders use order_index)
        active_price_side = {(o.price, o.side) for o in active_orders}

        # Check our pending orders
        for order in self.state.get_pending_orders():
            if order.market_id != market.market_id:
                continue

            if (order.price, order.side) not in active_price_side:
                # Order is no longer active at this price/side - assume filled
                # NOTE: See docstring for limitations of this approach
                await self._on_fill(order)

    async def _on_fill(self, order: Order):
        """Handle a filled order - place the opposite side.

        Preserves the original order's grid_level so we can track which
        level a cycling order originated from.
        """
        self.state.mark_filled(order.id)

        if order.side == OrderSide.BUY:
            # Buy filled -> place sell at +3%
            sell_price = order.price * (1 + GRID_SPREAD)
            sell_size = order.size  # Sell the LIT we just bought

            log.info(f"Buy filled @ ${order.price} -> placing sell @ ${sell_price}")
            # Preserve grid_level from the original buy order
            await self._place_grid_sell(sell_price, sell_size, grid_level=order.grid_level)

        else:
            # Sell filled -> place buy at -3%, keeping 3% profit
            buy_price = order.price * (1 - GRID_SPREAD)
            usdc_received = order.size * order.price
            usdc_to_reinvest = usdc_received * (1 - GRID_PROFIT_RETAIN)

            log.info(f"Sell filled @ ${order.price} -> placing buy @ ${buy_price}")
            log.info(f"  Profit retained: ${usdc_received * GRID_PROFIT_RETAIN:.2f}")
            # Preserve grid_level from the original sell order
            await self._place_grid_buy(buy_price, usdc_to_reinvest, grid_level=order.grid_level)

            # Update profit tracking (store as string for Decimal precision)
            total_profit = Decimal(self.state.get("total_grid_profit", "0"))
            total_profit += usdc_received * GRID_PROFIT_RETAIN
            self.state.set("total_grid_profit", str(total_profit))

    def pause(self):
        """Pause all grid trading activity (called by floor protection).

        When paused:
        - No new buy orders will be placed
        - No new sell orders will be placed (cycling is stopped)
        - check_fills() will return early without processing
        - initialize() will skip order placement

        Existing orders remain on the exchange until explicitly cancelled.
        """
        self.state.set("grid_paused", True)
        log.warning("Grid trading PAUSED")

    # Alias for backward compatibility - pause_buys actually pauses all grid activity
    pause_buys = pause

    def resume(self):
        """Resume grid trading."""
        self.state.set("grid_paused", False)
        log.info("Grid trading RESUMED")

    async def cancel_all(self) -> int:
        """Cancel all grid orders."""
        market = self.client.get_market(self.symbol, self.market_type)
        if market is None:
            log.error(f"Market not found: {self.symbol}_{self.market_type.value}")
            return 0

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
        # Read profit as string and convert to Decimal for precision
        profit_str = self.state.get("total_grid_profit", "0")
        stats["total_profit"] = Decimal(profit_str) if profit_str else Decimal("0")
        stats["paused"] = self.state.get("grid_paused", False)
        return stats
