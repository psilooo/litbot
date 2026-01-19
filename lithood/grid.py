# lithood/grid.py
"""Spot grid trading engine."""

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
        """Check for filled orders and trigger cycling."""
        if self.state.get("grid_paused"):
            return

        # Get active orders from exchange
        market = self.client.get_market(self.symbol, self.market_type)
        if market is None:
            log.error(f"Market not found: {self.symbol}_{self.market_type.value}")
            return

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
            # Buy filled -> place sell at +3%
            sell_price = order.price * (1 + GRID_SPREAD)
            sell_size = order.size  # Sell the LIT we just bought

            log.info(f"Buy filled @ ${order.price} -> placing sell @ ${sell_price}")
            await self._place_grid_sell(sell_price, sell_size)

        else:
            # Sell filled -> place buy at -3%, keeping 3% profit
            buy_price = order.price * (1 - GRID_SPREAD)
            usdc_received = order.size * order.price
            usdc_to_reinvest = usdc_received * (1 - GRID_PROFIT_RETAIN)

            log.info(f"Sell filled @ ${order.price} -> placing buy @ ${buy_price}")
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
        stats["total_profit"] = self.state.get("total_grid_profit", 0)
        stats["paused"] = self.state.get("grid_paused", False)
        return stats
