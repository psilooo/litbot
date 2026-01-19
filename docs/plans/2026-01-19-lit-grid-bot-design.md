# LIT Grid Trading Bot - Design Document

**Date:** 2026-01-19
**Status:** Approved

---

## Overview

Automated trading bot for Lighter DEX combining:
1. Spot grid trading (volatility extraction)
2. Perp hedge (downside protection + funding yield)
3. Floor protection (guarantees $25k minimum)

**Target:** $75,000 from $36,154 starting capital

---

## Technical Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Language | Python | Fast iteration, good for trading bots |
| Runtime | Raspberry Pi 5 (Kali) | 24/7 operation, user already has it |
| State | SQLite | Atomic writes, survives crashes, easy backup |
| Credentials | Environment variables | Simple, standard practice |
| Monitoring | Logs via SSH (Tailscale) | User can access from anywhere |
| Testing | Mini-test first, then live with minimal size | Verify API integration before risking capital |

---

## Project Structure

```
lithood/
├── .env                     # API credentials (gitignored)
├── .env.example             # Template for credentials
├── config.py                # All strategy parameters from summary.md
├── requirements.txt         # Dependencies
│
├── lighter/
│   ├── __init__.py
│   ├── client.py            # REST API client (auth, requests)
│   ├── signer.py            # Transaction signing (nonce, JWT)
│   └── types.py             # Data classes for orders, positions
│
├── bot/
│   ├── __init__.py
│   ├── grid.py              # Spot grid engine (buy/sell cycling)
│   ├── hedge.py             # Perp hedge manager (short, stop-loss, re-entry)
│   ├── floor.py             # Floor protection (tiered de-risk)
│   └── state.py             # SQLite state management
│
├── scripts/
│   ├── test_connectivity.py # Mini-test: spot + perp lifecycle
│   └── run_bot.py           # Main entry point
│
└── logs/                    # Log files (gitignored)
```

---

## Component Designs

### 1. API Client (`lighter/client.py`)

```python
class LighterClient:
    def __init__(self, base_url: str, api_key: str, private_key: str):
        self.base_url = base_url
        self.api_key = api_key
        self.private_key = private_key
        self.session = requests.Session()

    # Core methods
    def get_nonce(self) -> int
    def get_account(self) -> Account
    def get_orderbook(self, market: str) -> OrderBook
    def get_active_orders(self, market: str = None) -> list[Order]
    def get_positions(self) -> list[Position]
    def get_funding_rate(self, market: str) -> FundingRate

    # Order operations (all require nonce + signing)
    def place_limit_order(self, market: str, side: str, price: Decimal, size: Decimal) -> Order
    def place_market_order(self, market: str, side: str, size: Decimal) -> Order
    def cancel_order(self, order_id: str) -> bool
    def cancel_all_orders(self, market: str = None) -> int

    # Perp-specific
    def open_perp_position(self, market: str, side: str, size: Decimal, leverage: Decimal) -> Position
    def close_perp_position(self, market: str) -> Position
```

Key decisions:
- Synchronous requests (simpler than async, 2s polling is fine)
- Decimal for prices/sizes (avoids floating point errors)
- Nonce handled internally per order

---

### 2. State Management (`bot/state.py`)

```sql
-- Track all orders placed by the bot
CREATE TABLE orders (
    id TEXT PRIMARY KEY,           -- Lighter order ID
    market TEXT NOT NULL,          -- "LIT-USDC" or "LIT-USDC-PERP"
    side TEXT NOT NULL,            -- "buy" or "sell"
    price REAL NOT NULL,
    size REAL NOT NULL,
    status TEXT NOT NULL,          -- "pending", "filled", "cancelled"
    grid_level INTEGER,            -- Which grid level (1-8), NULL if not grid
    created_at TIMESTAMP,
    filled_at TIMESTAMP
);

-- Track hedge position lifecycle
CREATE TABLE hedge_history (
    id INTEGER PRIMARY KEY,
    action TEXT NOT NULL,          -- "open", "stop_loss", "re_entry", "close"
    price REAL NOT NULL,
    size REAL NOT NULL,
    pnl REAL,                      -- Realized P&L on close
    funding_earned REAL,
    timestamp TIMESTAMP
);

-- Current bot state (single row, updated frequently)
CREATE TABLE bot_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP
);
```

State sync on startup:
```python
def sync_with_exchange(self, client: LighterClient):
    """Reconcile local state with exchange reality."""
    account = client.get_account()
    active_orders = client.get_active_orders()
    positions = client.get_positions()

    # Update balances
    self.set("actual_lit", account.lit_balance)
    self.set("actual_usdc", account.usdc_balance)

    # Reconcile orders
    active_ids = {o.id for o in active_orders}
    for local_order in self.get_pending_orders():
        if local_order.id not in active_ids:
            self.mark_filled(local_order.id)

    # Sync hedge state
    perp_position = next((p for p in positions if p.market == "LIT-USDC-PERP"), None)
    self.set("hedge_active", perp_position and perp_position.size < 0)
```

---

### 3. Grid Engine (`bot/grid.py`)

```python
class GridEngine:
    def __init__(self, client: LighterClient, state: StateManager, config: GridConfig):
        self.client = client
        self.state = state
        self.config = config

    def initialize_grid(self):
        """Place initial grid orders based on current price."""
        current_price = self.client.get_orderbook("LIT-USDC").mid_price

        for level in self.config.buy_levels:
            if level.price < current_price:
                self.place_grid_buy(level)

        for level in self.config.sell_levels:
            if level.price > current_price:
                self.place_grid_sell(level)

    def check_fills(self):
        """Poll for filled orders, trigger cycling."""
        if self.state.get("grid_paused"):
            return

        active = self.client.get_active_orders()
        active_ids = {o.id for o in active}

        for order in self.state.get_pending_orders():
            if order.id not in active_ids:
                self.on_fill(order)

    def on_fill(self, order: Order):
        """Filled buy → place sell. Filled sell → place buy."""
        self.state.mark_filled(order.id)

        if order.side == "buy":
            sell_price = order.price * 1.03  # 3% above
            self.place_grid_sell(price=sell_price, size=order.size)
        else:
            buy_price = order.price * 0.97   # 3% below
            usdc = order.size * order.price * 0.97  # Keep 3% profit
            self.place_grid_buy(price=buy_price, usdc=usdc)

    def pause_buys(self):
        self.state.set("grid_paused", True)

    def cancel_all(self):
        for order in self.state.get_pending_orders():
            self.client.cancel_order(order.id)
```

---

### 4. Hedge Manager (`bot/hedge.py`)

```python
class HedgeManager:
    def __init__(self, client: LighterClient, state: StateManager, config: HedgeConfig):
        self.client = client
        self.state = state
        self.config = config  # short_size=3000, stop_loss=1.95, re_entry=1.75

    def initialize(self):
        """Open initial 3,000 LIT short on startup."""
        if not self.state.get("hedge_active"):
            self.open_short()

    def open_short(self):
        position = self.client.open_perp_position(
            market="LIT-USDC-PERP",
            side="short",
            size=Decimal("3000"),
            leverage=Decimal("3.36")
        )
        self.state.set("hedge_active", True)
        self.state.set("hedge_entry_price", position.entry_price)

    def check(self, current_price: Decimal):
        """Manage stop-loss and re-entry."""
        if self.state.get("hedge_active"):
            if current_price >= self.config.stop_loss:  # $1.95
                self.close_short(reason="stop_loss")
                self.state.set("last_stop_loss_time", time.time())
        else:
            last_stop = self.state.get("last_stop_loss_time")
            cooldown_passed = (time.time() - last_stop) > 24 * 3600

            if current_price <= self.config.re_entry_price and cooldown_passed:
                self.open_short()

    def check_funding(self):
        """Pause hedge if funding negative >24h."""
        rate = self.client.get_funding_rate("LIT-USDC-PERP")
        # Track negative funding duration, close if >24h
```

Parameters from summary.md:
- Short size: 3,000 LIT
- Stop-loss: $1.95 (max loss $810)
- Re-entry: $1.75 after 24h cooldown
- Pause if funding negative >24h

---

### 5. Floor Protection (`bot/floor.py`)

```python
class FloorProtection:
    def __init__(self, client, state, grid, hedge, config):
        self.client = client
        self.state = state
        self.grid = grid
        self.hedge = hedge
        self.config = config

    def check(self, current_price: Decimal):
        """Execute tiers if triggered."""
        portfolio_value = self.calculate_portfolio_value(current_price)
        tier = self.state.get("floor_tier_triggered", 0)

        if current_price <= 1.50 and tier < 1:
            self.grid.pause_buys()
            self.alert("Price warning: $1.50 - grid buys paused")
            self.state.set("floor_tier_triggered", 1)

        if current_price <= 1.40 and tier < 2:
            self.market_sell_lit(2000, "reserve")
            self.state.set("floor_tier_triggered", 2)

        if current_price <= 1.30 and tier < 3:
            self.market_sell_lit(2175, "reserve")
            self.state.set("floor_tier_triggered", 3)

        if current_price <= 1.20 and tier < 4:
            self.grid.cancel_all()
            self.market_sell_lit(3000, "grid")
            self.state.set("floor_tier_triggered", 4)

        if portfolio_value <= 26000:
            self.emergency_exit()

    def emergency_exit(self):
        """Sell everything, close hedge, halt bot."""
        self.hedge.close_short(reason="emergency")
        self.grid.cancel_all()
        self.client.cancel_all_orders()
        self.market_sell_all_lit()
        self.state.set("bot_halted", True)
        self.alert("EMERGENCY: Floor protection - bot halted at $25k")
```

Tiers from summary.md:
| Price | Action |
|-------|--------|
| $1.50 | Pause grid buys |
| $1.40 | Sell 2,000 reserve LIT |
| $1.30 | Sell 2,175 reserve LIT |
| $1.20 | Cancel grid, sell 3,000 LIT |
| $26k portfolio | Emergency exit |

---

### 6. Main Loop (`scripts/run_bot.py`)

```python
def main():
    client = LighterClient(
        base_url=os.getenv("LIGHTER_API_URL"),
        api_key=os.getenv("LIGHTER_API_KEY"),
        private_key=os.getenv("LIGHTER_PRIVATE_KEY"),
    )
    state = StateManager("lithood.db")

    grid = GridEngine(client, state, GRID_CONFIG)
    hedge = HedgeManager(client, state, HEDGE_CONFIG)
    floor = FloorProtection(client, state, grid, hedge, FLOOR_CONFIG)

    # Startup
    state.sync_with_exchange(client)
    hedge.initialize()
    grid.initialize_grid()

    # Main loop
    while not state.get("bot_halted"):
        try:
            current_price = client.get_orderbook("LIT-USDC").mid_price

            floor.check(current_price)      # Priority 1
            hedge.check(current_price)      # Priority 2
            hedge.check_funding()           # Priority 3
            grid.check_fills()              # Priority 4

            if time.time() % 300 < 2:
                log_status(state, current_price)

            time.sleep(2)
        except Exception as e:
            log(f"Error: {e}")
            time.sleep(10)
```

---

### 7. Mini-Test (`scripts/test_connectivity.py`)

```python
def test_full_lifecycle():
    """Complete round-trip verification for spot and perp."""

    # 1. Baseline account state
    account = client.get_account()
    starting_lit = account.lit_balance
    starting_usdc = account.usdc_balance
    starting_orders = len(client.get_active_orders())
    starting_positions = len(client.get_positions())

    # 2. Spot order test
    price = client.get_orderbook("LIT-USDC").mid_price * Decimal("0.90")
    order = client.place_limit_order("LIT-USDC", "buy", price, Decimal("1"))
    time.sleep(1)
    orders = client.get_active_orders()
    assert any(o.id == order.id for o in orders)
    client.cancel_order(order.id)

    # 3. Spot fill test
    order = client.place_market_order("LIT-USDC", "buy", Decimal("1"))
    time.sleep(2)
    account = client.get_account()
    assert account.lit_balance > starting_lit

    # 4. Perp order test
    price = client.get_orderbook("LIT-USDC-PERP").mid_price * Decimal("1.10")
    order = client.place_limit_order("LIT-USDC-PERP", "sell", price, Decimal("1"))
    time.sleep(1)
    orders = client.get_active_orders(market="LIT-USDC-PERP")
    assert any(o.id == order.id for o in orders)
    client.cancel_order(order.id)

    # 5. Perp position test
    position = client.open_perp_position("LIT-USDC-PERP", "short", Decimal("1"), Decimal("2"))
    time.sleep(1)
    positions = client.get_positions()
    assert any(p.market == "LIT-USDC-PERP" for p in positions)
    client.close_perp_position("LIT-USDC-PERP")

    # 6. Final state
    account = client.get_account()
    log(f"Net cost: ~${starting_usdc - account.usdc_balance:.2f}")
```

Run this before deploying the grid. Cost: ~$3.40

---

## Capital Allocation

From summary.md:

```python
ALLOCATION = {
    "core_lit": 8_000,           # Never grid, held for $2.50-$4.50 sells
    "grid_sell_lit": 5_000,      # Active grid cycling
    "reserve_lit": 4_175,        # Deploy on dips / floor triggers
    "grid_buy_usdc": 5_000,      # Active grid cycling
    "hedge_margin_usdc": 1_500,  # Perp short margin
    "cash_reserve_usdc": 800,    # Emergency buffer
}
```

---

## Execution Plan

1. Run mini-test to verify API integration (~$3.40)
2. Deploy bot with full strategy on Pi 5
3. Monitor via SSH + Tailscale

---

## Key Guarantees

- **$25k floor is sacred** - emergency exit halts bot
- **Exchange is source of truth** - state syncs on restart
- **Grid maximizes profit in chop** - 3% spreads, auto-cycling
- **Hedge protects downside** - moves floor breach from $1.03 to $0.89
- **Core 8,000 LIT held for upside** - sold only at $2.50-$4.50+
