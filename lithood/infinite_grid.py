# lithood/infinite_grid.py
"""Infinite grid trading engine with adaptive recentering.

DESIGN:
-------
- Grid re-centers when price reaches top or bottom level
- Trailing sell floor prevents selling all LIT in a rally
- Floor ratchets up with each profitable cycle
- No core sells - all capital in the cycling grid
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
        lit_per_order: Decimal = Decimal("400"),
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
        self._sell_floor: Decimal = Decimal("0")  # Trailing floor

    async def initialize(self):
        """Initialize grid centered on current price with trailing floor."""
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

        # Initialize sell floor at current price (will ratchet up)
        stored_floor = self.state.get("infinite_grid_sell_floor")
        if stored_floor:
            self._sell_floor = Decimal(stored_floor)
            # Floor should never be below current price on fresh start
            if self._sell_floor < entry_price:
                self._sell_floor = entry_price
        else:
            self._sell_floor = entry_price
        self.state.set("infinite_grid_sell_floor", str(self._sell_floor))

        log.info(f"Initializing infinite grid. Center: ${entry_price}, Floor: ${self._sell_floor}")

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

        # Generate sell levels (above center, respecting floor)
        self._sell_levels = []
        for i in range(1, self.config.num_levels + 1):
            price = center * ((1 + spacing) ** i)
            price = price.quantize(Decimal("0.0001"))
            # Only add if above sell floor
            if price >= self._sell_floor:
                self._sell_levels.append(price)

        log.info(f"Generated {len(self._buy_levels)} buy levels, {len(self._sell_levels)} sell levels")

        if not self._sell_levels:
            log.warning(f"No sell levels generated - floor ${self._sell_floor} may be too high")

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
        """Place a grid sell order (respects floor)."""
        if self.state.get("grid_paused"):
            return None

        # Enforce sell floor
        if price < self._sell_floor:
            log.debug(f"Skipping sell at ${price} - below floor ${self._sell_floor}")
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
        """Check for fills and cycle orders."""
        pass  # Implemented in Task 3

    async def _maybe_recenter(self, current_price: Decimal):
        """Check if grid needs recentering."""
        pass  # Implemented in Task 4
