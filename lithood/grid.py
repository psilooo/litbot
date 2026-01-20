# lithood/grid.py
"""Spot grid trading engine with fixed-level cycling.

DESIGN:
-------
- Grid levels spaced 2% apart (both buys and sells)
- Cycle spread also 2%, so counter-orders land exactly on grid levels
- When buy fills at level N → sell at level N+1 (2% higher)
- When sell fills at level N → buy at level N-1 (2% lower)
- No drift: counter-orders always snap to nearest grid level

Order ID handling
-----------------
When placing orders via client.place_limit_order(), the returned Order has id=tx_hash.
When querying active orders via client.get_active_orders(), Orders have id=order_index.

These are different values! We lookup orders by price/side/market to match fills.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, Dict, Set

from lithood.client import LighterClient
from lithood.state import StateManager
from lithood.types import Order, OrderSide, MarketType, OrderStatus
from lithood.config import (
    generate_grid_pairs, generate_full_grid_ladder, GridPair, GRID_CONFIG,
    SPOT_SYMBOL,
)
from lithood.logger import log

# Cycle spread - must equal level spacing to prevent drift
CYCLE_SPREAD = GRID_CONFIG["cycle_spread_pct"]


class GridEngine:
    """Manages spot grid trading with fixed-level cycling."""

    def __init__(self, client: LighterClient, state: StateManager):
        self.client = client
        self.state = state
        self.symbol = SPOT_SYMBOL  # "LIT/USDC" for spot market
        self.market_type = MarketType.SPOT
        self.grid_levels: list[GridPair] = []  # Initial grid levels
        self._grid_prices: Set[Decimal] = set()  # All valid grid prices for snapping

    async def initialize(self):
        """Generate grid levels and place initial orders based on current price."""
        entry_price = await self.client.get_mid_price(self.symbol, self.market_type)
        if entry_price is None:
            log.error("Failed to get mid price - cannot initialize grid")
            return

        # Clear stale pending orders from previous runs
        # (they have different prices and would cause false fill detection)
        await self._clear_stale_pending_orders()

        # Store entry price for reference
        self.state.set("bot_entry_price", str(entry_price))
        self.state.set("grid_entry_price", str(entry_price))

        log.info(f"Initializing grid. Entry price: ${entry_price}")

        if self.state.get("grid_paused"):
            log.warning("Grid is paused - skipping initialization")
            return

        # Generate grid levels for initial placement
        self.grid_levels = generate_grid_pairs(entry_price)

        # Build FULL price ladder for snapping (includes entry and all intermediate levels)
        # This ensures counter-orders can snap even when they fall in the "dead zone"
        self._grid_prices = set(generate_full_grid_ladder(entry_price))

        # Log the generated grid
        log.info(f"Generated {len(self.grid_levels)} grid levels (2% spacing):")
        for level in self.grid_levels:
            log.info(f"  Level {level.pair_id}: Buy ${level.buy_price} / Sell ${level.sell_price}")

        # Place initial orders - buys below entry, sells above entry (all 400 LIT)
        for level in self.grid_levels:
            if level.buy_price < entry_price:
                await self._place_grid_buy(
                    price=level.buy_price,
                    lit_size=level.lit_size,  # Fixed 400 LIT
                    level_id=level.pair_id
                )

            if level.sell_price > entry_price:
                await self._place_grid_sell(
                    price=level.sell_price,
                    lit_amount=level.lit_size,  # Fixed 400 LIT
                    level_id=level.pair_id
                )

        log.info("Grid initialized")

    async def _clear_stale_pending_orders(self):
        """Clear stale pending orders from previous runs.

        On restart, the grid generates new prices based on current entry price.
        Old pending orders in state would have different prices and cause
        false fill detection. Cancel them on the exchange and clear local state.
        """
        market = self.client.get_market(self.symbol, self.market_type)
        if market is None:
            return

        # First cancel all orders on the exchange for this market
        # This ensures we don't leave orphan orders on the exchange
        try:
            cancelled = await self.client.cancel_all_orders(market_id=market.market_id)
            if cancelled > 0:
                log.info(f"Cancelled {cancelled} orders on exchange during startup cleanup")
        except Exception as e:
            log.warning(f"Failed to cancel exchange orders during cleanup: {e}")

        # Then clear local state
        stale_count = 0
        for order in self.state.get_pending_orders():
            if order.market_id == market.market_id:
                self.state.mark_cancelled(order.id)
                stale_count += 1

        if stale_count > 0:
            log.info(f"Cleared {stale_count} stale pending orders from local state")

    def _snap_to_grid(self, price: Decimal) -> Decimal:
        """Snap a calculated price to the nearest grid level.

        This prevents floating-point drift by ensuring counter-orders
        always land exactly on predefined grid levels.
        """
        if not self._grid_prices:
            # No grid prices yet, return as-is
            return price.quantize(Decimal("0.0001"))

        # Find the closest grid price
        closest = min(self._grid_prices, key=lambda p: abs(p - price))

        # Only snap if within 1% of a grid level (sanity check)
        if abs(closest - price) / price < Decimal("0.01"):
            return closest

        # If no close grid level, return quantized price (shouldn't happen normally)
        log.warning(f"Price ${price} not near any grid level, using as-is")
        return price.quantize(Decimal("0.0001"))

    async def _place_grid_buy(self, price: Decimal, lit_size: Decimal, level_id: int = 0) -> Optional[Order]:
        """Place a grid buy order for a fixed LIT amount."""
        if self.state.get("grid_paused"):
            log.debug(f"Grid paused - skipping buy at ${price}")
            return None

        order = await self.client.place_limit_order(
            symbol=self.symbol,
            market_type=self.market_type,
            side=OrderSide.BUY,
            price=price,
            size=lit_size,
        )
        if order is None:
            log.error(f"Failed to place grid buy at ${price}")
            return None

        order.grid_level = level_id
        self.state.save_order(order)

        log.info(f"Grid BUY placed: {lit_size:.2f} LIT @ ${price}")
        return order

    async def _place_grid_sell(self, price: Decimal, lit_amount: Decimal, level_id: int = 0) -> Optional[Order]:
        """Place a grid sell order."""
        if self.state.get("grid_paused"):
            log.debug(f"Grid paused - skipping sell at ${price}")
            return None

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

        order.grid_level = level_id
        self.state.save_order(order)

        log.info(f"Grid SELL placed: {lit_amount:.2f} LIT @ ${price}")
        return order

    async def check_fills(self):
        """Check for filled orders and trigger cycling.

        Uses price-based matching since order IDs differ between
        placement (tx_hash) and query (order_index).

        Handles both full fills (order disappears) and partial fills
        (order still active but filled_size increased).
        """
        if self.state.get("grid_paused"):
            return

        # Get active orders from exchange
        market = self.client.get_market(self.symbol, self.market_type)
        if market is None:
            log.error(f"Market not found: {self.symbol}_{self.market_type.value}")
            return

        active_orders = await self.client.get_active_orders(market_id=market.market_id)

        # Build map of (price, side) -> exchange order for partial fill detection
        active_order_map: Dict[tuple[Decimal, OrderSide], Order] = {}
        for o in active_orders:
            active_order_map[(o.price, o.side)] = o

        # Grace period: don't check orders placed less than 30 seconds ago
        # (they may not have propagated to the exchange yet)
        grace_cutoff = datetime.now() - timedelta(seconds=30)

        # Check our pending and partially filled orders
        # (partially filled orders need continued monitoring for more fills)
        orders_to_check = (
            self.state.get_pending_orders()
            + self.state.get_orders_by_status(OrderStatus.PARTIALLY_FILLED)
        )
        for order in orders_to_check:
            if order.market_id != market.market_id:
                continue

            # Skip orders that are too new (grace period)
            if order.created_at > grace_cutoff:
                continue

            key = (order.price, order.side)
            exchange_order = active_order_map.get(key)

            if exchange_order is None:
                # Order is no longer active - fully filled
                filled_size = order.size  # Assume full size was filled
                await self._on_fill(order, filled_size)
            elif exchange_order.filled_size > order.filled_size:
                # Order is still active but has new partial fill
                new_fill_amount = exchange_order.filled_size - order.filled_size
                log.info(
                    f"Partial fill detected: {new_fill_amount:.4f} of {order.size:.4f} "
                    f"@ ${order.price} ({order.side.value})"
                )
                # Update local state with new filled amount
                self.state.mark_partially_filled(order.id, exchange_order.filled_size)
                # Place counter-order for only the newly filled portion
                await self._on_fill(order, new_fill_amount)

    async def _on_fill(self, order: Order, filled_size: Decimal):
        """Handle a filled order - place counter-order at the adjacent grid level.

        Args:
            order: The order that was filled (or partially filled)
            filled_size: The amount that was filled (may be less than order.size for partials)

        FIXED-LEVEL CYCLING:
        - Buy fills → sell at next level up (order.price × 1.02, snapped to grid)
        - Sell fills → buy at next level down (order.price × 0.98, snapped to grid)

        Since level spacing = cycle spread = 2%, counter-orders land exactly on grid levels.
        Snapping ensures no floating-point drift over time.
        """
        is_full_fill = filled_size >= order.size
        if is_full_fill:
            self.state.mark_filled(order.id, filled_size)

        # Check pause status before placing counter-orders
        if self.state.get("grid_paused"):
            fill_type = "full" if is_full_fill else "partial"
            log.info(f"Grid paused - not placing counter-order for {fill_type} fill at ${order.price}")
            return

        # Get level_id for tracking (use existing or assign 0)
        level_id = order.grid_level or 0

        if order.side == OrderSide.BUY:
            # Buy filled -> place sell one level up (2% higher)
            raw_price = order.price * (1 + CYCLE_SPREAD)
            sell_price = self._snap_to_grid(raw_price)
            sell_size = filled_size  # Sell only the LIT we actually bought

            log.info(f"BUY FILLED @ ${order.price} -> placing sell @ ${sell_price} (next level up)")
            await self._place_grid_sell(sell_price, sell_size, level_id=level_id)

            # Increment fill counter
            fills = int(self.state.get("grid_buy_fills", "0"))
            self.state.set("grid_buy_fills", str(fills + 1))

        else:
            # Sell filled -> place buy one level down (2% lower)
            # Use same LIT size - profit is captured in the price spread
            raw_price = order.price * (1 - CYCLE_SPREAD)
            buy_price = self._snap_to_grid(raw_price)

            # Profit = difference between sell and buy price × size
            realized_profit = filled_size * (order.price - buy_price)

            log.info(f"SELL FILLED @ ${order.price} -> placing buy @ ${buy_price} (next level down)")
            log.info(f"  Spread profit: ${realized_profit:.2f}")

            await self._place_grid_buy(buy_price, filled_size, level_id=level_id)  # Same LIT size

            # Update profit tracking
            total_profit = Decimal(self.state.get("total_grid_profit", "0"))
            total_profit += realized_profit
            self.state.set("total_grid_profit", str(total_profit))

            # Increment fill and cycle counters
            fills = int(self.state.get("grid_sell_fills", "0"))
            self.state.set("grid_sell_fills", str(fills + 1))

            cycles = int(self.state.get("grid_cycles", "0"))
            self.state.set("grid_cycles", str(cycles + 1))

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

    # Alias for backward compatibility
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
        stats["buy_fills"] = int(self.state.get("grid_buy_fills", "0"))
        stats["sell_fills"] = int(self.state.get("grid_sell_fills", "0"))
        stats["cycles"] = int(self.state.get("grid_cycles", "0"))
        stats["num_levels"] = len(self.grid_levels)
        return stats
