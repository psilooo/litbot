# lithood/hedge.py
"""Perp hedge manager for downside protection and funding yield.

v2 CHANGES:
-----------
- Dynamic stop-loss: 16% above hedge entry price (not fixed $1.95)
- Smart re-entry: At bot entry price OR 5% pullback from recent high
- Trailing stop: Locks in gains when price drops significantly
- Tracks recent_high for pullback detection

v2.1 CHANGES:
-------------
- Exchange-native stop-loss orders (instead of bot-managed polling)
- Stop-loss is placed on exchange after opening short
- Trailing stop cancels old and places new stop-loss order
"""

import asyncio
import time
from decimal import Decimal
from typing import Optional

from lithood.client import LighterClient
from lithood.state import StateManager
from lithood.types import OrderSide, OrderType, MarketType, Position, Order
from lithood.config import HEDGE_CONFIG, PERP_SYMBOL
from lithood.logger import log


class HedgeManager:
    """Manages the perp short hedge position with dynamic stop/re-entry."""

    def __init__(self, client: LighterClient, state: StateManager):
        self.client = client
        self.state = state
        self.symbol = PERP_SYMBOL  # "LIT" for perp market
        self.config = HEDGE_CONFIG

    async def initialize(self):
        """Open initial hedge short if not already active."""
        if not self.config["enabled"]:
            log.info("Hedge disabled in config")
            return

        # Get current price for bot entry reference
        current_price = await self.client.get_mid_price(self.symbol, MarketType.PERP)
        if current_price:
            # Store bot entry price for re-entry reference
            if not self.state.get("bot_entry_price"):
                self.state.set("bot_entry_price", str(current_price))
            # Initialize recent high tracking
            if not self.state.get("hedge_recent_high"):
                self.state.set("hedge_recent_high", str(current_price))

        if self.state.get("hedge_active"):
            log.info("Hedge already active")
            # Ensure stop price is set based on stored entry
            entry_str = self.state.get("hedge_entry_price")
            if entry_str:
                entry = Decimal(entry_str)
                stop_price = entry * (1 + self.config["stop_loss_pct"])
                self.state.set("hedge_stop_price", str(stop_price))

                # Check if stop-loss order exists on exchange, place if missing
                existing_stop = await self._find_stop_loss_order()
                if existing_stop is None:
                    log.warning("No stop-loss order found on exchange - placing one now")
                    size_str = self.state.get("hedge_size")
                    size = Decimal(size_str) if size_str else self.config["short_size"]

                    stop_order = await self.client.place_stop_loss_order(
                        symbol=self.symbol,
                        market_type=MarketType.PERP,
                        side=OrderSide.BUY,
                        size=size,
                        trigger_price=stop_price,
                        reduce_only=True,
                    )
                    if stop_order:
                        self.state.set("hedge_stop_order_placed", True)
                        log.info(f"Stop-loss order placed @ ${stop_price:.3f}")
                    else:
                        log.error("Failed to place stop-loss order")
                        self.state.set("hedge_stop_order_placed", False)
                else:
                    log.info(f"Existing stop-loss order found @ ${existing_stop.price}")
                    self.state.set("hedge_stop_order_placed", True)
            return

        await self.open_short()

    async def open_short(self):
        """Open the hedge short position with exchange-native stop-loss."""
        size = self.config["short_size"]

        log.info(f"Opening hedge short: {size} LIT")

        order = await self.client.place_market_order(
            symbol=self.symbol,
            market_type=MarketType.PERP,
            side=OrderSide.SELL,
            size=size,
        )

        if order is None:
            log.error("Failed to open hedge short - order not placed")
            return

        # Brief wait for position to be visible
        await asyncio.sleep(1)

        # Get entry price from position
        positions = await self.client.get_positions()
        perp_pos = self._find_position(positions)

        if perp_pos:
            entry_price = perp_pos.entry_price
            actual_size = abs(perp_pos.size)  # position size is negative for short
            if actual_size != size:
                log.warning(f"Expected short size {size}, got {actual_size}")
                size = actual_size  # Use actual size for state
        else:
            # Fallback to mid price if position not yet visible
            mid_price = await self.client.get_mid_price(self.symbol, MarketType.PERP)
            if mid_price:
                entry_price = mid_price
                log.warning("Using mid price as entry estimate - position not yet visible")
            else:
                log.error("Cannot determine entry price - aborting hedge open")
                return

        # Calculate dynamic stop-loss: 16% above entry
        stop_price = entry_price * (1 + self.config["stop_loss_pct"])

        self.state.set("hedge_active", True)
        self.state.set("hedge_entry_price", str(entry_price))
        self.state.set("hedge_size", str(size))
        self.state.set("hedge_stop_price", str(stop_price))
        self.state.log_hedge_action("open", entry_price, size)

        log.info(f"Hedge opened: {size} LIT short @ ${entry_price}")

        # Place exchange-native stop-loss order (BUY to close short)
        stop_order = await self.client.place_stop_loss_order(
            symbol=self.symbol,
            market_type=MarketType.PERP,
            side=OrderSide.BUY,  # Buy to close short
            size=size,
            trigger_price=stop_price,
            reduce_only=True,
        )

        if stop_order:
            self.state.set("hedge_stop_order_placed", True)
            log.info(f"  Stop-loss order placed on exchange @ ${stop_price:.3f} (+{self.config['stop_loss_pct']*100:.0f}%)")
        else:
            log.error("Failed to place stop-loss order on exchange - will use bot-managed fallback")
            self.state.set("hedge_stop_order_placed", False)

    async def close_short(self, reason: str = "manual"):
        """Close the hedge short position."""
        if not self.state.get("hedge_active"):
            log.warning("No active hedge to close")
            return

        size_str = self.state.get("hedge_size")
        size = Decimal(size_str) if size_str else self.config["short_size"]
        entry_price_str = self.state.get("hedge_entry_price")
        entry_price = Decimal(entry_price_str) if entry_price_str else Decimal("0")

        log.info(f"Closing hedge short: {size} LIT (reason: {reason})")

        # Buy to close
        order = await self.client.place_market_order(
            symbol=self.symbol,
            market_type=MarketType.PERP,
            side=OrderSide.BUY,
            size=size,
        )

        if order is None:
            log.error("Failed to close hedge short - order not placed")
            return

        # Verify position is closed
        await asyncio.sleep(1)  # Brief wait for position update
        positions = await self.client.get_positions()
        perp_pos = self._find_position(positions)
        if perp_pos and perp_pos.size != 0:
            log.error(f"Position still exists after close attempt: {perp_pos.size} - state not updated")
            return  # Don't update state if close failed

        # Calculate PnL
        current_price = await self.client.get_mid_price(self.symbol, MarketType.PERP)
        if current_price is None:
            log.error("Failed to get current price for PnL calculation")
            current_price = Decimal("0")
            pnl = Decimal("0")
        else:
            pnl = (entry_price - current_price) * size

        self.state.set("hedge_active", False)
        self.state.set("hedge_stop_order_placed", False)
        self.state.log_hedge_action(reason, current_price, size, pnl=pnl)

        # Track total hedge PnL
        total_pnl_str = self.state.get("total_hedge_pnl")
        total_pnl = Decimal(total_pnl_str) if total_pnl_str else Decimal("0")
        total_pnl += pnl
        self.state.set("total_hedge_pnl", str(total_pnl))

        # Clean up orphan stop-loss order (position closed, stop no longer needed)
        await self._cancel_stop_loss_order()

        log.info(f"Hedge closed @ ${current_price}, PnL: ${pnl:.2f}")

    def _find_position(self, positions: list[Position]) -> Optional[Position]:
        """Find our perp position."""
        market = self.client.get_market(self.symbol, MarketType.PERP)
        if market is None:
            log.error(f"Market not found: {self.symbol} perp")
            return None
        for p in positions:
            if p.market_id == market.market_id and p.size != 0:
                return p
        return None

    async def _find_stop_loss_order(self) -> Optional[Order]:
        """Find the active stop-loss order for this hedge."""
        market = self.client.get_market(self.symbol, MarketType.PERP)
        if market is None:
            return None

        orders = await self.client.get_active_orders(market_id=market.market_id)
        for o in orders:
            # Stop-loss for short is a BUY order with STOP_LOSS type
            if o.side == OrderSide.BUY and o.order_type == OrderType.STOP_LOSS:
                return o
        return None

    async def _cancel_stop_loss_order(self) -> bool:
        """Cancel the active stop-loss order."""
        stop_order = await self._find_stop_loss_order()
        if stop_order is None:
            return True  # No order to cancel

        market = self.client.get_market(self.symbol, MarketType.PERP)
        success = await self.client.cancel_order(stop_order.id, market_id=market.market_id)
        if success:
            log.info(f"Cancelled stop-loss order {stop_order.id}")
        else:
            log.error(f"Failed to cancel stop-loss order {stop_order.id}")
        return success

    async def _update_stop_loss_order(self, new_stop_price: Decimal):
        """Update stop-loss by cancelling old and placing new."""
        size_str = self.state.get("hedge_size")
        size = Decimal(size_str) if size_str else self.config["short_size"]

        # Cancel existing stop-loss
        await self._cancel_stop_loss_order()

        # Place new stop-loss at lower price
        stop_order = await self.client.place_stop_loss_order(
            symbol=self.symbol,
            market_type=MarketType.PERP,
            side=OrderSide.BUY,
            size=size,
            trigger_price=new_stop_price,
            reduce_only=True,
        )

        if stop_order:
            self.state.set("hedge_stop_price", str(new_stop_price))
            self.state.set("hedge_stop_order_placed", True)
        else:
            log.error("Failed to place updated stop-loss order")
            self.state.set("hedge_stop_order_placed", False)

    async def check(self, current_price: Decimal):
        """Check hedge status - detect stop trigger and smart re-entry.

        v2.1 LOGIC (exchange-native stop-loss):
        ---------------------------------------
        When ACTIVE:
        - Exchange handles stop-loss automatically
        - We detect if position closed and update state
        - Trailing stop: cancel old, place new stop at lower price

        When INACTIVE:
        - Re-entry condition 1: Price at or below bot entry price
        - Re-entry condition 2: Price pulled back 5% from recent high
        - Both require cooldown to have passed
        """
        if not self.config["enabled"]:
            return

        # Update recent high tracking
        recent_high_str = self.state.get("hedge_recent_high", "0")
        recent_high = Decimal(recent_high_str) if recent_high_str else current_price
        if current_price > recent_high:
            recent_high = current_price
            self.state.set("hedge_recent_high", str(recent_high))

        if self.state.get("hedge_active"):
            await self._manage_active_hedge(current_price)
        else:
            await self._check_reentry(current_price, recent_high)

    async def _manage_active_hedge(self, current_price: Decimal):
        """Manage an active hedge - detect stop trigger and trailing stop.

        With exchange-native stop-loss:
        - Exchange automatically closes position when stop triggers
        - We detect this by checking if position is gone
        - Trailing stop: cancel old stop, place new one at lower price
        """
        # Check if stop-loss was triggered by exchange (position closed)
        positions = await self.client.get_positions()
        perp_pos = self._find_position(positions)

        if perp_pos is None or perp_pos.size == 0:
            # Position is gone - stop-loss was triggered by exchange
            entry_price_str = self.state.get("hedge_entry_price", "0")
            entry_price = Decimal(entry_price_str)
            stop_price_str = self.state.get("hedge_stop_price", "0")
            stop_price = Decimal(stop_price_str)
            size_str = self.state.get("hedge_size")
            size = Decimal(size_str) if size_str else self.config["short_size"]

            # Calculate PnL (closed at stop price)
            pnl = (entry_price - stop_price) * size

            log.warning(f"STOP-LOSS triggered by exchange @ ~${stop_price} (entry ${entry_price})")

            self.state.set("hedge_active", False)
            self.state.set("hedge_stop_order_placed", False)
            self.state.set("last_stop_loss_time", time.time())
            self.state.log_hedge_action("stop_loss", stop_price, size, pnl=pnl)

            # Track total hedge PnL
            total_pnl_str = self.state.get("total_hedge_pnl")
            total_pnl = Decimal(total_pnl_str) if total_pnl_str else Decimal("0")
            total_pnl += pnl
            self.state.set("total_hedge_pnl", str(total_pnl))

            log.info(f"Hedge closed by exchange, PnL: ${pnl:.2f}")
            return

        # Position still active - check trailing stop logic
        entry_str = self.state.get("hedge_entry_price", "0")
        entry_price = Decimal(entry_str)
        stop_str = self.state.get("hedge_stop_price")
        stop_price = Decimal(stop_str) if stop_str else entry_price * (1 + self.config["stop_loss_pct"])

        # If price dropped 10% below entry, trail the stop down
        if current_price < entry_price * Decimal("0.90"):
            new_stop = current_price * (1 + self.config["stop_loss_pct"])
            if new_stop < stop_price:
                # Update stop-loss on exchange (cancel old, place new)
                await self._update_stop_loss_order(new_stop)
                log.info(f"Trailing stop updated: ${stop_price:.3f} -> ${new_stop:.3f}")

    async def _check_reentry(self, current_price: Decimal, recent_high: Decimal):
        """Check if we should re-enter the hedge.

        Re-entry conditions (either one, AFTER cooldown):
        1. Price at or below bot entry price
        2. Price pulled back 5% from recent high
        """
        # Check cooldown
        last_stop = self.state.get("last_stop_loss_time", 0)
        if last_stop:
            cooldown_hours = self.config["re_entry_cooldown_hours"]
            if (time.time() - last_stop) < cooldown_hours * 3600:
                return  # Still in cooldown

        # Get bot entry price for reference
        bot_entry_str = self.state.get("bot_entry_price")
        if not bot_entry_str:
            log.warning("No bot entry price set - cannot check re-entry conditions")
            return
        bot_entry_price = Decimal(bot_entry_str)

        # Condition 1: Price at or below bot entry price
        if current_price <= bot_entry_price:
            log.info(f"Re-entry: Price ${current_price} <= bot entry ${bot_entry_price}")
            await self.open_short()
            return

        # Condition 2: Price pulled back 5% from recent high
        pullback_pct = self.config["reentry_pullback_pct"]
        pullback_threshold = recent_high * (1 - pullback_pct)
        if current_price <= pullback_threshold:
            log.info(f"Re-entry: 5% pullback from ${recent_high} -> threshold ${pullback_threshold:.3f}")
            await self.open_short()
            # Reset recent high after re-entry
            self.state.set("hedge_recent_high", str(current_price))

    async def check_funding(self):
        """Check funding rate - pause if negative too long."""
        if not self.config["enabled"] or not self.state.get("hedge_active"):
            return

        funding = await self.client.get_funding_rate(self.symbol)

        if funding is None:
            log.warning("Failed to get funding rate")
            return

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
            "stop_price": self.state.get("hedge_stop_price"),
            "size": self.state.get("hedge_size"),
            "total_pnl": self.state.get("total_hedge_pnl", 0),
            "recent_high": self.state.get("hedge_recent_high"),
        }
