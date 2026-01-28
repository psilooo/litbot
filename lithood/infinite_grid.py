# lithood/infinite_grid.py
"""Infinite grid trading engine with adaptive recentering.

DESIGN:
-------
- Grid re-centers when price reaches top or bottom level
- No core sells - all capital in the cycling grid
- Captures volatility through continuous buy/sell cycling
"""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, Set, List

from lithood.client import LighterClient
from lithood.state import StateManager
from lithood.types import Order, OrderSide, MarketType, OrderStatus
from lithood.config import SPOT_SYMBOL
from lithood.logger import log


class InfiniteGridConfig:
    """Configuration for infinite grid."""
    def __init__(
        self,
        num_levels: int = 15,  # levels per side (more than original 10)
        level_spacing_pct: Decimal = Decimal("0.02"),
        lit_per_order: Decimal = Decimal("350"),
        total_grid_lit: Decimal = Decimal("16000"),  # All LIT for grid (no core)
        recenter_threshold: int = 2,  # Recenter when within N levels of edge
    ):
        self.num_levels = num_levels
        self.level_spacing_pct = level_spacing_pct
        self.lit_per_order = lit_per_order
        self.total_grid_lit = total_grid_lit
        self.recenter_threshold = recenter_threshold


class InfiniteGridEngine:
    """Manages infinite grid trading with adaptive recentering."""

    # Reconciliation interval in seconds (30 minutes)
    RECONCILE_INTERVAL = 30 * 60

    def __init__(self, client: LighterClient, state: StateManager, config: InfiniteGridConfig = None):
        self.client = client
        self.state = state
        self.config = config or InfiniteGridConfig()
        self.symbol = SPOT_SYMBOL
        self.market_type = MarketType.SPOT

        # Grid state
        self._grid_center: Decimal = Decimal("0")
        self._buy_levels: List[Decimal] = []
        self._sell_levels: List[Decimal] = []

        # Reconciliation tracking
        self._last_reconcile_time: Optional[datetime] = None

        # Processing lock to prevent duplicate counter-orders from same partial fill
        self._processing_orders: set[str] = set()

    async def initialize(self) -> bool:
        """Initialize grid centered on current price.

        Returns:
            True if initialization successful, False otherwise
        """
        entry_price = await self.client.get_mid_price(self.symbol, self.market_type)
        if entry_price is None:
            log.error("Failed to get mid price - cannot initialize infinite grid")
            return False

        # Clear stale orders - abort if cancellation fails
        if not await self._clear_all_grid_orders():
            log.error("Failed to clear existing orders - aborting initialization to prevent order accumulation")
            return False

        if self.state.get("grid_paused"):
            log.warning("Grid is paused - skipping initialization")
            return True  # Not an error, just skipped

        self._grid_center = entry_price

        log.info(f"Initializing infinite grid. Center: ${entry_price}")

        # Generate levels
        self._generate_levels(entry_price)

        # Place orders
        await self._place_initial_orders(entry_price)

        self.state.set("infinite_grid_center", str(entry_price))
        log.info("Infinite grid initialized")
        return True

    def _generate_levels(self, center: Decimal):
        """Generate buy and sell price levels around center."""
        spacing = self.config.level_spacing_pct

        # Generate buy levels (below center)
        self._buy_levels = []
        for i in range(1, self.config.num_levels + 1):
            price = center * ((1 - spacing) ** i)
            self._buy_levels.append(price.quantize(Decimal("0.0001")))

        # Generate sell levels (above center)
        self._sell_levels = []
        for i in range(1, self.config.num_levels + 1):
            price = center * ((1 + spacing) ** i)
            price = price.quantize(Decimal("0.0001"))
            self._sell_levels.append(price)

        log.info(f"Generated {len(self._buy_levels)} buy levels, {len(self._sell_levels)} sell levels")

    async def _place_initial_orders(self, center: Decimal):
        """Place buy and sell orders."""
        for price in self._buy_levels:
            await self._place_grid_buy(price)

        for price in self._sell_levels:
            await self._place_grid_sell(price)

    async def _clear_all_grid_orders(self, max_retries: int = 3, verify_delay: float = 1.0) -> bool:
        """Cancel all orders on the exchange and verify cancellation.

        Returns True only if all orders are confirmed cancelled on exchange.
        Local state is only updated for orders confirmed to be gone.

        Args:
            max_retries: Maximum number of cancellation attempts
            verify_delay: Seconds to wait between cancel and verify

        Returns:
            True if all orders successfully cancelled and verified, False otherwise
        """
        market = self.client.get_market(self.symbol, self.market_type)
        if market is None:
            log.error("Cannot clear orders: market not found")
            return False

        # Get local pending orders for this market before cancellation
        local_pending = [
            o for o in self.state.get_pending_orders()
            if o.market_id == market.market_id
        ]
        local_partial = [
            o for o in self.state.get_orders_by_status(OrderStatus.PARTIALLY_FILLED)
            if o.market_id == market.market_id
        ]
        local_orders = local_pending + local_partial

        if not local_orders:
            # No local orders to cancel, but check exchange for orphans
            try:
                exchange_orders = await self.client.get_active_orders(market_id=market.market_id)
                if exchange_orders:
                    log.warning(f"Found {len(exchange_orders)} orphan orders on exchange with no local state")
                    # Attempt to cancel orphans
                    await self.client.cancel_all_orders(market_id=market.market_id)
                    await asyncio.sleep(verify_delay)
                    remaining = await self.client.get_active_orders(market_id=market.market_id)
                    if remaining:
                        log.error(f"Failed to cancel {len(remaining)} orphan orders")
                        return False
                    log.info("Successfully cancelled orphan orders")
            except Exception as e:
                log.warning(f"Error checking for orphan orders: {e}")
            return True

        log.info(f"Attempting to cancel {len(local_orders)} orders")

        for attempt in range(max_retries):
            try:
                # Send cancellation request
                cancelled = await self.client.cancel_all_orders(market_id=market.market_id)
                log.info(f"Cancel request sent (attempt {attempt + 1}/{max_retries}), reported {cancelled} cancelled")

                # Wait for cancellation to process
                await asyncio.sleep(verify_delay)

                # Verify by checking what's actually on the exchange
                exchange_orders = await self.client.get_active_orders(market_id=market.market_id)
                exchange_ids = {o.id for o in exchange_orders}

                # Check which local orders are confirmed gone
                confirmed_cancelled = []
                still_active = []

                for order in local_orders:
                    if order.id not in exchange_ids:
                        confirmed_cancelled.append(order)
                    else:
                        still_active.append(order)

                # Mark confirmed cancelled orders in local state
                for order in confirmed_cancelled:
                    try:
                        self.state.mark_cancelled(order.id)
                    except Exception as e:
                        log.warning(f"Failed to mark order {order.id} as cancelled in local state: {e}")

                if confirmed_cancelled:
                    log.info(f"Verified {len(confirmed_cancelled)} orders cancelled")

                if not still_active:
                    # All orders confirmed cancelled
                    log.info("All orders successfully cancelled and verified")
                    return True

                # Some orders still active - will retry
                log.warning(
                    f"Cancellation incomplete: {len(still_active)} orders still active on exchange "
                    f"(attempt {attempt + 1}/{max_retries})"
                )
                for order in still_active[:5]:  # Log first 5
                    log.warning(f"  Still active: {order.side.value} @ ${order.price} (id={order.id})")

                # Update local_orders to only track still-active ones for next iteration
                local_orders = still_active

            except Exception as e:
                log.error(f"Cancellation attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    log.error("All cancellation attempts failed - local state preserved")
                    return False

            # Exponential backoff before retry
            if attempt < max_retries - 1:
                backoff = verify_delay * (2 ** attempt)
                log.info(f"Retrying in {backoff:.1f}s...")
                await asyncio.sleep(backoff)

        # If we get here, we've exhausted retries with orders still active
        log.error(
            f"Failed to cancel all orders after {max_retries} attempts. "
            f"{len(local_orders)} orders may still be active on exchange. "
            "Local state preserved for unverified orders."
        )
        return False

    async def _place_grid_buy(self, price: Decimal) -> Optional[Order]:
        """Place a grid buy order."""
        if self.state.get("grid_paused"):
            return None

        order = await self.client.place_limit_order(
            symbol=self.symbol,
            market_type=self.market_type,
            side=OrderSide.BUY,
            price=price,
            size=self.config.lit_per_order,
        )
        if order is None:
            log.error(f"Failed to place grid buy at ${price}")
            return None

        self.state.save_order(order)
        log.info(f"INF-GRID BUY: {self.config.lit_per_order} LIT @ ${price}")
        return order

    async def _place_grid_sell(self, price: Decimal) -> Optional[Order]:
        """Place a grid sell order."""
        if self.state.get("grid_paused"):
            return None

        order = await self.client.place_limit_order(
            symbol=self.symbol,
            market_type=self.market_type,
            side=OrderSide.SELL,
            price=price,
            size=self.config.lit_per_order,
        )
        if order is None:
            log.error(f"Failed to place grid sell at ${price}")
            return None

        self.state.save_order(order)
        log.info(f"INF-GRID SELL: {self.config.lit_per_order} LIT @ ${price}")
        return order

    async def check_fills(self):
        """Check for filled orders and cycle. Ratchet floor on profitable cycles."""
        if self.state.get("grid_paused"):
            return

        market = self.client.get_market(self.symbol, self.market_type)
        if market is None:
            log.error(f"Market not found: {self.symbol}_{self.market_type.value}")
            return

        try:
            active_orders = await self.client.get_active_orders(market_id=market.market_id)
        except Exception as e:
            log.error(f"Failed to get active orders: {e}")
            return

        # Use order ID for matching (fixes map collision when multiple orders at same price/side)
        active_by_id = {o.id: o for o in active_orders}
        # Keep price/side map as fallback for orders placed before this fix
        active_by_price_side = {(o.price, o.side): o for o in active_orders}

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
            if order.created_at > grace_cutoff:
                continue

            # Skip if already being processed (prevents duplicate counter-orders)
            if order.id in self._processing_orders:
                continue

            self._processing_orders.add(order.id)
            try:
                # Try ID match first, fall back to price/side for backward compatibility
                exchange_order = active_by_id.get(order.id)
                if exchange_order is None:
                    exchange_order = active_by_price_side.get((order.price, order.side))

                if exchange_order is None:
                    # Order is no longer active - fully filled
                    await self._on_full_fill(order)
                elif exchange_order.filled_size > order.filled_size:
                    # Order is still active but has new partial fill
                    new_fill_amount = exchange_order.filled_size - order.filled_size
                    log.info(
                        f"Partial fill detected: {new_fill_amount:.4f} of {order.size:.4f} "
                        f"@ ${order.price} ({order.side.value})"
                    )
                    # Update local state with new filled amount (keep as PARTIALLY_FILLED)
                    self.state.mark_partially_filled(order.id, exchange_order.filled_size)
                    # Place counter-order for only the newly filled portion
                    await self._place_counter_order(order, new_fill_amount)
            finally:
                self._processing_orders.discard(order.id)

    async def _on_full_fill(self, order: Order):
        """Handle full fill - mark filled and place counter-order for remaining size."""
        # Calculate remaining unfilled size (partial fills already handled)
        remaining_size = order.size - order.filled_size

        # Mark the order as fully filled
        self.state.mark_filled(order.id, order.size)

        # Only place counter-order for the remaining unfilled portion
        if remaining_size > 0:
            await self._place_counter_order(order, remaining_size)

    async def _place_counter_order(self, order: Order, filled_size: Decimal, retry_count: int = 0):
        """Place counter-order for a fill. Retries on failure to prevent order loss."""
        if self.state.get("grid_paused"):
            return

        spacing = self.config.level_spacing_pct
        max_retries = 3

        if order.side == OrderSide.BUY:
            # Buy filled -> sell 2% higher
            sell_price = (order.price * (1 + spacing)).quantize(Decimal("0.0001"))
            log.info(f"BUY FILLED @ ${order.price} ({filled_size} LIT) -> sell @ ${sell_price}")
            counter_order = await self._place_grid_sell(sell_price)

            if counter_order is None:
                if retry_count < max_retries:
                    # Check if order already exists at this price (placement may have succeeded)
                    market = self.client.get_market(self.symbol, self.market_type)
                    if market is not None:
                        try:
                            active_orders = await self.client.get_active_orders(market_id=market.market_id)
                            existing = [o for o in active_orders if o.price == sell_price and o.side == OrderSide.SELL]
                            if existing:
                                log.info(f"Order already exists at ${sell_price} SELL, skipping retry")
                                counter_order = existing[0]
                                # Save to local state if not already tracked
                                self.state.save_order(counter_order)
                                fills = int(self.state.get("infinite_grid_buy_fills", "0"))
                                self.state.set("infinite_grid_buy_fills", str(fills + 1))
                                return
                        except Exception as e:
                            log.warning(f"Failed to check for existing order: {e}")

                    log.warning(f"Counter-order failed, retrying ({retry_count + 1}/{max_retries})...")
                    await asyncio.sleep(2 ** retry_count)  # Exponential backoff
                    await self._place_counter_order(order, filled_size, retry_count + 1)
                    return
                else:
                    log.error(f"CRITICAL: Failed to place counter SELL after {max_retries} retries! "
                              f"Order lost at ${sell_price}")
                    return

            fills = int(self.state.get("infinite_grid_buy_fills", "0"))
            self.state.set("infinite_grid_buy_fills", str(fills + 1))

        else:
            # Sell filled -> buy 2% lower
            buy_price = (order.price * (1 - spacing)).quantize(Decimal("0.0001"))
            profit = filled_size * order.price * spacing  # Approximate profit

            log.info(f"SELL FILLED @ ${order.price} ({filled_size} LIT) -> buy @ ${buy_price} (profit ~${profit:.2f})")
            counter_order = await self._place_grid_buy(buy_price)

            if counter_order is None:
                if retry_count < max_retries:
                    # Check if order already exists at this price (placement may have succeeded)
                    market = self.client.get_market(self.symbol, self.market_type)
                    if market is not None:
                        try:
                            active_orders = await self.client.get_active_orders(market_id=market.market_id)
                            existing = [o for o in active_orders if o.price == buy_price and o.side == OrderSide.BUY]
                            if existing:
                                log.info(f"Order already exists at ${buy_price} BUY, skipping retry")
                                counter_order = existing[0]
                                # Save to local state if not already tracked
                                self.state.save_order(counter_order)
                                # Update profit tracking
                                total_profit = Decimal(self.state.get("infinite_grid_profit", "0"))
                                total_profit += profit
                                self.state.set("infinite_grid_profit", str(total_profit))
                                fills = int(self.state.get("infinite_grid_sell_fills", "0"))
                                self.state.set("infinite_grid_sell_fills", str(fills + 1))
                                cycles = int(self.state.get("infinite_grid_cycles", "0"))
                                self.state.set("infinite_grid_cycles", str(cycles + 1))
                                return
                        except Exception as e:
                            log.warning(f"Failed to check for existing order: {e}")

                    log.warning(f"Counter-order failed, retrying ({retry_count + 1}/{max_retries})...")
                    await asyncio.sleep(2 ** retry_count)  # Exponential backoff
                    await self._place_counter_order(order, filled_size, retry_count + 1)
                    return
                else:
                    log.error(f"CRITICAL: Failed to place counter BUY after {max_retries} retries! "
                              f"Order lost at ${buy_price}")
                    return

            # Update profit tracking
            total_profit = Decimal(self.state.get("infinite_grid_profit", "0"))
            total_profit += profit
            self.state.set("infinite_grid_profit", str(total_profit))

            fills = int(self.state.get("infinite_grid_sell_fills", "0"))
            self.state.set("infinite_grid_sell_fills", str(fills + 1))

            cycles = int(self.state.get("infinite_grid_cycles", "0"))
            self.state.set("infinite_grid_cycles", str(cycles + 1))

    async def check_and_recenter(self, current_price: Decimal) -> bool:
        """Check if price has reached grid edge and recenter if needed.

        Args:
            current_price: Current market price

        Returns:
            True if no recenter needed or recenter successful, False if recenter failed
        """
        if not self._buy_levels or not self._sell_levels:
            return True

        threshold = self.config.recenter_threshold

        # Ensure threshold doesn't exceed list length
        if threshold <= 0:
            return True
        sell_threshold = min(threshold, len(self._sell_levels))
        buy_threshold = min(threshold, len(self._buy_levels))

        # Near top? (price approaching highest sell)
        if current_price >= self._sell_levels[-sell_threshold]:
            log.info(f"Price ${current_price} near top of grid - recentering")
            return await self._recenter(current_price)

        # Near bottom? (price approaching lowest buy)
        if current_price <= self._buy_levels[-buy_threshold]:
            log.info(f"Price ${current_price} near bottom of grid - recentering")
            return await self._recenter(current_price)

        return True

    async def _recenter(self, new_center: Decimal) -> bool:
        """Cancel all orders and rebuild grid around new center.

        Args:
            new_center: New center price for the grid

        Returns:
            True if recenter successful, False otherwise
        """
        log.info(f"RECENTERING grid from ${self._grid_center} to ${new_center}")

        # Cancel all existing orders - abort if cancellation fails
        if not await self._clear_all_grid_orders():
            log.error(
                f"Failed to clear orders during recenter - aborting to prevent order accumulation. "
                f"Grid center remains at ${self._grid_center}"
            )
            return False

        # Update center
        self._grid_center = new_center
        self.state.set("infinite_grid_center", str(new_center))

        # Regenerate levels (floor is preserved)
        self._generate_levels(new_center)

        # Place new orders
        await self._place_initial_orders(new_center)

        recenters = int(self.state.get("infinite_grid_recenters", "0"))
        self.state.set("infinite_grid_recenters", str(recenters + 1))

        log.info(f"Grid recentered. New center: ${new_center}")
        return True

    def pause(self):
        """Pause grid trading."""
        self.state.set("grid_paused", True)
        log.warning("Infinite grid PAUSED")

    def resume(self):
        """Resume grid trading."""
        self.state.set("grid_paused", False)
        log.info("Infinite grid RESUMED")

    async def cancel_all(self) -> bool:
        """Cancel all grid orders.

        Returns:
            True if all orders successfully cancelled, False otherwise
        """
        return await self._clear_all_grid_orders()

    def get_stats(self) -> dict:
        """Get grid statistics."""
        return {
            "center": self._grid_center,
            "buy_levels": len(self._buy_levels),
            "sell_levels": len(self._sell_levels),
            "cycles": int(self.state.get("infinite_grid_cycles", "0")),
            "profit": Decimal(self.state.get("infinite_grid_profit", "0")),
            "recenters": int(self.state.get("infinite_grid_recenters", "0")),
            "paused": self.state.get("grid_paused", False),
        }

    async def maybe_reconcile(self) -> bool:
        """Run reconciliation if enough time has passed. Returns True if reconciliation ran."""
        now = datetime.now()

        # Skip if we reconciled recently
        if self._last_reconcile_time is not None:
            elapsed = (now - self._last_reconcile_time).total_seconds()
            if elapsed < self.RECONCILE_INTERVAL:
                return False

        await self.reconcile_orders()
        self._last_reconcile_time = now
        return True

    async def reconcile_orders(self):
        """Reconcile local state with exchange - detect and fix order discrepancies.

        Checks for:
        1. Ghost orders: In local state as PENDING but not on exchange (may have filled undetected)
        2. Orphan orders: On exchange but not in local state (shouldn't happen, but log if found)
        3. Count mismatch: Total open orders doesn't match expected
        """
        log.info("=" * 50)
        log.info("  ORDER RECONCILIATION CHECK")
        log.info("=" * 50)

        market = self.client.get_market(self.symbol, self.market_type)
        if market is None:
            log.error("Cannot reconcile: market not found")
            return

        try:
            exchange_orders = await self.client.get_active_orders(market_id=market.market_id)
        except Exception as e:
            log.error(f"Cannot reconcile: failed to get exchange orders: {e}")
            return

        # Get local pending/partially filled orders
        local_pending = self.state.get_pending_orders()
        local_partial = self.state.get_orders_by_status(OrderStatus.PARTIALLY_FILLED)
        local_orders = [o for o in local_pending + local_partial if o.market_id == market.market_id]

        # Build lookup maps
        exchange_by_id = {o.id: o for o in exchange_orders}
        exchange_by_price_side = {(o.price, o.side): o for o in exchange_orders}
        local_by_id = {o.id: o for o in local_orders}

        # Count orders by side
        exchange_buys = sum(1 for o in exchange_orders if o.side == OrderSide.BUY)
        exchange_sells = sum(1 for o in exchange_orders if o.side == OrderSide.SELL)
        local_buys = sum(1 for o in local_orders if o.side == OrderSide.BUY)
        local_sells = sum(1 for o in local_orders if o.side == OrderSide.SELL)

        log.info(f"  Exchange orders: {len(exchange_orders)} ({exchange_buys} buys, {exchange_sells} sells)")
        log.info(f"  Local orders:    {len(local_orders)} ({local_buys} buys, {local_sells} sells)")

        issues_found = 0
        orders_fixed = 0

        # Check for ghost orders (in local but not on exchange)
        ghost_orders = []
        for order in local_orders:
            # Try ID match first, then price/side fallback
            on_exchange = exchange_by_id.get(order.id)
            if on_exchange is None:
                on_exchange = exchange_by_price_side.get((order.price, order.side))

            if on_exchange is None:
                ghost_orders.append(order)

        if ghost_orders:
            issues_found += len(ghost_orders)
            log.warning(f"  Found {len(ghost_orders)} GHOST orders (local but not on exchange):")

            # Grace period to avoid processing orders that check_fills already handled
            # or orders that were just placed and haven't synced yet
            grace_cutoff = datetime.now() - timedelta(seconds=30)

            for order in ghost_orders:
                log.warning(f"    - {order.side.value} @ ${order.price} (id={order.id})")

                # Skip if already processed by check_fills (marked FILLED)
                if order.status == OrderStatus.FILLED:
                    log.info(f"    -> Already marked FILLED, skipping")
                    continue

                # Skip recently created orders (may not have synced yet)
                if order.created_at > grace_cutoff:
                    log.info(f"    -> Order too recent, skipping")
                    continue

                # These likely filled without detection - mark as filled and place counter
                log.info(f"    -> Treating as filled, placing counter-order...")
                try:
                    await self._on_full_fill(order)
                    orders_fixed += 1
                except Exception as e:
                    log.error(f"    -> Failed to process ghost order: {e}")

        # Check for orphan orders (on exchange but not in local state)
        orphan_orders = []
        for order in exchange_orders:
            in_local = local_by_id.get(order.id)
            if in_local is None:
                # Try price/side match as fallback
                found = False
                for local_order in local_orders:
                    if local_order.price == order.price and local_order.side == order.side:
                        found = True
                        break
                if not found:
                    orphan_orders.append(order)

        if orphan_orders:
            issues_found += len(orphan_orders)
            log.warning(f"  Found {len(orphan_orders)} ORPHAN orders (on exchange but not in local state):")
            for order in orphan_orders:
                log.warning(f"    - {order.side.value} @ ${order.price} (id={order.id})")
                # Add to local state so we track it
                log.info(f"    -> Adding to local state...")
                try:
                    self.state.save_order(order)
                    orders_fixed += 1
                except Exception as e:
                    log.error(f"    -> Failed to save orphan order: {e}")

        # Summary
        if issues_found == 0:
            log.info("  Status: OK - All orders accounted for")
        else:
            log.warning(f"  Status: {issues_found} issues found, {orders_fixed} fixed")

        log.info("=" * 50)
