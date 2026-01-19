# lithood/client.py
"""Lighter DEX API client wrapper."""

from decimal import Decimal
from datetime import datetime
from typing import Optional

from eth_account import Account as EthAccount

from lighter import (
    Configuration,
    ApiClient,
    SignerClient,
    AccountApi,
    OrderApi,
    FundingApi,
)

from lithood.config import LIGHTER_BASE_URL, LIGHTER_PRIVATE_KEY
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
    FundingRate,
)


class LighterClient:
    """Client for interacting with the Lighter DEX API."""

    def __init__(
        self,
        base_url: str = LIGHTER_BASE_URL,
        private_key: str = LIGHTER_PRIVATE_KEY,
    ):
        """Initialize the client.

        Args:
            base_url: Lighter API base URL
            private_key: Private key for signing transactions
        """
        self.base_url = base_url
        self.private_key = private_key

        # Derive L1 address from private key
        if private_key:
            eth_account = EthAccount.from_key(private_key)
            self.l1_address = eth_account.address
        else:
            self.l1_address = ""

        # These will be initialized in connect()
        self.api_client: Optional[ApiClient] = None
        self.signer_client: Optional[SignerClient] = None
        self.account_index: Optional[int] = None
        self.account_api: Optional[AccountApi] = None
        self.order_api: Optional[OrderApi] = None
        self.funding_api: Optional[FundingApi] = None

        # Market cache: key = "{symbol}_{market_type}"
        self._markets: dict[str, Market] = {}

    async def connect(self) -> None:
        """Initialize API clients and load market data."""
        log.info(f"Connecting to Lighter DEX at {self.base_url}")

        # Initialize API client for read operations
        config = Configuration(host=self.base_url)
        self.api_client = ApiClient(configuration=config)

        # Initialize API endpoints
        self.account_api = AccountApi(self.api_client)
        self.order_api = OrderApi(self.api_client)
        self.funding_api = FundingApi(self.api_client)

        # Get account index from L1 address
        if self.l1_address:
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

        # Initialize signer client for write operations
        if self.private_key and self.account_index is not None:
            self.signer_client = SignerClient(
                url=self.base_url,
                account_index=self.account_index,
                api_private_keys={SignerClient.DEFAULT_API_KEY_INDEX: self.private_key},
            )
            log.info("Signer client initialized")

        # Load market data
        await self._load_markets()
        log.info(f"Connected. Loaded {len(self._markets)} markets.")

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

            return Account(
                index=acc.index,
                l1_address=acc.l1_address,
                collateral=Decimal(acc.collateral),
                available_balance=Decimal(acc.available_balance) if acc.available_balance else Decimal("0"),
                positions=positions,
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
        for mid in market_ids:
            try:
                # Create auth token
                auth_token = None
                if self.signer_client:
                    auth_token = self.signer_client.create_auth_token_with_expiry(
                        SignerClient.DEFAULT_10_MIN_AUTH_EXPIRY
                    )

                result = await self.order_api.account_active_orders(
                    account_index=self.account_index,
                    market_id=mid,
                    auth=auth_token,
                )

                for o in result.orders:
                    orders.append(Order(
                        id=o.order_id,
                        market_id=o.market_index,
                        side=OrderSide.SELL if o.is_ask else OrderSide.BUY,
                        price=Decimal(o.price),
                        size=Decimal(o.initial_base_amount),
                        status=self._parse_order_status(o.status),
                        order_type=OrderType.LIMIT if o.type == "limit" else OrderType.MARKET,
                        created_at=datetime.fromtimestamp(o.created_at / 1000) if o.created_at else datetime.now(),
                        filled_size=Decimal(o.filled_base_amount),
                    ))

            except Exception as e:
                log.error(f"Failed to get active orders for market {mid}: {e}")

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
    ) -> Optional[str]:
        """Place a limit order.

        Args:
            symbol: Market symbol (e.g., "LIT")
            market_type: Market type (SPOT or PERP)
            side: BUY or SELL
            price: Order price
            size: Order size in base asset
            post_only: If True, use POST_ONLY time in force (default True)

        Returns:
            Order ID if successful, None otherwise
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
                log.error(f"Failed to place limit order: {error}")
                return None

            if resp and resp.tx_hash:
                log.info(
                    f"Placed {side.value} limit order: {size} @ {price} "
                    f"(market={market.symbol}, tx={resp.tx_hash})"
                )
                return resp.tx_hash

            return None

        except Exception as e:
            log.error(f"Failed to place limit order: {e}")
            return None

    async def place_market_order(
        self,
        symbol: str,
        market_type: MarketType,
        side: OrderSide,
        size: Decimal,
    ) -> Optional[str]:
        """Place a market order.

        Args:
            symbol: Market symbol (e.g., "LIT")
            market_type: Market type (SPOT or PERP)
            side: BUY or SELL
            size: Order size in base asset

        Returns:
            Order ID if successful, None otherwise
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

            # Get current price for avg_execution_price
            mid_price = await self.get_mid_price(symbol, market_type)
            if not mid_price:
                log.error("Cannot place market order: no mid price available")
                return None

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
                log.error(f"Failed to place market order: {error}")
                return None

            if resp and resp.tx_hash:
                log.info(
                    f"Placed {side.value} market order: {size} "
                    f"(market={market.symbol}, tx={resp.tx_hash})"
                )
                return resp.tx_hash

            return None

        except Exception as e:
            log.error(f"Failed to place market order: {e}")
            return None

    async def cancel_order(
        self,
        order_id: str,
    ) -> bool:
        """Cancel an order.

        Args:
            order_id: Order ID string to cancel

        Returns:
            True if successful, False otherwise
        """
        if not self.signer_client:
            log.error("Signer client not initialized")
            return False

        try:
            order_index = int(order_id)

            # Find the order in active orders to get its market_id
            active_orders = await self.get_active_orders()
            order = next((o for o in active_orders if o.id == order_id), None)
            if not order:
                log.error(f"Order not found: {order_id}")
                return False

            tx, resp, error = await self.signer_client.cancel_order(
                market_index=order.market_id,
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
    ) -> bool:
        """Cancel all orders, optionally for a specific market.

        Args:
            market_id: Optional market ID to filter by (cancels all if None)

        Returns:
            True if successful, False otherwise
        """
        if not self.signer_client:
            log.error("Signer client not initialized")
            return False

        try:
            import time
            timestamp_ms = int(time.time() * 1000)

            # If market_id specified, cancel only orders for that market
            if market_id is not None:
                active_orders = await self.get_active_orders(market_id=market_id)
                success = True
                for order in active_orders:
                    order_index = int(order.id)
                    tx, resp, error = await self.signer_client.cancel_order(
                        market_index=market_id,
                        order_index=order_index,
                    )
                    if error:
                        log.error(f"Failed to cancel order {order.id}: {error}")
                        success = False
                if success:
                    log.info(f"Cancelled all orders for market {market_id}")
                return success

            # Cancel all orders across all markets
            tx, resp, error = await self.signer_client.cancel_all_orders(
                time_in_force=SignerClient.CANCEL_ALL_TIF_IMMEDIATE,
                timestamp_ms=timestamp_ms,
            )

            if error:
                log.error(f"Failed to cancel all orders: {error}")
                return False

            log.info("Cancelled all orders")
            return True

        except Exception as e:
            log.error(f"Failed to cancel all orders: {e}")
            return False

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
