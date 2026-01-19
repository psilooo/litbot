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

        if order is None:
            log.error("Failed to open hedge short - order not placed")
            return

        # Get entry price from position
        positions = await self.client.get_positions()
        perp_pos = self._find_position(positions)

        if perp_pos:
            entry_price = perp_pos.entry_price
        else:
            # Fallback to mid price if position not yet visible
            mid_price = await self.client.get_mid_price(self.symbol, MarketType.PERP)
            if mid_price is None:
                log.error("Failed to get entry price for hedge")
                entry_price = Decimal("0")
            else:
                entry_price = mid_price

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

        if order is None:
            log.error("Failed to close hedge short - order not placed")
            return

        # Calculate PnL
        current_price = await self.client.get_mid_price(self.symbol, MarketType.PERP)
        if current_price is None:
            log.error("Failed to get current price for PnL calculation")
            current_price = Decimal("0")
            pnl = Decimal("0")
        else:
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
        if market is None:
            log.error(f"Market not found: {self.symbol} perp")
            return None
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
            "size": self.state.get("hedge_size"),
            "total_pnl": self.state.get("total_hedge_pnl", 0),
        }
