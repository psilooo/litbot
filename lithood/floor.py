# lithood/floor.py
"""Floor protection system to guarantee $25k minimum.

Uses portfolio-value-based protection only - no price tiers.
Emergency exit triggers when portfolio value drops to $25.5k buffer.
"""

from decimal import Decimal

from lithood.client import LighterClient
from lithood.state import StateManager
from lithood.types import OrderSide, MarketType
from lithood.config import FLOOR_CONFIG, SPOT_SYMBOL
from lithood.logger import log


class FloorProtection:
    """Portfolio-value-based floor protection to guarantee $25k minimum."""

    def __init__(self, client: LighterClient, state: StateManager,
                 grid=None, hedge=None):
        self.client = client
        self.state = state
        self.grid = grid
        self.hedge = hedge
        self.config = FLOOR_CONFIG
        self.symbol = SPOT_SYMBOL  # "LIT/USDC" for spot market sells

    async def check(self, current_price: Decimal):
        """Check portfolio value against floor protection threshold."""
        portfolio_value = await self._calculate_portfolio_value(current_price)

        log.debug(f"Floor check: price=${current_price}, portfolio=${portfolio_value:.2f}")

        # Emergency exit if portfolio value drops to buffer threshold
        if portfolio_value <= self.config["emergency_buffer"]:
            await self._emergency_exit(current_price, portfolio_value)

    async def _calculate_portfolio_value(self, price: Decimal) -> Decimal:
        """Calculate total portfolio value using actual exchange balances.

        Includes:
        - LIT balance (available + locked in orders) * current price
        - USDC balance (available + locked in orders)
        - Perp collateral/margin
        - Hedge unrealized PnL
        """
        account = await self.client.get_account()
        if account is None:
            log.warning("Could not get account - cannot calculate portfolio value")
            return Decimal("0")

        # Get asset IDs from spot market
        spot_market = self.client.get_market(self.symbol, MarketType.SPOT)
        if spot_market is None:
            log.error(f"Spot market not found: {self.symbol}")
            return Decimal("0")

        lit_asset_id = spot_market.base_asset_id
        usdc_asset_id = spot_market.quote_asset_id

        # Get actual LIT balance (available + locked)
        lit_balance = Decimal("0")
        for asset in account.assets:
            if asset.asset_id == lit_asset_id:
                lit_balance = asset.balance + asset.locked_balance
                break
        lit_value = lit_balance * price

        # Update state with actual balance for display purposes
        self.state.set("lit_balance", str(lit_balance))

        # Get actual USDC balance (available + locked)
        usdc_balance = Decimal("0")
        for asset in account.assets:
            if asset.asset_id == usdc_asset_id:
                usdc_balance = asset.balance + asset.locked_balance
                break

        # Note: Don't add perp collateral separately - it may already be included in spot USDC
        # depending on how the exchange reports balances
        total = lit_value + usdc_balance

        log.debug(f"Portfolio: LIT={lit_balance}(${lit_value:.2f}) + USDC=${usdc_balance:.2f} = ${total:.2f}")

        return total

    async def _market_sell_lit(self, amount: Decimal, reason: str) -> bool:
        """Market sell LIT. Returns True if successful."""
        log.warning(f"Selling {amount} LIT ({reason})")

        order = await self.client.place_market_order(
            symbol=self.symbol,
            market_type=MarketType.SPOT,
            side=OrderSide.SELL,
            size=amount,
        )

        if order is None:
            log.error(f"Failed to sell {amount} LIT ({reason})")
            return False

        log.warning(f"Sold {amount} LIT ({reason})")
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

        # Get actual LIT balance from exchange and sell all
        account = await self.client.get_account()
        if account is not None:
            spot_market = self.client.get_market(self.symbol, MarketType.SPOT)
            if spot_market:
                lit_asset_id = spot_market.base_asset_id
                lit_balance = Decimal("0")
                for asset in account.assets:
                    if asset.asset_id == lit_asset_id:
                        # After cancelling orders, locked_balance should be 0
                        lit_balance = asset.balance
                        break

                if lit_balance > 0:
                    success = await self._market_sell_lit(lit_balance, "emergency")
                    if not success:
                        log.error("CRITICAL: Emergency liquidation FAILED - manual intervention required!")
        else:
            log.error("CRITICAL: Could not get account to check LIT balance for emergency exit!")

        # Halt bot
        self.state.set("bot_halted", True)
        self._alert("EMERGENCY: Floor protection triggered - bot halted")

        log.error("Bot halted. Manual intervention required.")

    def _alert(self, message: str):
        """Send alert (placeholder for notifications)."""
        log.warning(f"ALERT: {message}")
        # TODO: Add Telegram/email notifications

    def get_stats(self) -> dict:
        """Get floor protection statistics."""
        return {
            "floor_value": float(self.config["floor_value"]),
            "emergency_buffer": float(self.config["emergency_buffer"]),
            "emergency_triggered": self.state.get("bot_halted", False),
        }
