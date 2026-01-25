# Infinite Grid Strategy Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create a new grid bot script that replaces core sells with an "infinite" grid that re-centers when price reaches the edge, while protecting against selling all LIT too early in a strong rally.

**Architecture:** Adaptive grid that shifts its center when price reaches the top or bottom level. Implements a "trailing floor" mechanism where the lowest sell price rises with each completed buy→sell cycle, preventing early sellout during rallies.

**Tech Stack:** Python, existing lithood modules (client, state, types)

---

## The Problem

Current setup:
- 10 buy + 10 sell grid levels (400 LIT each = 4,000 LIT cycling)
- 11,175 LIT in core sells at $2.00-$4.50 (sitting idle)
- **Issue:** Core sells earn nothing until those prices hit. Grid capital could be cycling more.

## Profitability Analysis

**Current grid math (2% spacing, 400 LIT):**
- Each complete cycle (buy fills → sell fills) earns: `400 LIT × 2% = 8 LIT ≈ $12 at $1.50`
- With 10 levels, if price oscillates through all levels once per day: ~$120/day potential

**Extended grid (more levels, same capital):**
- More levels = more opportunity to catch volatility
- But: more sells above = risk of selling everything in a rally

## The "Trailing Floor" Solution

**Problem:** If grid extends infinitely upward, a rally from $1.50 to $3.00 would sell all your LIT.

**Solution:** Implement a "ratcheting sell floor" that rises with successful cycles:

1. Start with sell floor at current price (e.g., $1.50)
2. Each time a buy→sell cycle completes profitably, raise the floor by the spread (2%)
3. Grid buys can go as low as needed, but sells never go below the current floor
4. **Effect:** In a rally, you sell gradually at ever-higher prices. In a dump, you buy cheap but don't sell below your ratcheted floor.

**Example:**
- Start: floor = $1.50, grid sells at $1.53, $1.56, $1.59...
- After 5 profitable cycles: floor = $1.65, sells now at $1.68, $1.71...
- Price crashes to $1.20: buys fill, but sells won't go below $1.65
- Price recovers: you sell at $1.68+ (captured the dip)

## Grid Recentering Logic

When price reaches the top or bottom of the grid:

**Price hits top sell:**
1. Cancel all buy orders
2. Re-center grid around new price
3. Place new buys below, new sells above
4. Update floor if it's risen

**Price hits bottom buy:**
1. Cancel all sell orders
2. Re-center grid around new price
3. Place new buys below, new sells above
4. Floor stays where it is (don't lower it)

---

## Tasks

### Task 1: Create InfiniteGridEngine class

**Files:**
- Create: `lithood/infinite_grid.py`
- Reference: `lithood/grid.py` (existing patterns)

**Step 1: Create the new file with basic structure**

```python
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
        """Initialize grid centered on current price."""
        pass  # Implemented in Task 2

    async def check_fills(self):
        """Check for fills and cycle orders."""
        pass  # Implemented in Task 3

    async def _maybe_recenter(self, current_price: Decimal):
        """Check if grid needs recentering."""
        pass  # Implemented in Task 4
```

**Step 2: Verify file created**

Run: `python -c "from lithood.infinite_grid import InfiniteGridEngine; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add lithood/infinite_grid.py
git commit -m "feat: add InfiniteGridEngine skeleton"
```

---

### Task 2: Implement grid initialization with trailing floor

**Files:**
- Modify: `lithood/infinite_grid.py`

**Step 1: Implement initialize method**

```python
    async def initialize(self):
        """Initialize grid centered on current price with trailing floor."""
        entry_price = await self.client.get_mid_price(self.symbol, self.market_type)
        if entry_price is None:
            log.error("Failed to get mid price - cannot initialize infinite grid")
            return

        # Clear stale orders
        await self._clear_all_grid_orders()

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
```

**Step 2: Add helper methods for placing orders**

```python
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
```

**Step 3: Test initialization**

Run: `python -c "from lithood.infinite_grid import InfiniteGridEngine, InfiniteGridConfig; print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add lithood/infinite_grid.py
git commit -m "feat: implement infinite grid initialization with trailing floor"
```

---

### Task 3: Implement fill detection and cycling with floor ratcheting

**Files:**
- Modify: `lithood/infinite_grid.py`

**Step 1: Implement check_fills with floor ratcheting**

```python
    async def check_fills(self):
        """Check for filled orders and cycle. Ratchet floor on profitable cycles."""
        if self.state.get("grid_paused"):
            return

        market = self.client.get_market(self.symbol, self.market_type)
        if market is None:
            return

        active_orders = await self.client.get_active_orders(market_id=market.market_id)
        active_map = {(o.price, o.side): o for o in active_orders}

        grace_cutoff = datetime.now() - timedelta(seconds=30)

        for order in self.state.get_pending_orders():
            if order.market_id != market.market_id:
                continue
            if order.created_at > grace_cutoff:
                continue

            key = (order.price, order.side)
            if key not in active_map:
                # Order filled
                await self._on_fill(order, order.size)

    async def _on_fill(self, order: Order, filled_size: Decimal):
        """Handle fill - cycle and possibly ratchet floor."""
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

            # RATCHET THE FLOOR UP
            # Each profitable sell cycle means we can raise our floor
            new_floor = (self._sell_floor * (1 + spacing)).quantize(Decimal("0.0001"))
            if new_floor > self._sell_floor:
                log.info(f"FLOOR RATCHET: ${self._sell_floor} -> ${new_floor}")
                self._sell_floor = new_floor
                self.state.set("infinite_grid_sell_floor", str(self._sell_floor))

            # Update profit tracking
            total_profit = Decimal(self.state.get("infinite_grid_profit", "0"))
            total_profit += profit
            self.state.set("infinite_grid_profit", str(total_profit))

            fills = int(self.state.get("infinite_grid_sell_fills", "0"))
            self.state.set("infinite_grid_sell_fills", str(fills + 1))

            cycles = int(self.state.get("infinite_grid_cycles", "0"))
            self.state.set("infinite_grid_cycles", str(cycles + 1))
```

**Step 2: Commit**

```bash
git add lithood/infinite_grid.py
git commit -m "feat: implement fill detection with floor ratcheting"
```

---

### Task 4: Implement grid recentering

**Files:**
- Modify: `lithood/infinite_grid.py`

**Step 1: Implement recentering logic**

```python
    async def check_and_recenter(self, current_price: Decimal):
        """Check if price has reached grid edge and recenter if needed."""
        if not self._buy_levels or not self._sell_levels:
            return

        # Check if near top or bottom
        threshold = self.config.recenter_threshold

        # Near top? (price approaching highest sell)
        if self._sell_levels and current_price >= self._sell_levels[-threshold]:
            log.info(f"Price ${current_price} near top of grid - recentering")
            await self._recenter(current_price)
            return

        # Near bottom? (price approaching lowest buy)
        if self._buy_levels and current_price <= self._buy_levels[-threshold]:
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

        log.info(f"Grid recentered. New center: ${new_center}, Floor: ${self._sell_floor}")
```

**Step 2: Commit**

```bash
git add lithood/infinite_grid.py
git commit -m "feat: implement grid recentering when price hits edge"
```

---

### Task 5: Create the new bot script

**Files:**
- Create: `scripts/run_infinite_grid.py`

**Step 1: Create the new script**

```python
#!/usr/bin/env python3
# scripts/run_infinite_grid.py
"""Infinite Grid Bot - maximizes volatility capture without core sells.

This bot uses an adaptive grid that:
1. Re-centers when price reaches the edge
2. Uses a trailing floor to prevent selling all LIT early
3. Ratchets the floor up with each profitable cycle
"""

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
from lithood.floor import FloorProtection
from lithood.types import MarketType
from lithood.config import POLL_INTERVAL_SECONDS, SPOT_SYMBOL
from lithood.logger import log
from lithood.retry import RETRY_PERSISTENT, calculate_delay


class InfiniteGridBot:
    """Infinite grid bot - no core sells, all capital cycling."""

    def __init__(self):
        self.client = LighterClient()
        db_path = os.getenv("BOT_STATE_DB", os.path.join(os.path.dirname(__file__), "..", "infinite_grid_state.db"))
        self.state = StateManager(db_path=db_path)
        self.grid: InfiniteGridEngine = None
        self.floor: FloorProtection = None
        self._running = False
        self._stopped = False
        self._start_time = None
        self._consecutive_failures = 0

    async def start(self):
        """Initialize and start the bot."""
        self._start_time = datetime.now()

        log.info("=" * 60)
        log.info("  INFINITE GRID BOT")
        log.info("  Maximum volatility capture")
        log.info("=" * 60)

        await self.client.connect()

        # Configure grid - all available LIT for cycling
        config = InfiniteGridConfig(
            num_levels=15,
            level_spacing_pct=Decimal("0.02"),
            lit_per_order=Decimal("400"),
            total_grid_lit=Decimal("16000"),
            recenter_threshold=2,
        )

        self.grid = InfiniteGridEngine(self.client, self.state, config)
        # Floor protection without hedge
        self.floor = FloorProtection(self.client, self.state, self.grid, hedge=None)

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
                if self.state.get("bot_halted"):
                    log.info("Bot halted by floor protection")
                    break

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
                await self.floor.check(current_price)
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
        floor = Decimal(self.state.get("infinite_grid_sell_floor", "0"))
        cycles = int(self.state.get("infinite_grid_cycles", "0"))
        profit = Decimal(self.state.get("infinite_grid_profit", "0"))
        recenters = int(self.state.get("infinite_grid_recenters", "0"))
        buy_fills = int(self.state.get("infinite_grid_buy_fills", "0"))
        sell_fills = int(self.state.get("infinite_grid_sell_fills", "0"))

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
        print(f"  PRICE: ${price:.4f}")
        print(f"  FLOOR: ${floor:.4f} (sells never below this)")
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


async def main():
    bot = InfiniteGridBot()
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
```

**Step 2: Make executable**

Run: `chmod +x scripts/run_infinite_grid.py`

**Step 3: Test import**

Run: `python scripts/run_infinite_grid.py --help 2>&1 || python -c "import scripts.run_infinite_grid; print('OK')"`

**Step 4: Commit**

```bash
git add scripts/run_infinite_grid.py
git commit -m "feat: add infinite grid bot script"
```

---

### Task 6: Add stats/pause/resume methods

**Files:**
- Modify: `lithood/infinite_grid.py`

**Step 1: Add utility methods**

```python
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
            "sell_floor": self._sell_floor,
            "buy_levels": len(self._buy_levels),
            "sell_levels": len(self._sell_levels),
            "cycles": int(self.state.get("infinite_grid_cycles", "0")),
            "profit": Decimal(self.state.get("infinite_grid_profit", "0")),
            "recenters": int(self.state.get("infinite_grid_recenters", "0")),
            "paused": self.state.get("grid_paused", False),
        }
```

**Step 2: Commit**

```bash
git add lithood/infinite_grid.py
git commit -m "feat: add stats, pause, resume to infinite grid"
```

---

## Summary

**Two bots, one choice:**

| Feature | run_bot.py | run_infinite_grid.py |
|---------|------------|---------------------|
| Core sells | Yes ($2-$4.50) | No |
| Grid levels | 10 + 10 | 15 + 15 |
| Recentering | No | Yes |
| Trailing floor | No | Yes |
| Capital efficiency | Lower | Higher |
| Upside exposure | Fixed tiers | Adaptive |

**Run original:** `python scripts/run_bot.py`
**Run infinite:** `python scripts/run_infinite_grid.py`

Each uses a separate state database, so you can switch between them.
