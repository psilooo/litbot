#!/usr/bin/env python3
# scripts/run_bot.py
"""Main entry point for the LIT Grid Trading Bot."""

import asyncio
import signal
import sys
import time
from decimal import Decimal
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from lithood.client import LighterClient
from lithood.state import StateManager
from lithood.grid import GridEngine
from lithood.hedge import HedgeManager
from lithood.floor import FloorProtection
from lithood.types import MarketType
from lithood.config import POLL_INTERVAL_SECONDS
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
        if account is not None:
            log.info(f"Account collateral: ${account.collateral}")
            log.info(f"Available balance: ${account.available_balance}")
        else:
            log.warning("Could not retrieve account information")

        # Check for existing positions
        positions = await self.client.get_positions()
        for p in positions:
            if p.size < 0:
                log.info(f"Found existing short: {p.size} LIT @ ${p.entry_price}")
                self.state.set("hedge_active", True)
                self.state.set("hedge_entry_price", str(p.entry_price))
                self.state.set("hedge_size", str(abs(p.size)))

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

                if current_price is None:
                    log.warning("Could not get current price - skipping this cycle")
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)
                    continue

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

        # Extract grid stats - use filled_orders (from state.get_grid_stats())
        filled_orders = grid_stats.get("filled_orders", 0)
        total_profit = grid_stats.get("total_profit", Decimal("0"))
        grid_paused = grid_stats.get("paused", False)

        # Extract hedge stats - total_pnl may be 0, None, or a string
        hedge_active = hedge_stats.get("active", False)
        total_pnl_raw = hedge_stats.get("total_pnl", 0)
        if total_pnl_raw is None:
            total_pnl = Decimal("0")
        elif isinstance(total_pnl_raw, str):
            total_pnl = Decimal(total_pnl_raw)
        else:
            total_pnl = Decimal(str(total_pnl_raw))

        log.info("-" * 40)
        log.info(f"STATUS @ ${price}")
        log.info(f"  Grid: {filled_orders} fills, "
                 f"${total_profit:.2f} profit, "
                 f"paused={grid_paused}")
        log.info(f"  Hedge: active={hedge_active}, "
                 f"${total_pnl:.2f} PnL")
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
