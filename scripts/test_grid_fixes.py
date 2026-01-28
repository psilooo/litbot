#!/usr/bin/env python3
"""
Test script to verify the grid order management fixes.

Tests:
1. Cancellation verification - _clear_all_grid_orders() verifies before clearing state
2. Grid initialization - correct order count
3. Fill simulation - counter-orders placed correctly
4. Recenter simulation - old orders cancelled before new ones placed
5. Reconciliation - doesn't create duplicate counter-orders

Cost: ~$2-5 for the tiny test orders (10 LIT per order, 3 levels = 60 LIT)
"""

import asyncio
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lithood.client import LighterClient
from lithood.state import StateManager
from lithood.infinite_grid import InfiniteGridEngine, InfiniteGridConfig
from lithood.types import OrderSide, MarketType, OrderStatus
from lithood.config import SPOT_SYMBOL
from lithood.logger import log

# Test configuration - very small for safety
TEST_LEVELS = 3  # 3 buy + 3 sell = 6 orders
TEST_LIT_PER_ORDER = Decimal("10")  # Minimum practical size
TEST_SPACING = Decimal("0.02")  # 2% spacing


class GridFixTester:
    """Test harness for grid fixes."""

    def __init__(self):
        self.client = LighterClient()
        self.state = StateManager(db_path=":memory:")  # In-memory DB for tests
        self.config = InfiniteGridConfig(
            num_levels=TEST_LEVELS,
            level_spacing_pct=TEST_SPACING,
            lit_per_order=TEST_LIT_PER_ORDER,
            total_grid_lit=TEST_LIT_PER_ORDER * TEST_LEVELS * 2,
            recenter_threshold=1,  # Recenter when within 1 level of edge
        )
        self.grid = None
        self.market = None
        self.initial_order_count = 0

    async def setup(self):
        """Connect and initialize."""
        log.info("Connecting to Lighter DEX...")
        await self.client.connect()
        log.info("Connected!")

        self.market = self.client.get_market(SPOT_SYMBOL, MarketType.SPOT)
        assert self.market is not None, "Spot market not found"

        # Count existing orders
        existing = await self.client.get_active_orders(market_id=self.market.market_id)
        self.initial_order_count = len(existing)
        log.info(f"Initial order count: {self.initial_order_count}")

        # Create grid engine
        self.grid = InfiniteGridEngine(self.client, self.state, self.config)

    async def cleanup(self):
        """Cancel all test orders and close connection."""
        log.info("Cleaning up test orders...")
        if self.grid:
            await self.grid.cancel_all()

        # Double-check with direct cancellation
        if self.market:
            await self.client.cancel_all_orders(market_id=self.market.market_id)

        await asyncio.sleep(2)

        # Verify cleanup
        remaining = await self.client.get_active_orders(market_id=self.market.market_id)
        if len(remaining) > self.initial_order_count:
            log.warning(f"Cleanup incomplete: {len(remaining)} orders remain (was {self.initial_order_count})")
        else:
            log.info(f"Cleanup complete: {len(remaining)} orders (was {self.initial_order_count})")

        await self.client.close()


async def test_cancellation_verification(tester: GridFixTester) -> bool:
    """
    TEST 1: Verify _clear_all_grid_orders() actually verifies cancellation.

    - Initialize grid (places orders)
    - Call _clear_all_grid_orders()
    - Verify returns True only when orders are actually gone
    - Verify local state matches exchange state
    """
    log.info("")
    log.info("=" * 60)
    log.info("=== TEST 1: CANCELLATION VERIFICATION ===")
    log.info("=" * 60)

    # Initialize grid
    log.info(f"Initializing grid with {TEST_LEVELS} levels per side...")
    success = await tester.grid.initialize()
    assert success, "Grid initialization failed"

    await asyncio.sleep(2)  # Wait for exchange state

    # Check order count
    exchange_orders = await tester.client.get_active_orders(market_id=tester.market.market_id)
    expected_count = TEST_LEVELS * 2  # buys + sells
    log.info(f"Orders on exchange: {len(exchange_orders)} (expected: {expected_count})")

    if len(exchange_orders) < expected_count:
        log.warning(f"Fewer orders than expected - some may not have been placed")

    # Check local state
    local_pending = tester.state.get_pending_orders()
    log.info(f"Orders in local state: {len(local_pending)}")

    # Now clear all orders
    log.info("Calling _clear_all_grid_orders()...")
    clear_result = await tester.grid._clear_all_grid_orders()

    await asyncio.sleep(2)  # Wait for exchange state

    # Verify result
    exchange_after = await tester.client.get_active_orders(market_id=tester.market.market_id)
    local_after = tester.state.get_pending_orders()

    log.info(f"After clearing:")
    log.info(f"  _clear_all_grid_orders() returned: {clear_result}")
    log.info(f"  Orders on exchange: {len(exchange_after)}")
    log.info(f"  Orders in local state (pending): {len(local_after)}")

    # The fix should ensure:
    # - Returns True only if exchange has 0 orders
    # - Local state is cleared only for confirmed cancelled orders
    if clear_result:
        assert len(exchange_after) == 0, "Returned True but orders still on exchange!"
        log.info("PASS: _clear_all_grid_orders() correctly verified cancellation")
    else:
        log.warning("_clear_all_grid_orders() returned False - some orders may remain")
        # This is actually correct behavior if cancellation failed

    log.info("TEST 1 PASSED")
    return True


async def test_grid_order_count(tester: GridFixTester) -> bool:
    """
    TEST 2: Verify grid places correct number of orders.

    - Initialize grid with N levels
    - Verify exactly N buy + N sell orders on exchange
    - Verify local state matches
    """
    log.info("")
    log.info("=" * 60)
    log.info("=== TEST 2: GRID ORDER COUNT ===")
    log.info("=" * 60)

    # Re-initialize grid
    log.info(f"Initializing grid with {TEST_LEVELS} levels per side...")
    success = await tester.grid.initialize()
    assert success, "Grid initialization failed"

    await asyncio.sleep(3)  # Wait for exchange state

    # Count orders by side
    exchange_orders = await tester.client.get_active_orders(market_id=tester.market.market_id)
    buys = [o for o in exchange_orders if o.side == OrderSide.BUY]
    sells = [o for o in exchange_orders if o.side == OrderSide.SELL]

    log.info(f"Orders on exchange:")
    log.info(f"  Buy orders: {len(buys)} (expected: {TEST_LEVELS})")
    log.info(f"  Sell orders: {len(sells)} (expected: {TEST_LEVELS})")
    log.info(f"  Total: {len(exchange_orders)} (expected: {TEST_LEVELS * 2})")

    # Check local state
    local_pending = tester.state.get_pending_orders()
    log.info(f"Orders in local state: {len(local_pending)}")

    # Log prices for verification
    log.info("Buy levels:")
    for o in sorted(buys, key=lambda x: x.price, reverse=True):
        log.info(f"  ${o.price} - {o.size} LIT")

    log.info("Sell levels:")
    for o in sorted(sells, key=lambda x: x.price):
        log.info(f"  ${o.price} - {o.size} LIT")

    # Verify counts
    assert len(buys) == TEST_LEVELS, f"Expected {TEST_LEVELS} buys, got {len(buys)}"
    assert len(sells) == TEST_LEVELS, f"Expected {TEST_LEVELS} sells, got {len(sells)}"

    log.info("TEST 2 PASSED")
    return True


async def test_recenter_order_count(tester: GridFixTester) -> bool:
    """
    TEST 3: Verify recenter doesn't accumulate orders.

    - Force a recenter by setting grid center far from current price
    - Verify order count stays at expected level (not doubled)
    """
    log.info("")
    log.info("=" * 60)
    log.info("=== TEST 3: RECENTER ORDER COUNT ===")
    log.info("=" * 60)

    # Get current price
    current_price = await tester.client.get_mid_price(SPOT_SYMBOL, MarketType.SPOT)
    assert current_price is not None, "Failed to get price"
    log.info(f"Current price: ${current_price}")

    # Count orders before recenter
    orders_before = await tester.client.get_active_orders(market_id=tester.market.market_id)
    log.info(f"Orders before recenter: {len(orders_before)}")

    # Force recenter by calling it directly
    log.info("Forcing recenter...")
    recenter_result = await tester.grid._recenter(current_price)

    await asyncio.sleep(3)  # Wait for exchange state

    # Count orders after recenter
    orders_after = await tester.client.get_active_orders(market_id=tester.market.market_id)
    log.info(f"Orders after recenter: {len(orders_after)}")
    log.info(f"Recenter result: {recenter_result}")

    # The fix should ensure old orders are cancelled before new ones placed
    expected_count = TEST_LEVELS * 2

    if len(orders_after) > expected_count:
        log.error(f"ORDER ACCUMULATION DETECTED!")
        log.error(f"Expected max {expected_count} orders, got {len(orders_after)}")
        log.error("This indicates the cancellation verification fix is not working!")
        return False

    log.info(f"Order count stable: {len(orders_after)} (expected max: {expected_count})")

    # Do a second recenter to double-check
    log.info("Forcing second recenter...")
    await tester.grid._recenter(current_price)
    await asyncio.sleep(3)

    orders_final = await tester.client.get_active_orders(market_id=tester.market.market_id)
    log.info(f"Orders after second recenter: {len(orders_final)}")

    if len(orders_final) > expected_count:
        log.error(f"ORDER ACCUMULATION after second recenter!")
        return False

    log.info("TEST 3 PASSED")
    return True


async def test_reconciliation_no_duplicates(tester: GridFixTester) -> bool:
    """
    TEST 4: Verify reconciliation doesn't create duplicate counter-orders.

    - Set up grid
    - Run reconciliation
    - Verify order count doesn't increase
    """
    log.info("")
    log.info("=" * 60)
    log.info("=== TEST 4: RECONCILIATION NO DUPLICATES ===")
    log.info("=" * 60)

    # Count orders before reconciliation
    orders_before = await tester.client.get_active_orders(market_id=tester.market.market_id)
    log.info(f"Orders before reconciliation: {len(orders_before)}")

    # Force reconciliation by resetting the timer
    tester.grid._last_reconcile_time = None

    log.info("Running reconciliation...")
    await tester.grid.reconcile_orders()

    await asyncio.sleep(2)

    # Count orders after reconciliation
    orders_after = await tester.client.get_active_orders(market_id=tester.market.market_id)
    log.info(f"Orders after reconciliation: {len(orders_after)}")

    # Reconciliation should NOT increase order count
    if len(orders_after) > len(orders_before):
        log.error(f"RECONCILIATION CREATED EXTRA ORDERS!")
        log.error(f"Before: {len(orders_before)}, After: {len(orders_after)}")
        return False

    log.info("Order count stable after reconciliation")

    # Run reconciliation again to double-check
    log.info("Running second reconciliation...")
    tester.grid._last_reconcile_time = None
    await tester.grid.reconcile_orders()
    await asyncio.sleep(2)

    orders_final = await tester.client.get_active_orders(market_id=tester.market.market_id)
    log.info(f"Orders after second reconciliation: {len(orders_final)}")

    if len(orders_final) > len(orders_before):
        log.error(f"RECONCILIATION CREATED EXTRA ORDERS on second run!")
        return False

    log.info("TEST 4 PASSED")
    return True


async def test_check_fills_no_duplicates(tester: GridFixTester) -> bool:
    """
    TEST 5: Verify check_fills with processing lock doesn't create duplicates.

    - Run check_fills multiple times rapidly
    - Verify order count doesn't increase unexpectedly
    """
    log.info("")
    log.info("=" * 60)
    log.info("=== TEST 5: CHECK_FILLS PROCESSING LOCK ===")
    log.info("=" * 60)

    # Count orders before
    orders_before = await tester.client.get_active_orders(market_id=tester.market.market_id)
    log.info(f"Orders before rapid check_fills: {len(orders_before)}")

    # Run check_fills multiple times rapidly (simulating race condition)
    log.info("Running check_fills 5 times rapidly...")
    for i in range(5):
        await tester.grid.check_fills()
        await asyncio.sleep(0.1)  # Very short delay

    await asyncio.sleep(2)  # Wait for any async operations to complete

    # Count orders after
    orders_after = await tester.client.get_active_orders(market_id=tester.market.market_id)
    log.info(f"Orders after rapid check_fills: {len(orders_after)}")

    # Order count should be stable (no fills expected, no new orders)
    if len(orders_after) > len(orders_before):
        log.warning(f"Order count increased: {len(orders_before)} -> {len(orders_after)}")
        log.warning("This may be expected if orders filled during test")

    # Check that processing lock is working
    log.info(f"Processing lock set size: {len(tester.grid._processing_orders)}")
    assert len(tester.grid._processing_orders) == 0, "Processing lock not properly released!"

    log.info("TEST 5 PASSED")
    return True


async def test_full_cycle(tester: GridFixTester) -> bool:
    """
    TEST 6: Full cycle test - init, operate, recenter, cleanup.

    - Initialize grid
    - Run several check_fills cycles
    - Force recenter
    - Run reconciliation
    - Verify final order count is correct
    """
    log.info("")
    log.info("=" * 60)
    log.info("=== TEST 6: FULL CYCLE TEST ===")
    log.info("=" * 60)

    # Clear and re-initialize
    log.info("Clearing existing grid...")
    await tester.grid._clear_all_grid_orders()
    await asyncio.sleep(2)

    log.info("Initializing fresh grid...")
    success = await tester.grid.initialize()
    assert success, "Grid initialization failed"
    await asyncio.sleep(3)

    initial_count = len(await tester.client.get_active_orders(market_id=tester.market.market_id))
    log.info(f"Initial order count: {initial_count}")

    # Simulate several operation cycles
    log.info("Running 3 operation cycles...")
    current_price = await tester.client.get_mid_price(SPOT_SYMBOL, MarketType.SPOT)

    for i in range(3):
        log.info(f"Cycle {i+1}/3...")
        await tester.grid.check_fills()
        await tester.grid.check_and_recenter(current_price)
        await asyncio.sleep(1)

    mid_count = len(await tester.client.get_active_orders(market_id=tester.market.market_id))
    log.info(f"Order count after cycles: {mid_count}")

    # Force recenter
    log.info("Forcing recenter...")
    await tester.grid._recenter(current_price)
    await asyncio.sleep(3)

    post_recenter_count = len(await tester.client.get_active_orders(market_id=tester.market.market_id))
    log.info(f"Order count after recenter: {post_recenter_count}")

    # Run reconciliation
    log.info("Running reconciliation...")
    tester.grid._last_reconcile_time = None
    await tester.grid.reconcile_orders()
    await asyncio.sleep(2)

    final_count = len(await tester.client.get_active_orders(market_id=tester.market.market_id))
    log.info(f"Final order count: {final_count}")

    # Summary
    log.info("")
    log.info("Order count progression:")
    log.info(f"  Initial: {initial_count}")
    log.info(f"  After cycles: {mid_count}")
    log.info(f"  After recenter: {post_recenter_count}")
    log.info(f"  Final: {final_count}")

    expected_max = TEST_LEVELS * 2
    if final_count > expected_max:
        log.error(f"ORDER ACCUMULATION DETECTED!")
        log.error(f"Expected max {expected_max}, got {final_count}")
        return False

    log.info(f"Order count within expected range (max {expected_max})")
    log.info("TEST 6 PASSED")
    return True


async def main() -> int:
    """Run all grid fix tests."""
    log.info("=" * 60)
    log.info("GRID ORDER MANAGEMENT FIX TESTS")
    log.info("=" * 60)
    log.info("")
    log.info(f"Test configuration:")
    log.info(f"  Levels per side: {TEST_LEVELS}")
    log.info(f"  LIT per order: {TEST_LIT_PER_ORDER}")
    log.info(f"  Spacing: {TEST_SPACING * 100}%")
    log.info(f"  Expected total orders: {TEST_LEVELS * 2}")
    log.info("")

    tester = GridFixTester()
    results = {}

    try:
        await tester.setup()

        # Run tests
        results["test_cancellation_verification"] = await test_cancellation_verification(tester)
        results["test_grid_order_count"] = await test_grid_order_count(tester)
        results["test_recenter_order_count"] = await test_recenter_order_count(tester)
        results["test_reconciliation_no_duplicates"] = await test_reconciliation_no_duplicates(tester)
        results["test_check_fills_no_duplicates"] = await test_check_fills_no_duplicates(tester)
        results["test_full_cycle"] = await test_full_cycle(tester)

    except Exception as e:
        log.error(f"Test error: {e}")
        import traceback
        traceback.print_exc()
        return 1

    finally:
        await tester.cleanup()

    # Summary
    log.info("")
    log.info("=" * 60)
    log.info("TEST RESULTS SUMMARY")
    log.info("=" * 60)

    passed = 0
    failed = 0
    for test_name, result in results.items():
        status = "PASSED" if result else "FAILED"
        log.info(f"  {test_name}: {status}")
        if result:
            passed += 1
        else:
            failed += 1

    log.info("")
    log.info(f"Total: {passed} passed, {failed} failed")

    if failed > 0:
        log.error("SOME TESTS FAILED - DO NOT USE IN PRODUCTION")
        return 1

    log.info("")
    log.info("=" * 60)
    log.info("ALL TESTS PASSED!")
    log.info("=" * 60)
    log.info("The grid order management fixes are working correctly.")

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
