#!/usr/bin/env python3
# scripts/run_infinite_grid.py
"""Infinite Grid Bot - maximizes volatility capture without core sells.

This bot uses an adaptive grid that:
1. Re-centers when price reaches the edge
2. Captures volatility through continuous buy/sell cycling
3. No floor - grid cycles freely at all price levels
"""

import argparse
import asyncio
import os
import signal
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from lithood.client import LighterClient
from lithood.state import StateManager
from lithood.infinite_grid import InfiniteGridEngine, InfiniteGridConfig
from lithood.types import MarketType
from lithood.config import POLL_INTERVAL_SECONDS, SPOT_SYMBOL
from lithood.logger import log
from lithood.retry import RETRY_PERSISTENT, calculate_delay


class InfiniteGridBot:
    """Infinite grid bot - no core sells, all capital cycling."""

    def __init__(self, amount: Decimal = Decimal("350"), levels: int = 15):
        self.client = LighterClient()
        db_path = os.getenv("BOT_STATE_DB", os.path.join(os.path.dirname(__file__), "..", "infinite_grid_state.db"))
        self.state = StateManager(db_path=db_path)
        self.grid: InfiniteGridEngine = None
        self._running = False
        self._stopped = False
        self._start_time = None
        self._consecutive_failures = 0
        self._amount = amount
        self._levels = levels

    async def start(self):
        """Initialize and start the bot."""
        self._start_time = datetime.now()

        log.info("=" * 60)
        log.info("  INFINITE GRID BOT")
        log.info("  Maximum volatility capture")
        log.info(f"  Amount: {self._amount} LIT | Levels: {self._levels}")
        log.info("=" * 60)

        await self.client.connect()

        # Configure grid - all available LIT for cycling
        config = InfiniteGridConfig(
            num_levels=self._levels,
            level_spacing_pct=Decimal("0.02"),
            lit_per_order=self._amount,
            total_grid_lit=self._amount * self._levels * 2,
            recenter_threshold=2,
        )

        self.grid = InfiniteGridEngine(self.client, self.state, config)

        await self._sync_state()
        await self.grid.initialize()

        log.info("Infinite Grid Bot initialized. Starting main loop...")
        self._running = True

        current_price = await self.client.get_mid_price(SPOT_SYMBOL, MarketType.SPOT)
        if current_price:
            await self._print_status(current_price)

        await self._run_loop()

    async def _sync_state(self):
        """Sync local state with exchange."""
        log.info("Syncing state with exchange...")
        account = await self.client.get_account()
        if account:
            log.info(f"Account collateral: ${account.collateral}")
            self.state.set("usdc_balance", str(account.available_balance))
        log.info("State sync complete")

    async def _run_loop(self):
        """Main bot loop."""
        last_status_time = 0
        status_interval = 60

        while self._running:
            try:
                if self._consecutive_failures >= 3:
                    if not await self.client.ensure_connected():
                        delay = calculate_delay(self._consecutive_failures, RETRY_PERSISTENT)
                        log.warning(f"Connection failed, waiting {delay:.1f}s...")
                        await asyncio.sleep(delay)
                        continue

                current_price = await self.client.get_mid_price(SPOT_SYMBOL, MarketType.SPOT)
                if current_price is None:
                    self._consecutive_failures += 1
                    delay = min(2 ** self._consecutive_failures, 120)
                    await asyncio.sleep(delay)
                    continue

                self._consecutive_failures = 0

                # Core loop
                await self.grid.check_fills()
                await self.grid.check_and_recenter(current_price)

                if datetime.now().timestamp() - last_status_time >= status_interval:
                    await self._print_status(current_price)
                    last_status_time = datetime.now().timestamp()

                await asyncio.sleep(POLL_INTERVAL_SECONDS)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._consecutive_failures += 1
                delay = min(2 ** self._consecutive_failures, 120)
                log.error(f"Error: {e}, backing off {delay}s")
                await asyncio.sleep(delay)

    async def _print_status(self, price: Decimal):
        """Print status."""
        cycles = int(self.state.get("infinite_grid_cycles", "0"))
        profit = Decimal(self.state.get("infinite_grid_profit", "0"))
        recenters = int(self.state.get("infinite_grid_recenters", "0"))
        buy_fills = int(self.state.get("infinite_grid_buy_fills", "0"))
        sell_fills = int(self.state.get("infinite_grid_sell_fills", "0"))
        center = Decimal(self.state.get("infinite_grid_center", "0"))

        runtime = ""
        if self._start_time:
            delta = datetime.now() - self._start_time
            hours, remainder = divmod(int(delta.total_seconds()), 3600)
            minutes, _ = divmod(remainder, 60)
            runtime = f"{hours}h {minutes}m"

        print()
        print("=" * 60)
        print(f"  INFINITE GRID | {datetime.now().strftime('%H:%M:%S')} | Runtime: {runtime}")
        print("=" * 60)
        print(f"  PRICE:  ${price:.4f}")
        print(f"  CENTER: ${center:.4f}")
        print("-" * 60)
        print(f"  Cycles:     {cycles:>6}")
        print(f"  Buy fills:  {buy_fills:>6}")
        print(f"  Sell fills: {sell_fills:>6}")
        print(f"  Recenters:  {recenters:>6}")
        print(f"  Profit:     ${profit:>10,.2f}")
        print("=" * 60)
        print()

    async def stop(self):
        """Stop gracefully."""
        if self._stopped:
            return
        self._stopped = True
        log.info("Stopping infinite grid bot...")
        self._running = False
        await asyncio.sleep(POLL_INTERVAL_SECONDS + 1)
        try:
            await self.client.close()
        except:
            pass
        finally:
            self.state.close()
        log.info("Bot stopped")


def parse_args():
    parser = argparse.ArgumentParser(description="Infinite Grid Bot")
    parser.add_argument(
        "--amount",
        type=float,
        default=350,
        help="LIT per order (default: 350)"
    )
    parser.add_argument(
        "--levels",
        type=int,
        default=15,
        help="Number of buy/sell levels (default: 15)"
    )
    return parser.parse_args()


async def main():
    args = parse_args()
    bot = InfiniteGridBot(
        amount=Decimal(str(args.amount)),
        levels=args.levels
    )
    stop_requested = False

    def request_stop():
        nonlocal stop_requested
        if not stop_requested:
            stop_requested = True
            asyncio.create_task(bot.stop())

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, request_stop)

    try:
        await bot.start()
    finally:
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
