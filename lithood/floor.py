# lithood/floor.py
"""Floor protection system to guarantee $25k minimum."""

from decimal import Decimal

from lithood.client import LighterClient
from lithood.state import StateManager
from lithood.types import OrderSide, MarketType
from lithood.config import FLOOR_CONFIG, ALLOCATION
from lithood.logger import log


class FloorProtection:
    """Tiered de-risk system to protect the $25k floor."""

    def __init__(self, client: LighterClient, state: StateManager,
                 grid=None, hedge=None):
        self.client = client
        self.state = state
        self.grid = grid
        self.hedge = hedge
        self.config = FLOOR_CONFIG
        self.symbol = "LIT"

    async def check(self, current_price: Decimal):
        """Check price against floor protection tiers."""
        portfolio_value = await self._calculate_portfolio_value(current_price)
        tier_triggered = self.state.get("floor_tier_triggered", 0)

        log.debug(f"Floor check: price=${current_price}, portfolio=${portfolio_value:.2f}, tier={tier_triggered}")

        for i, tier in enumerate(self.config["tiers"]):
            tier_num = i + 1
            if current_price <= tier["price"] and tier_triggered < tier_num:
                await self._execute_tier(tier_num, tier, current_price)
                self.state.set("floor_tier_triggered", tier_num)
                break  # Execute only one tier per check cycle

        # Emergency check
        if portfolio_value <= self.config["emergency_buffer"]:
            await self._emergency_exit(current_price, portfolio_value)

    async def _calculate_portfolio_value(self, price: Decimal) -> Decimal:
        """Calculate total portfolio value."""
        account = await self.client.get_account()

        # LIT value (track in state as string for Decimal precision)
        # Get default from allocation config
        default_lit = ALLOCATION["core_lit"] + ALLOCATION["grid_sell_lit"] + ALLOCATION["reserve_lit"]
        lit_balance_str = self.state.get("lit_balance", str(default_lit))
        lit_balance = Decimal(str(lit_balance_str))
        lit_value = lit_balance * price

        # USDC balance
        usdc_balance = Decimal("0")
        if account is not None:
            usdc_balance = account.available_balance
        else:
            log.warning("Could not get account - using 0 for USDC balance")

        # Hedge PnL
        hedge_pnl = Decimal("0")
        if self.state.get("hedge_active"):
            entry_str = self.state.get("hedge_entry_price")
            entry = Decimal(str(entry_str)) if entry_str else price
            size_str = self.state.get("hedge_size")
            size = Decimal(str(size_str)) if size_str else Decimal("0")
            hedge_pnl = (entry - price) * size

        total = lit_value + usdc_balance + hedge_pnl
        return total

    async def _execute_tier(self, tier_num: int, tier: dict, price: Decimal):
        """Execute a floor protection tier."""
        action = tier["action"]

        log.warning(f"FLOOR TIER {tier_num} TRIGGERED @ ${price}")
        log.warning(f"  Action: {action}")

        if action == "pause_grid_buys":
            if self.grid:
                self.grid.pause_buys()
            self._alert(f"Price warning: ${price} - grid buys paused")

        elif action == "sell_reserve":
            amount = tier["amount"]
            await self._market_sell_lit(amount, "reserve")

        elif action == "cancel_grid_sell":
            if self.grid:
                await self.grid.cancel_all()
            amount = tier["amount"]
            await self._market_sell_lit(amount, "grid")

        elif action == "emergency_exit":
            await self._emergency_exit(price, Decimal("0"))

    async def _market_sell_lit(self, amount: Decimal, bucket: str) -> bool:
        """Market sell LIT from a specific bucket. Returns True if successful."""
        log.warning(f"Selling {amount} LIT from {bucket} bucket")

        order = await self.client.place_market_order(
            symbol=self.symbol,
            market_type=MarketType.SPOT,
            side=OrderSide.SELL,
            size=amount,
        )

        if order is None:
            log.error(f"Failed to sell {amount} LIT from {bucket} bucket")
            return False

        # Update LIT balance tracking (store as string for Decimal precision)
        # Get default from allocation config
        default_lit = ALLOCATION["core_lit"] + ALLOCATION["grid_sell_lit"] + ALLOCATION["reserve_lit"]
        lit_balance_str = self.state.get("lit_balance", str(default_lit))
        lit_balance = Decimal(str(lit_balance_str))
        lit_balance -= amount
        self.state.set("lit_balance", str(lit_balance))

        log.warning(f"Sold {amount} LIT. New balance: {lit_balance}")
        return True

    async def _emergency_exit(self, price: Decimal, portfolio_value: Decimal):
        """Emergency exit - sell everything to guarantee $25k."""
        log.error("=" * 50)
        log.error("EMERGENCY EXIT TRIGGERED")
        log.error(f"Price: ${price}, Portfolio: ${portfolio_value}")
        log.error("=" * 50)

        # Close hedge first
        if self.hedge and self.state.get("hedge_active"):
            await self.hedge.close_short(reason="emergency")

        # Cancel all orders
        if self.grid:
            await self.grid.cancel_all()
        await self.client.cancel_all_orders()

        # Sell all LIT
        # Get default from allocation config
        default_lit = ALLOCATION["core_lit"] + ALLOCATION["grid_sell_lit"] + ALLOCATION["reserve_lit"]
        lit_balance_str = self.state.get("lit_balance", str(default_lit))
        lit_balance = Decimal(str(lit_balance_str))
        if lit_balance > 0:
            success = await self._market_sell_lit(lit_balance, "emergency")
            if not success:
                log.error("CRITICAL: Emergency liquidation FAILED - manual intervention required!")

        # Halt bot
        self.state.set("bot_halted", True)
        self._alert("EMERGENCY: Floor protection triggered - bot halted")

        log.error("Bot halted. Manual intervention required.")

    def _alert(self, message: str):
        """Send alert (placeholder for notifications)."""
        log.warning(f"ALERT: {message}")
        # TODO: Add Telegram/email notifications

    def reset_tiers(self):
        """Reset tier triggers (for testing or after recovery)."""
        self.state.set("floor_tier_triggered", 0)
        log.info("Floor protection tiers reset")

    def get_stats(self) -> dict:
        """Get floor protection statistics."""
        return {
            "tier_triggered": self.state.get("floor_tier_triggered", 0),
            "floor_value": float(self.config["floor_value"]),
            "emergency_buffer": float(self.config["emergency_buffer"]),
        }
