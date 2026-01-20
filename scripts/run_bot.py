#!/usr/bin/env python3
# scripts/run_bot.py
"""Main entry point for the LIT Grid Trading Bot v2."""

import asyncio
import os
import signal
import sys
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from lithood.client import LighterClient
from lithood.state import StateManager
from lithood.grid import GridEngine
from lithood.hedge import HedgeManager
from lithood.floor import FloorProtection
from lithood.types import MarketType, OrderSide
from lithood.config import (
    POLL_INTERVAL_SECONDS, SPOT_SYMBOL, PERP_SYMBOL,
    ALLOCATION, HEDGE_CONFIG, CORE_TARGETS,
)
from lithood.logger import log
from lithood.retry import RETRY_PERSISTENT, calculate_delay


class LitGridBot:
    """Main bot orchestrator."""

    def __init__(self):
        self.client = LighterClient()
        db_path = os.getenv("BOT_STATE_DB", os.path.join(os.path.dirname(__file__), "..", "bot_state.db"))
        self.state = StateManager(db_path=db_path)
        self.grid: GridEngine = None
        self.hedge: HedgeManager = None
        self.floor: FloorProtection = None
        self._running = False
        self._stopped = False
        self._start_time = None
        self._consecutive_failures = 0
        self._last_successful_cycle = None

    async def start(self):
        """Initialize and start the bot."""
        self._start_time = datetime.now()

        log.info("=" * 60)
        log.info("  LIT GRID TRADING BOT v2.0")
        log.info("  Dynamic Grid + Smart Hedge")
        log.info("=" * 60)

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

        # Place core sell orders ($2.50 - $4.50)
        await self._place_core_sell_orders()

        log.info("Bot initialized. Starting main loop...")
        self._running = True

        # Show initial status
        current_price = await self.client.get_mid_price(SPOT_SYMBOL, MarketType.SPOT)
        if current_price:
            await self._print_status(current_price)

        # Main loop
        await self._run_loop()

    async def _sync_state(self):
        """Sync local state with exchange."""
        log.info("Syncing state with exchange...")

        account = await self.client.get_account()
        if account is not None:
            log.info(f"Account collateral: ${account.collateral}")
            log.info(f"Available balance: ${account.available_balance}")
            # Store USDC balance
            self.state.set("usdc_balance", str(account.available_balance))
        else:
            log.warning("Could not retrieve account information")

        # Check for existing LIT perp position (hedge)
        try:
            perp_market = self.client.get_market(PERP_SYMBOL, MarketType.PERP)
            positions = await self.client.get_positions()
            found_hedge = False
            for p in positions:
                # Only check the LIT perp market for hedge position
                if p.market_id == perp_market.market_id and p.size < 0:
                    log.info(f"Found existing hedge short: {p.size} LIT @ ${p.entry_price}")
                    self.state.set("hedge_active", True)
                    self.state.set("hedge_entry_price", str(p.entry_price))
                    self.state.set("hedge_size", str(abs(p.size)))
                    # Calculate stop price for existing position
                    stop_price = p.entry_price * (1 + HEDGE_CONFIG["stop_loss_pct"])
                    self.state.set("hedge_stop_price", str(stop_price))
                    found_hedge = True
            # Reset hedge state if no position found (clears stale state from previous runs)
            if not found_hedge:
                self.state.set("hedge_active", False)
        except Exception as e:
            log.error(f"Failed to sync positions: {e}")

        # Check for existing orders (only LIT markets)
        spot_market = self.client.get_market(SPOT_SYMBOL, MarketType.SPOT)
        perp_market = self.client.get_market(PERP_SYMBOL, MarketType.PERP)
        spot_orders = await self.client.get_active_orders(market_id=spot_market.market_id)
        perp_orders = await self.client.get_active_orders(market_id=perp_market.market_id)
        orders = spot_orders + perp_orders
        log.info(f"Found {len(orders)} existing LIT orders")

        log.info("State sync complete")

    async def _place_core_sell_orders(self):
        """Place core position sell orders at $2.50, $3.00, $3.50, $4.00, $4.50.

        These are the 8,000 LIT core position that we hold for upside.
        Orders are placed once at startup and sit on the book.
        """
        # Check if core orders already placed (from previous run)
        if self.state.get("core_orders_placed"):
            log.info("Core sell orders already placed (from previous run)")
            return

        log.info("Placing core sell orders...")

        for target in CORE_TARGETS:
            price = target["price"]
            lit_amount = target["lit"]

            order = await self.client.place_limit_order(
                symbol=SPOT_SYMBOL,
                market_type=MarketType.SPOT,
                side=OrderSide.SELL,
                price=price,
                size=lit_amount,
            )

            if order:
                log.info(f"Core SELL placed: {lit_amount} LIT @ ${price}")
            else:
                log.error(f"Failed to place core sell at ${price}")

        self.state.set("core_orders_placed", True)
        log.info(f"Placed {len(CORE_TARGETS)} core sell orders ($2.50 - $4.50)")

    async def _run_loop(self):
        """Main bot loop with exponential backoff for outages."""
        last_status_time = 0
        status_interval = 60  # Show status every 60 seconds

        while self._running:
            # Check if bot was halted by floor protection
            try:
                if self.state.get("bot_halted"):
                    log.info("Bot halted by floor protection")
                    break
            except Exception:
                break  # Database closed, exit gracefully
            try:
                # Check if we should stop
                if not self._running:
                    break

                # Health check and reconnection if needed
                if self._consecutive_failures >= 3:
                    log.warning(f"Multiple failures ({self._consecutive_failures}), checking connection...")
                    if not await self.client.ensure_connected():
                        # Connection still down, use exponential backoff
                        delay = calculate_delay(self._consecutive_failures, RETRY_PERSISTENT)
                        log.warning(f"Connection failed, waiting {delay:.1f}s before retry...")
                        await asyncio.sleep(delay)
                        continue
                    else:
                        # Reconnected - reconcile state
                        log.info("Connection restored, reconciling state...")
                        await self._reconcile_after_outage()

                # Get current price
                current_price = await self.client.get_mid_price(SPOT_SYMBOL, MarketType.SPOT)

                if current_price is None:
                    self._consecutive_failures += 1
                    delay = min(2 ** self._consecutive_failures, 120)  # Max 2 min
                    log.warning(f"Could not get price (failure #{self._consecutive_failures}), waiting {delay}s...")
                    await asyncio.sleep(delay)
                    continue

                # Success - reset failure counter
                if self._consecutive_failures > 0:
                    log.info(f"Connection restored after {self._consecutive_failures} failures")
                self._consecutive_failures = 0
                self._last_successful_cycle = time.time()

                # Check in priority order
                await self.floor.check(current_price)        # 1. Floor protection
                await self.hedge.check(current_price)        # 2. Hedge (disabled but safe to call)
                await self.hedge.check_funding()             # 3. Funding rate
                await self.grid.check_fills()                # 4. Grid cycling

                # Periodic status display (non-critical, don't affect trading on error)
                if time.time() - last_status_time >= status_interval:
                    try:
                        await self._print_status(current_price)
                    except Exception as e:
                        log.warning(f"Status display error (non-critical): {e}")
                    last_status_time = time.time()

                await asyncio.sleep(POLL_INTERVAL_SECONDS)

            except asyncio.CancelledError:
                log.info("Main loop cancelled")
                break
            except Exception as e:
                if not self._running:
                    break  # Exit cleanly on shutdown

                self._consecutive_failures += 1
                delay = min(2 ** self._consecutive_failures, 120)  # Exponential backoff, max 2 min

                log.error(f"Error in main loop (failure #{self._consecutive_failures}): {e}")
                log.warning(f"Backing off for {delay}s...")
                await asyncio.sleep(delay)

        log.info("Main loop ended")

    async def _reconcile_after_outage(self):
        """Reconcile bot state with exchange after connection outage.

        This ensures we detect any fills that happened during downtime
        and don't double-place orders.
        """
        try:
            log.info("Reconciling state after outage...")

            # Re-sync account state
            await self._sync_state()

            # Check for any fills we might have missed
            # The grid's check_fills will detect orders that are no longer active
            await self.grid.check_fills()

            log.info("State reconciliation complete")

        except Exception as e:
            log.error(f"Error during state reconciliation: {e}")

    async def _print_status(self, price: Decimal):
        """Print clean status output with all key metrics."""
        grid_stats = self.grid.get_stats()
        floor_stats = self.floor.get_stats()

        # Get account info for balances
        account = await self.client.get_account()

        # Get asset IDs from spot market
        spot_market = self.client.get_market(SPOT_SYMBOL, MarketType.SPOT)
        lit_asset_id = spot_market.base_asset_id if spot_market else 0
        usdc_asset_id = spot_market.quote_asset_id if spot_market else 0

        # Get actual LIT balance from exchange (AVAILABLE ONLY - not locked in orders)
        lit_balance = Decimal("0")
        if account:
            for asset in account.assets:
                if asset.asset_id == lit_asset_id:
                    lit_balance = asset.balance  # Available only
                    break

        # Get actual USDC balance from exchange (AVAILABLE ONLY - not locked in orders)
        usdc_spot = Decimal("0")
        if account:
            for asset in account.assets:
                if asset.asset_id == usdc_asset_id:
                    usdc_spot = asset.balance  # Available only
                    break

        # LIT value
        lit_value = lit_balance * price

        # Grid PnL
        grid_pnl = grid_stats.get("total_profit", Decimal("0"))

        # Total net worth
        total_net_worth = lit_value + usdc_spot

        # Order counts
        pending_orders = grid_stats.get("pending_orders", 0)
        filled_orders = grid_stats.get("filled_orders", 0)
        grid_cycles = grid_stats.get("cycles", 0)

        # vs Floor calculation
        floor_value = Decimal("25000")
        vs_floor = total_net_worth - floor_value
        vs_floor_pct = (vs_floor / floor_value) * 100

        # Entry price
        entry_str = self.state.get("bot_entry_price", str(price))
        entry_price = Decimal(entry_str)
        price_change = ((price / entry_price) - 1) * 100

        # Runtime
        runtime = ""
        if self._start_time:
            delta = datetime.now() - self._start_time
            hours, remainder = divmod(int(delta.total_seconds()), 3600)
            minutes, seconds = divmod(remainder, 60)
            runtime = f"{hours}h {minutes}m"

        # Print status
        print()
        print("=" * 60)
        print(f"  LIT GRID BOT | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Runtime: {runtime}")
        print("=" * 60)
        print(f"  PRICE: ${price:.4f} ({price_change:+.2f}%)  |  Entry: ${entry_price:.4f}")
        print("-" * 60)
        print(f"  PORTFOLIO")
        print(f"    LIT:            {lit_balance:>10,.2f}  (${lit_value:>10,.2f})")
        print(f"    USDC:           ${usdc_spot:>10,.2f}")
        print(f"    ───────────────────────────────────────")
        print(f"    TOTAL NET:      ${total_net_worth:>12,.2f}")
        print(f"    vs Floor:       ${vs_floor:>12,.2f}  ({vs_floor_pct:+.1f}%)")
        print("-" * 60)
        print(f"  GRID")
        print(f"    Levels:         {grid_stats.get('num_levels', 0):>6}")
        print(f"    Orders:         {pending_orders:>6} pending  |  {filled_orders:>6} filled")
        print(f"    Cycles:         {grid_cycles:>6}")
        print(f"    Profit:         ${grid_pnl:>12,.2f}")
        print("-" * 60)
        print(f"  Floor Tier: {floor_stats.get('tier_triggered', 0)} | Grid Paused: {grid_stats.get('paused', False)}")
        print("=" * 60)
        print()

    async def stop(self):
        """Stop the bot gracefully."""
        if self._stopped:
            return
        self._stopped = True

        log.info("Stopping bot...")
        self._running = False

        # Wait for main loop to exit (it checks _running each cycle)
        await asyncio.sleep(POLL_INTERVAL_SECONDS + 1)

        try:
            if self.client:
                await self.client.close()
        except Exception:
            pass  # Ignore errors during shutdown
        finally:
            if self.state:
                self.state.close()
        log.info("Bot stopped")


async def main():
    bot = LitGridBot()

    # Handle shutdown signals with race condition protection
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
    except KeyboardInterrupt:
        pass
    finally:
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
