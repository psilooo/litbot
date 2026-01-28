# lithood/client.py
"""Lighter DEX API client wrapper with retry logic and connection monitoring."""

import asyncio
import time
from decimal import Decimal
from datetime import datetime, timedelta
from typing import Optional

from eth_account import Account as EthAccount

# IMPORTANT: Import config FIRST to set proxy env vars before lighter/requests loads
from lithood.config import (
    LIGHTER_BASE_URL,
    LIGHTER_PRIVATE_KEY,
    LIGHTER_API_KEY_PRIVATE,
    LIGHTER_API_KEY_INDEX,
    LIGHTER_ACCOUNT_INDEX,
    PROXY_URL,
)

from lighter import (
    Configuration,
    ApiClient,
    SignerClient,
    AccountApi,
    OrderApi,
    FundingApi,
)
from lithood.logger import log
from lithood.types import (
    Market,
    MarketType,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
    Position,
    Account,
    AssetBalance,
    FundingRate,
)
from lithood.retry import (
    retry_async,
    ConnectionMonitor,
    RETRY_FAST,
    RETRY_STANDARD,
    RETRY_PERSISTENT,
    is_transient_error,
)


class LighterClient:
    """Client for interacting with the Lighter DEX API."""

    def __init__(
        self,
        base_url: str = LIGHTER_BASE_URL,
        wallet_private_key: str = LIGHTER_PRIVATE_KEY,
        api_key_private: str = LIGHTER_API_KEY_PRIVATE,
        api_key_index: int = LIGHTER_API_KEY_INDEX,
        account_index: Optional[int] = None,
    ):
        """Initialize the client.

        Args:
            base_url: Lighter API base URL
            wallet_private_key: Wallet private key (for account lookup)
            api_key_private: API key private key (for signing orders)
            api_key_index: API key index (3-254)
            account_index: Account index (optional, will be looked up if not provided)
        """
        self.base_url = base_url
        self.wallet_private_key = wallet_private_key
        self.api_key_private = api_key_private
        self.api_key_index = api_key_index

        # Use provided account index or look it up later
        if LIGHTER_ACCOUNT_INDEX:
            self.account_index = int(LIGHTER_ACCOUNT_INDEX)
        else:
            self.account_index = account_index

        # Derive L1 address from wallet private key (for account lookup)
        if wallet_private_key:
            eth_account = EthAccount.from_key(wallet_private_key)
            self.l1_address = eth_account.address
        else:
            self.l1_address = ""

        # These will be initialized in connect()
        self.api_client: Optional[ApiClient] = None
        self.signer_client: Optional[SignerClient] = None
        self.account_api: Optional[AccountApi] = None
        self.order_api: Optional[OrderApi] = None
        self.funding_api: Optional[FundingApi] = None

        # Market cache: key = "{symbol}_{market_type}"
        self._markets: dict[str, Market] = {}

        # Connection monitoring
        self._connection_monitor = ConnectionMonitor(self._reconnect)
        self._last_successful_op = time.time()

    async def connect(self) -> None:
        """Initialize API clients and load market data."""
        log.info(f"Connecting to Lighter DEX at {self.base_url}")
        if PROXY_URL:
            # Mask password in log
            masked_proxy = PROXY_URL.split("@")[-1] if "@" in PROXY_URL else PROXY_URL
            log.info(f"Using proxy: {masked_proxy}")

        # Initialize API client for read operations
        config = Configuration(host=self.base_url)
        if PROXY_URL:
            config.proxy = PROXY_URL
        self.api_client = ApiClient(configuration=config)

        # Initialize API endpoints
        self.account_api = AccountApi(self.api_client)
        self.order_api = OrderApi(self.api_client)
        self.funding_api = FundingApi(self.api_client)

        # Get account index from L1 address if not already set
        if self.account_index is None and self.l1_address:
            try:
                sub_accounts = await self.account_api.accounts_by_l1_address(
                    l1_address=self.l1_address
                )
                if sub_accounts.sub_accounts:
                    self.account_index = sub_accounts.sub_accounts[0].index
                    log.info(f"Found account index: {self.account_index}")
                else:
                    log.warning(f"No account found for L1 address: {self.l1_address}")
            except Exception as e:
                log.error(f"Failed to get account index: {e}")
                raise
        elif self.account_index is not None:
            log.info(f"Using configured account index: {self.account_index}")

        # Initialize signer client for write operations (using API key)
        if self.api_key_private and self.account_index is not None:
            # Pre-configure proxy before SignerClient init (SDK makes requests during __init__)
            if PROXY_URL:
                from lighter.api_client import Configuration as LighterConfiguration
                LighterConfiguration.get_default_copy().proxy = PROXY_URL
            self.signer_client = SignerClient(
                url=self.base_url,
                account_index=self.account_index,
                api_private_keys={self.api_key_index: self.api_key_private},
            )
            # Also apply proxy to the instance's api_client
            if PROXY_URL:
                self.signer_client.api_client.configuration.proxy = PROXY_URL
                log.info(f"Signer client proxy configured")
            log.info(f"Signer client initialized with API key index {self.api_key_index}")
        elif not self.api_key_private:
            log.warning("No API key configured - read-only mode (cannot place orders)")

        # Load market data
        await self._load_markets()
        self._connection_monitor.record_success()
        log.info(f"Connected. Loaded {len(self._markets)} markets.")

    async def _reconnect(self) -> None:
        """Reconnect to the exchange after connection loss."""
        log.info("Attempting to reconnect to exchange...")

        # Close existing connections
        try:
            if self.api_client:
                await self.api_client.close()
            if self.signer_client:
                await self.signer_client.close()
        except Exception:
            pass  # Ignore errors during cleanup

        # Re-initialize
        config = Configuration(host=self.base_url)
        if PROXY_URL:
            config.proxy = PROXY_URL
        self.api_client = ApiClient(configuration=config)

        self.account_api = AccountApi(self.api_client)
        self.order_api = OrderApi(self.api_client)
        self.funding_api = FundingApi(self.api_client)

        if self.api_key_private and self.account_index is not None:
            # Pre-configure proxy before SignerClient init
            if PROXY_URL:
                from lighter.api_client import Configuration as LighterConfig
                LighterConfig.get_default_copy().proxy = PROXY_URL
            self.signer_client = SignerClient(
                url=self.base_url,
                account_index=self.account_index,
                api_private_keys={self.api_key_index: self.api_key_private},
            )
            if PROXY_URL:
                self.signer_client.api_client.configuration.proxy = PROXY_URL

        # Verify connection by loading markets
        await self._load_markets()
        log.info("Reconnection successful")

    async def ensure_connected(self) -> bool:
        """Ensure connection is healthy, reconnect if needed."""
        return await self._connection_monitor.ensure_connected()

    async def health_check(self) -> bool:
        """Quick health check - try to fetch orderbook."""
        try:
            if not self.order_api:
                return False
            # Try to get orderbook details (lightweight call)
            await self.order_api.order_book_details()
            self._connection_monitor.record_success()
            return True
        except Exception as e:
            log.warning(f"Health check failed: {e}")
            self._connection_monitor.record_failure()
            return False

    def is_connected(self) -> bool:
        """Check if we believe we're connected."""
        return self._connection_monitor.is_connected

    async def close(self) -> None:
        """Clean up connections."""
        if self.api_client:
            await self.api_client.close()
            self.api_client = None
        if self.signer_client:
            await self.signer_client.close()
            self.signer_client = None
        log.info("Client connections closed")

    async def _load_markets(self) -> None:
        """Load and cache market metadata."""
        if not self.order_api:
            raise RuntimeError("Client not connected")

        try:
            details = await self.order_api.order_book_details()

            # Load perp markets
            for ob in details.order_book_details:
                market_type = MarketType.PERP
                key = f"{ob.symbol}_{market_type.value}"
                self._markets[key] = Market(
                    symbol=ob.symbol,
                    market_id=ob.market_id,
                    market_type=market_type,
                    base_asset_id=ob.base_asset_id,
                    quote_asset_id=ob.quote_asset_id,
                    min_base_amount=Decimal(ob.min_base_amount),
                    min_quote_amount=Decimal(ob.min_quote_amount),
                    size_decimals=ob.size_decimals,
                    price_decimals=ob.price_decimals,
                    taker_fee=Decimal(ob.taker_fee),
                    maker_fee=Decimal(ob.maker_fee),
                )
                log.debug(f"Loaded market: {key} (id={ob.market_id})")

            # Load spot markets
            for ob in details.spot_order_book_details:
                market_type = MarketType.SPOT
                key = f"{ob.symbol}_{market_type.value}"
                self._markets[key] = Market(
                    symbol=ob.symbol,
                    market_id=ob.market_id,
                    market_type=market_type,
                    base_asset_id=ob.base_asset_id,
                    quote_asset_id=ob.quote_asset_id,
                    min_base_amount=Decimal(ob.min_base_amount),
                    min_quote_amount=Decimal(ob.min_quote_amount),
                    size_decimals=ob.size_decimals,
                    price_decimals=ob.price_decimals,
                    taker_fee=Decimal(ob.taker_fee),
                    maker_fee=Decimal(ob.maker_fee),
                )
                log.debug(f"Loaded market: {key} (id={ob.market_id})")

        except Exception as e:
            log.error(f"Failed to load markets: {e}")
            raise

    def get_market(self, symbol: str, market_type: MarketType) -> Optional[Market]:
        """Get market info by symbol and type.

        Args:
            symbol: Market symbol (e.g., "LIT")
            market_type: Market type (SPOT or PERP)

        Returns:
            Market info or None if not found
        """
        key = f"{symbol}_{market_type.value}"
        return self._markets.get(key)

    async def get_account(self) -> Optional[Account]:
        """Get account balances and positions.

        Returns:
            Account info or None if not connected
        """
        if not self.account_api or self.account_index is None:
            return None

        try:
            result = await self.account_api.account(
                by="index",
                value=str(self.account_index)
            )
            if not result.accounts:
                return None

            acc = result.accounts[0]

            # Parse positions
            positions = []
            for pos in acc.positions:
                # sign=1 for long, sign=-1 for short
                size = Decimal(pos.position)
                if pos.sign == -1:
                    size = -size

                positions.append(Position(
                    market_id=pos.market_id,
                    size=size,
                    entry_price=Decimal(pos.avg_entry_price),
                    unrealized_pnl=Decimal(pos.unrealized_pnl),
                    liquidation_price=Decimal(pos.liquidation_price) if pos.liquidation_price else None,
                ))

            # Parse spot asset balances
            assets = []
            if hasattr(acc, 'assets') and acc.assets:
                for asset in acc.assets:
                    assets.append(AssetBalance(
                        asset_id=asset.asset_id,
                        balance=Decimal(str(asset.balance)) if asset.balance else Decimal("0"),
                        locked_balance=Decimal(str(asset.locked_balance)) if hasattr(asset, 'locked_balance') and asset.locked_balance else Decimal("0"),
                    ))

            # Get total asset value if available
            total_asset_value = Decimal("0")
            if hasattr(acc, 'total_asset_value') and acc.total_asset_value:
                total_asset_value = Decimal(str(acc.total_asset_value))

            return Account(
                index=acc.index,
                l1_address=acc.l1_address,
                collateral=Decimal(acc.collateral),
                available_balance=Decimal(acc.available_balance) if acc.available_balance else Decimal("0"),
                positions=positions,
                assets=assets,
                total_asset_value=total_asset_value,
            )

        except Exception as e:
            log.error(f"Failed to get account: {e}")
            raise

    async def get_active_orders(
        self,
        market_id: Optional[int] = None,
    ) -> list[Order]:
        """Get active orders, optionally filtered by market.

        Args:
            market_id: Optional market ID to filter by

        Returns:
            List of active orders
        """
        if not self.order_api or self.account_index is None:
            return []

        # If no market_id specified, we need to query all markets
        market_ids = [market_id] if market_id else [m.market_id for m in self._markets.values()]

        orders = []
        auth_failures = 0
        for mid in market_ids:
            try:
                # Create auth token
                auth_token = None
                if self.signer_client:
                    auth_token, auth_error = self.signer_client.create_auth_token_with_expiry(
                        SignerClient.DEFAULT_10_MIN_AUTH_EXPIRY
                    )
                    if auth_error:
                        auth_failures += 1
                        log.error(f"Failed to create auth token for market {mid}: {auth_error}")
                        continue

                result = await self.order_api.account_active_orders(
                    account_index=self.account_index,
                    market_id=mid,
                    auth=auth_token,
                )

                for o in result.orders:
                    # Use order_index for cancellation - it's the integer ID required by the SDK
                    orders.append(Order(
                        id=str(o.order_index),
                        market_id=o.market_index,
                        side=OrderSide.SELL if o.is_ask else OrderSide.BUY,
                        price=Decimal(o.price),
                        size=Decimal(o.initial_base_amount),
                        status=self._parse_order_status(o.status),
                        order_type=self._parse_order_type(o.type),
                        created_at=datetime.fromtimestamp(o.created_at / 1000) if o.created_at else datetime.now(),
                        filled_size=Decimal(o.filled_base_amount),
                    ))

            except Exception as e:
                log.error(f"Failed to get active orders for market {mid}: {e}")

        # Raise if all markets failed due to auth errors
        if auth_failures > 0 and auth_failures == len(market_ids):
            raise RuntimeError(f"Auth token creation failed for all {auth_failures} markets")

        return orders

    def _parse_order_status(self, status: str) -> OrderStatus:
        """Parse order status string to enum."""
        status_map = {
            "open": OrderStatus.PENDING,
            "filled": OrderStatus.FILLED,
            "cancelled": OrderStatus.CANCELLED,
            "partial": OrderStatus.PARTIALLY_FILLED,
        }
        return status_map.get(status.lower(), OrderStatus.PENDING)

    def _parse_order_type(self, order_type: str) -> OrderType:
        """Parse order type string to enum."""
        type_map = {
            "limit": OrderType.LIMIT,
            "market": OrderType.MARKET,
            "stop_loss": OrderType.STOP_LOSS,
            "stop_loss_limit": OrderType.STOP_LOSS_LIMIT,
            "take_profit": OrderType.TAKE_PROFIT,
            "take_profit_limit": OrderType.TAKE_PROFIT_LIMIT,
        }
        return type_map.get(order_type.lower(), OrderType.LIMIT)

    async def get_mid_price(self, symbol: str, market_type: MarketType) -> Optional[Decimal]:
        """Get mid price from orderbook.

        Args:
            symbol: Market symbol (e.g., "LIT")
            market_type: Market type (SPOT or PERP)

        Returns:
            Mid price or None if orderbook empty
        """
        if not self.order_api:
            return None

        market = self.get_market(symbol, market_type)
        if not market:
            log.error(f"Market not found: {symbol}_{market_type.value}")
            return None

        try:
            result = await self.order_api.order_book_orders(
                market_id=market.market_id,
                limit=1,
            )

            if not result.bids or not result.asks:
                return None

            best_bid = Decimal(result.bids[0].price)
            best_ask = Decimal(result.asks[0].price)
            return (best_bid + best_ask) / 2

        except Exception as e:
            log.error(f"Failed to get mid price for market {symbol}_{market_type.value}: {e}")
            return None

    def _to_price_int(self, price: Decimal, market: Market) -> int:
        """Convert Decimal price to API integer format.

        Args:
            price: Price as Decimal
            market: Market info for decimals

        Returns:
            Price as integer with proper scaling
        """
        return int(price * (10 ** market.price_decimals))

    def _to_size_int(self, size: Decimal, market: Market) -> int:
        """Convert Decimal size to API integer format.

        Args:
            size: Size as Decimal
            market: Market info for decimals

        Returns:
            Size as integer with proper scaling
        """
        return int(size * (10 ** market.size_decimals))

    async def place_limit_order(
        self,
        symbol: str,
        market_type: MarketType,
        side: OrderSide,
        price: Decimal,
        size: Decimal,
        post_only: bool = True,
    ) -> Optional[Order]:
        """Place a limit order with retry logic.

        Args:
            symbol: Market symbol (e.g., "LIT")
            market_type: Market type (SPOT or PERP)
            side: BUY or SELL
            price: Order price
            size: Order size in base asset
            post_only: If True, use POST_ONLY time in force (default True)

        Returns:
            Order object if successful, None otherwise
        """
        if not self.signer_client:
            log.error("Signer client not initialized")
            return None

        market = self.get_market(symbol, market_type)
        if not market:
            log.error(f"Market not found: {symbol}_{market_type.value}")
            return None

        async def _place_order():
            is_ask = 1 if side == OrderSide.SELL else 0
            price_int = self._to_price_int(price, market)
            size_int = self._to_size_int(size, market)

            # Map post_only to time in force
            if post_only:
                tif = SignerClient.ORDER_TIME_IN_FORCE_POST_ONLY
            else:
                tif = SignerClient.ORDER_TIME_IN_FORCE_GOOD_TILL_TIME

            tx, resp, error = await self.signer_client.create_order(
                market_index=market.market_id,
                client_order_index=0,
                base_amount=size_int,
                price=price_int,
                is_ask=is_ask,
                order_type=SignerClient.ORDER_TYPE_LIMIT,
                time_in_force=tif,
                reduce_only=False,
            )

            if error:
                # Check if it's a transient error worth retrying
                if is_transient_error(Exception(str(error))):
                    raise ConnectionError(f"Transient error: {error}")
                log.error(f"Failed to place limit order: {error}")
                return None

            if resp and resp.tx_hash:
                self._connection_monitor.record_success()
                # Use order_index as id for cancellation if available, otherwise tx_hash
                order_id = str(resp.order_index) if hasattr(resp, 'order_index') and resp.order_index else resp.tx_hash
                return Order(
                    id=order_id,
                    market_id=market.market_id,
                    side=side,
                    price=price,
                    size=size,
                    status=OrderStatus.PENDING,
                    order_type=OrderType.LIMIT,
                    tx_hash=resp.tx_hash,
                    created_at=datetime.now(),
                    filled_size=Decimal("0"),
                )
            return None

        result, error = await retry_async(
            _place_order,
            config=RETRY_STANDARD,
            operation_name=f"place {side.value} limit order @ {price}",
        )

        if error:
            self._connection_monitor.record_failure()
            return None

        if result:
            log.info(
                f"Placed {side.value} limit order: {size} @ {price} "
                f"(market={market.symbol}, id={result.id}, tx={result.tx_hash})"
            )

        return result

    async def place_market_order(
        self,
        symbol: str,
        market_type: MarketType,
        side: OrderSide,
        size: Decimal,
    ) -> Optional[Order]:
        """Place a market order with retry logic.

        Args:
            symbol: Market symbol (e.g., "LIT")
            market_type: Market type (SPOT or PERP)
            side: BUY or SELL
            size: Order size in base asset

        Returns:
            Order object if successful, None otherwise
        """
        if not self.signer_client:
            log.error("Signer client not initialized")
            return None

        market = self.get_market(symbol, market_type)
        if not market:
            log.error(f"Market not found: {symbol}_{market_type.value}")
            return None

        # Capture position before order for fill verification (perp markets only)
        position_before: Optional[Decimal] = None
        if market_type == MarketType.PERP:
            positions = await self.get_positions()
            for pos in positions:
                if pos.market_id == market.market_id:
                    position_before = pos.size
                    break
            if position_before is None:
                position_before = Decimal("0")

        async def _place_order():
            is_ask = 1 if side == OrderSide.SELL else 0
            size_int = self._to_size_int(size, market)

            # Get current price for avg_execution_price
            mid_price = await self.get_mid_price(symbol, market_type)
            if not mid_price:
                raise ConnectionError("Cannot place market order: no mid price available")

            # Use mid price with 1% slippage
            slippage = Decimal("0.01")
            if side == OrderSide.BUY:
                avg_price = mid_price * (1 + slippage)
            else:
                avg_price = mid_price * (1 - slippage)

            price_int = self._to_price_int(avg_price, market)

            tx, resp, error = await self.signer_client.create_market_order(
                market_index=market.market_id,
                client_order_index=0,
                base_amount=size_int,
                avg_execution_price=price_int,
                is_ask=is_ask,
                reduce_only=False,
            )

            if error:
                if is_transient_error(Exception(str(error))):
                    raise ConnectionError(f"Transient error: {error}")
                log.error(f"Failed to place market order: {error}")
                return None

            if resp and resp.tx_hash:
                self._connection_monitor.record_success()
                # Use order_index as id for cancellation if available, otherwise tx_hash
                order_id = str(resp.order_index) if hasattr(resp, 'order_index') and resp.order_index else resp.tx_hash
                return Order(
                    id=order_id,
                    market_id=market.market_id,
                    side=side,
                    price=avg_price,
                    size=size,
                    status=OrderStatus.PENDING,  # Will be verified below
                    order_type=OrderType.MARKET,
                    tx_hash=resp.tx_hash,
                    created_at=datetime.now(),
                    filled_size=Decimal("0"),  # Will be verified below
                )
            return None

        result, error = await retry_async(
            _place_order,
            config=RETRY_STANDARD,
            operation_name=f"place {side.value} market order",
        )

        if error:
            self._connection_monitor.record_failure()
            return None

        if result:
            # Verify actual fill by querying position change (perp markets only)
            if market_type == MarketType.PERP and position_before is not None:
                await asyncio.sleep(0.5)  # Brief delay for settlement
                positions = await self.get_positions()
                position_after = Decimal("0")
                for pos in positions:
                    if pos.market_id == market.market_id:
                        position_after = pos.size
                        break

                # Calculate actual filled size from position change
                position_delta = abs(position_after - position_before)
                if position_delta > 0:
                    result.filled_size = position_delta
                    result.status = OrderStatus.FILLED if position_delta >= size else OrderStatus.PARTIALLY_FILLED
                    log.info(
                        f"Market order fill verified: requested={size}, filled={position_delta} "
                        f"(position: {position_before} -> {position_after})"
                    )
                else:
                    # No position change detected - order may have failed or not settled
                    result.status = OrderStatus.PENDING
                    log.warning(
                        f"Market order fill not verified: no position change detected "
                        f"(tx={result.tx_hash})"
                    )
            else:
                # For spot markets, assume filled (no position tracking available)
                result.filled_size = size
                result.status = OrderStatus.FILLED

            log.info(
                f"Placed {side.value} market order: {size} "
                f"(market={market.symbol}, id={result.id}, tx={result.tx_hash}, "
                f"status={result.status.value}, filled={result.filled_size})"
            )

        return result

    async def cancel_order(
        self,
        order_id: str,
        market_id: Optional[int] = None,
    ) -> bool:
        """Cancel an order.

        Args:
            order_id: Order index as string (from get_active_orders, not from place_limit_order).
                     The SDK requires the numeric order_index for cancellation.
            market_id: Market ID for the order. If not provided, will search active orders (slow).

        Returns:
            True if successful, False otherwise
        """
        if not self.signer_client:
            log.error("Signer client not initialized")
            return False

        try:
            order_index = int(order_id)

            # Use provided market_id or search for it
            if market_id is None:
                active_orders = await self.get_active_orders()
                order = next((o for o in active_orders if o.id == order_id), None)
                if not order:
                    log.error(f"Order not found: {order_id}")
                    return False
                market_id = order.market_id

            tx, resp, error = await self.signer_client.cancel_order(
                market_index=market_id,
                order_index=order_index,
            )

            if error:
                log.error(f"Failed to cancel order: {error}")
                return False

            log.info(f"Cancelled order {order_id}")
            return True

        except Exception as e:
            log.error(f"Failed to cancel order: {e}")
            return False

    async def cancel_all_orders(
        self,
        market_id: Optional[int] = None,
    ) -> int:
        """Cancel all orders, optionally for a specific market.

        Args:
            market_id: Optional market ID to filter by (cancels all if None)

        Returns:
            Number of orders cancelled
        """
        if not self.signer_client:
            log.error("Signer client not initialized")
            return 0

        try:
            timestamp_ms = int(time.time() * 1000)

            # If market_id specified, cancel only orders for that market
            if market_id is not None:
                active_orders = await self.get_active_orders(market_id=market_id)
                cancelled_count = 0
                for order in active_orders:
                    order_index = int(order.id)
                    tx, resp, error = await self.signer_client.cancel_order(
                        market_index=market_id,
                        order_index=order_index,
                    )
                    if error:
                        log.error(f"Failed to cancel order {order.id}: {error}")
                    else:
                        cancelled_count += 1
                log.info(f"Cancelled {cancelled_count} orders for market {market_id}")
                return cancelled_count

            # Get count of active orders before cancelling
            active_orders = await self.get_active_orders()
            order_count = len(active_orders)

            # Cancel all orders across all markets
            tx, resp, error = await self.signer_client.cancel_all_orders(
                time_in_force=SignerClient.CANCEL_ALL_TIF_IMMEDIATE,
                timestamp_ms=timestamp_ms,
            )

            if error:
                log.error(f"Failed to cancel all orders: {error}")
                return 0

            log.info(f"Cancelled {order_count} orders")
            return order_count

        except Exception as e:
            log.error(f"Failed to cancel all orders: {e}")
            return 0

    async def get_funding_rate(self, symbol: str) -> Optional[FundingRate]:
        """Get current funding rate for a perp market.

        Args:
            symbol: Market symbol (e.g., "LIT")

        Returns:
            FundingRate or None if not found
        """
        if not self.funding_api:
            return None

        market = self.get_market(symbol, MarketType.PERP)
        if not market:
            log.error(f"Perp market not found: {symbol}")
            return None

        try:
            result = await self.funding_api.funding_rates()

            for fr in result.funding_rates:
                if fr.market_id == market.market_id:
                    return FundingRate(
                        market_id=fr.market_id,
                        rate=Decimal(str(fr.rate)),
                        timestamp=datetime.now(),
                    )

            return None

        except Exception as e:
            log.error(f"Failed to get funding rate for {symbol}: {e}")
            return None

    async def get_positions(self) -> list[Position]:
        """Get all perp positions.

        Returns:
            List of positions
        """
        account = await self.get_account()
        if not account:
            return []
        return account.positions

    async def place_stop_loss_order(
        self,
        symbol: str,
        market_type: MarketType,
        side: OrderSide,
        size: Decimal,
        trigger_price: Decimal,
        reduce_only: bool = True,
    ) -> Optional[Order]:
        """Place a stop-loss market order.

        The order triggers when price crosses the trigger_price.
        For a SHORT position stop-loss: side=BUY, triggers when price rises to trigger_price.
        For a LONG position stop-loss: side=SELL, triggers when price falls to trigger_price.

        Args:
            symbol: Market symbol (e.g., "LIT")
            market_type: Market type (SPOT or PERP)
            side: BUY (to close short) or SELL (to close long)
            size: Order size in base asset
            trigger_price: Price at which to trigger the stop
            reduce_only: If True, only reduces position (default True for stop-loss)

        Returns:
            Order object if successful, None otherwise
        """
        if not self.signer_client:
            log.error("Signer client not initialized")
            return None

        market = self.get_market(symbol, market_type)
        if not market:
            log.error(f"Market not found: {symbol}_{market_type.value}")
            return None

        try:
            is_ask = 1 if side == OrderSide.SELL else 0
            size_int = self._to_size_int(size, market)
            trigger_price_int = self._to_price_int(trigger_price, market)

            # For stop-loss market order, use trigger_price as price too
            # (will execute at market when triggered)
            price_int = trigger_price_int

            tx, resp, error = await self.signer_client.create_order(
                market_index=market.market_id,
                client_order_index=0,
                base_amount=size_int,
                price=price_int,
                is_ask=is_ask,
                order_type=SignerClient.ORDER_TYPE_STOP_LOSS,
                time_in_force=SignerClient.ORDER_TIME_IN_FORCE_IMMEDIATE_OR_CANCEL,
                reduce_only=reduce_only,
                trigger_price=trigger_price_int,
            )

            if error:
                log.error(f"Failed to place stop-loss order: {error}")
                return None

            if resp and resp.tx_hash:
                # Use order_index as id for cancellation if available, otherwise tx_hash
                order_id = str(resp.order_index) if hasattr(resp, 'order_index') and resp.order_index else resp.tx_hash
                log.info(
                    f"Placed STOP-LOSS {side.value} order: {size} @ trigger ${trigger_price} "
                    f"(market={market.symbol}, id={order_id}, tx={resp.tx_hash})"
                )
                return Order(
                    id=order_id,
                    market_id=market.market_id,
                    side=side,
                    price=trigger_price,
                    size=size,
                    status=OrderStatus.PENDING,
                    order_type=OrderType.STOP_LOSS,
                    tx_hash=resp.tx_hash,
                    created_at=datetime.now(),
                    filled_size=Decimal("0"),
                )

            return None

        except Exception as e:
            log.error(f"Failed to place stop-loss order: {e}")
            return None
