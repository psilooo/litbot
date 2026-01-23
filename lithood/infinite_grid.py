# lithood/infinite_grid.py
"""Infinite grid trading engine with adaptive recentering.

DESIGN:
-------
- Grid re-centers when price reaches top or bottom level
- No core sells - all capital in the cycling grid
- Captures volatility through continuous buy/sell cycling
"""

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

    async def initialize(self):
        """Initialize grid centered on current price."""
        entry_price = await self.client.get_mid_price(self.symbol, self.market_type)
        if entry_price is None:
            log.error("Failed to get mid price - cannot initialize infinite grid")
            return

        # Clear stale orders
        await self._clear_all_grid_orders()

        if self.state.get("grid_paused"):
            log.warning("Grid is paused - skipping initialization")
            return

        self._grid_center = entry_price

        log.info(f"Initializing infinite grid. Center: ${entry_price}")

        # Generate levels
        self._generate_levels(entry_price)

        # Place orders
        await self._place_initial_orders(entry_price)

        self.state.set("infinite_grid_center", str(entry_price))
        log.info("Infinite grid initialized")

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

    async def _clear_all_grid_orders(self):
        """Cancel all orders on the exchange."""
        market = self.client.get_market(self.symbol, self.market_type)
        if market is None:
            return
        try:
            cancelled = await self.client.cancel_all_orders(market_id=market.market_id)
            if cancelled > 0:
                log.info(f"Cancelled {cancelled} orders during grid initialization")
        except Exception as e:
            log.warning(f"Failed to cancel orders: {e}")

        # Clear local state
        for order in self.state.get_pending_orders():
            if order.market_id == market.market_id:
                self.state.mark_cancelled(order.id)

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

        active_map = {(o.price, o.side): o for o in active_orders}

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

            key = (order.price, order.side)
            exchange_order = active_map.get(key)

            if exchange_order is None:
                # Order is no longer active - fully filled
                await self._on_fill(order, order.size)
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
        """Handle fill - place counter-order."""
        self.state.mark_filled(order.id, filled_size)

        if self.state.get("grid_paused"):
            return

        spacing = self.config.level_spacing_pct

        if order.side == OrderSide.BUY:
            # Buy filled -> sell 2% higher
            sell_price = (order.price * (1 + spacing)).quantize(Decimal("0.0001"))
            log.info(f"BUY FILLED @ ${order.price} -> sell @ ${sell_price}")
            await self._place_grid_sell(sell_price)

            fills = int(self.state.get("infinite_grid_buy_fills", "0"))
            self.state.set("infinite_grid_buy_fills", str(fills + 1))

        else:
            # Sell filled -> buy 2% lower
            buy_price = (order.price * (1 - spacing)).quantize(Decimal("0.0001"))
            profit = filled_size * order.price * spacing  # Approximate profit

            log.info(f"SELL FILLED @ ${order.price} -> buy @ ${buy_price} (profit ~${profit:.2f})")
            await self._place_grid_buy(buy_price)

            # Update profit tracking
            total_profit = Decimal(self.state.get("infinite_grid_profit", "0"))
            total_profit += profit
            self.state.set("infinite_grid_profit", str(total_profit))

            fills = int(self.state.get("infinite_grid_sell_fills", "0"))
            self.state.set("infinite_grid_sell_fills", str(fills + 1))

            cycles = int(self.state.get("infinite_grid_cycles", "0"))
            self.state.set("infinite_grid_cycles", str(cycles + 1))

    async def check_and_recenter(self, current_price: Decimal):
        """Check if price has reached grid edge and recenter if needed."""
        if not self._buy_levels or not self._sell_levels:
            return

        threshold = self.config.recenter_threshold

        # Ensure threshold doesn't exceed list length
        if threshold <= 0:
            return
        sell_threshold = min(threshold, len(self._sell_levels))
        buy_threshold = min(threshold, len(self._buy_levels))

        # Near top? (price approaching highest sell)
        if current_price >= self._sell_levels[-sell_threshold]:
            log.info(f"Price ${current_price} near top of grid - recentering")
            await self._recenter(current_price)
            return

        # Near bottom? (price approaching lowest buy)
        if current_price <= self._buy_levels[-buy_threshold]:
            log.info(f"Price ${current_price} near bottom of grid - recentering")
            await self._recenter(current_price)
            return

    async def _recenter(self, new_center: Decimal):
        """Cancel all orders and rebuild grid around new center."""
        log.info(f"RECENTERING grid from ${self._grid_center} to ${new_center}")

        # Cancel all existing orders
        await self._clear_all_grid_orders()

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

    def pause(self):
        """Pause grid trading."""
        self.state.set("grid_paused", True)
        log.warning("Infinite grid PAUSED")

    def resume(self):
        """Resume grid trading."""
        self.state.set("grid_paused", False)
        log.info("Infinite grid RESUMED")

    async def cancel_all(self) -> int:
        """Cancel all grid orders."""
        await self._clear_all_grid_orders()
        return 0  # Count handled in _clear_all_grid_orders

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
