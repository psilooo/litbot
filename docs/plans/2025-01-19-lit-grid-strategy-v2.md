# LIT Grid Trading Strategy v2 — Corrected Specification

## Changes from v1

| Component | v1 (Flawed) | v2 (Fixed) |
|-----------|-------------|------------|
| Grid structure | Separate buy/sell zones with dead gap | Paired levels, interleaved prices |
| Cycling logic | `price × 1.03` formula | Fixed pair mapping |
| Hedge re-entry | Only at $1.75 | Dynamic: 5% pullback from high OR below entry price |
| Stop-loss | Fixed $1.95 | Dynamic: 16% above entry |
| Profit estimates | $6,750-$11,250/month | $700-$2,800/month (realistic) |
| Entry price | Hardcoded $1.68 | Dynamic: read at startup |
| Core sells | Unspecified | Limit orders placed at startup |

---

## Position & Constraints

| Metric | Value |
|--------|-------|
| Starting capital | $36,154 (17,175 LIT + $7,300 USDC) |
| Hard floor | $25,000 |
| Target exit | $75,000 |
| Entry price | **Dynamic** (read at bot startup) |

---

## Initialization

The bot reads current market price at startup and uses it as the reference for all calculations.

```python
def initialize():
    """Called once at bot startup."""
    state.entry_price = get_current_market_price()  # e.g., $1.64
    state.startup_time = now()

    # Generate grid levels centered on entry price
    generate_grid_levels(state.entry_price)

    # Place all grid orders
    place_initial_grid_orders(state.entry_price)

    # Place all core sell limit orders
    place_core_sell_orders()

    # Open initial hedge
    open_hedge(state.entry_price)

    log(f"Bot initialized at ${state.entry_price:.3f}")
```

---

## Capital Allocation

| Category | LIT | USDC | Purpose |
|----------|-----|------|---------|
| Core (never grid) | 8,000 | - | Upside capture to $75k |
| Grid | 5,000 | $5,000 | Active grid cycling |
| Reserve | 4,175 | - | Deploy on dips / liquidate on stops |
| Hedge margin | - | $1,500 | 3k LIT short at 4x leverage |
| Cash reserve | - | $800 | Emergency buffer |
| **Total** | **17,175** | **$7,300** | |

---

## Spot Grid — Dynamic Generation

### Rationale

Grid levels are generated at startup based on current price. Each buy level has ONE designated sell partner. The grid is **static after initialization** — levels don't shift as price moves.

### Grid Generation Logic

```python
GRID_CONFIG = {
    "num_pairs": 8,
    "size_usdc_per_pair": 625,
    "buy_spacing_pct": 0.025,   # 2.5% between buy levels
    "pair_spread_pcts": [       # Spread between buy and sell for each pair
        0.024,  # Pair 1: 2.4% (tightest, most frequent)
        0.049,  # Pair 2: 4.9%
        0.074,  # Pair 3: 7.4%
        0.100,  # Pair 4: 10.0%
        0.127,  # Pair 5: 12.7%
        0.154,  # Pair 6: 15.4%
        0.182,  # Pair 7: 18.2%
        0.211,  # Pair 8: 21.1% (widest, least frequent)
    ],
}

def generate_grid_levels(entry_price):
    """Generate grid pairs centered on entry price."""
    pairs = []

    for i in range(GRID_CONFIG["num_pairs"]):
        # Buy levels descend from just below entry price
        buy_offset = (i + 1) * GRID_CONFIG["buy_spacing_pct"]
        buy_price = entry_price * (1 - buy_offset / 2)

        # Sell price is buy price + pair spread
        spread = GRID_CONFIG["pair_spread_pcts"][i]
        sell_price = buy_price * (1 + spread)

        pairs.append({
            "id": i + 1,
            "buy": round(buy_price, 3),
            "sell": round(sell_price, 3),
            "spread_pct": spread,
            "size_usdc": GRID_CONFIG["size_usdc_per_pair"],
        })

    state.grid_pairs = pairs
    return pairs
```

### Example: Grid at $1.64 Entry

| Pair | Buy Price | Sell Price | Spread | USDC Size |
|------|-----------|------------|--------|-----------|
| 1 | $1.620 | $1.659 | 2.4% | $625 |
| 2 | $1.600 | $1.678 | 4.9% | $625 |
| 3 | $1.579 | $1.696 | 7.4% | $625 |
| 4 | $1.559 | $1.715 | 10.0% | $625 |
| 5 | $1.538 | $1.733 | 12.7% | $625 |
| 6 | $1.518 | $1.752 | 15.4% | $625 |
| 7 | $1.497 | $1.770 | 18.2% | $625 |
| 8 | $1.477 | $1.789 | 21.1% | $625 |

**Buy range:** $1.477 - $1.620 (all below entry)
**Sell range:** $1.659 - $1.789 (all above entry)

### Initial Order Placement

```python
def place_initial_grid_orders(entry_price):
    """Place all grid orders at startup."""
    for pair in state.grid_pairs:
        # Place buy order (always below entry price)
        place_limit_buy(
            pair_id=pair["id"],
            price=pair["buy"],
            usdc_amount=pair["size_usdc"]
        )

        # Place sell order with allocated grid LIT
        # LIT per pair = 5000 / 8 = 625 LIT
        lit_per_pair = 625
        place_limit_sell(
            pair_id=pair["id"],
            price=pair["sell"],
            lit_amount=lit_per_pair
        )

        state.grid_pairs_state[pair["id"]] = {
            "buy_active": True,
            "sell_active": True,
            "lit_held": 0,
        }
```

---

## Grid Cycling Logic

```python
PROFIT_RETENTION = 0.03  # Keep 3% of sell proceeds as profit

def on_buy_fill(pair_id, fill_price, usdc_spent):
    """Buy filled -> place corresponding sell."""
    pair = get_pair(pair_id)
    lit_acquired = usdc_spent / fill_price

    place_limit_sell(pair_id, pair["sell"], lit_acquired)
    state.grid_pairs_state[pair_id]["buy_active"] = False
    state.grid_pairs_state[pair_id]["sell_active"] = True
    state.grid_pairs_state[pair_id]["lit_held"] = lit_acquired

    log(f"Pair {pair_id}: Bought {lit_acquired:.1f} LIT @ ${fill_price:.3f}, placed sell @ ${pair['sell']:.3f}")

def on_sell_fill(pair_id, fill_price, lit_sold):
    """Sell filled -> place corresponding buy (minus profit retention)."""
    pair = get_pair(pair_id)
    usdc_received = lit_sold * fill_price
    usdc_for_rebuy = usdc_received * (1 - PROFIT_RETENTION)
    realized_profit = usdc_received * PROFIT_RETENTION

    place_limit_buy(pair_id, pair["buy"], usdc_for_rebuy)
    state.grid_pairs_state[pair_id]["sell_active"] = False
    state.grid_pairs_state[pair_id]["buy_active"] = True
    state.grid_pairs_state[pair_id]["lit_held"] = 0

    state.grid_pnl += realized_profit
    state.grid_cycles_completed += 1

    log(f"Pair {pair_id}: Sold {lit_sold:.1f} LIT @ ${fill_price:.3f}, profit ${realized_profit:.2f}, placed buy @ ${pair['buy']:.3f}")
```

### Expected Grid Performance (Realistic)

| Pair | Spread | Est. Cycles/Month | Profit/Cycle | Monthly |
|------|--------|-------------------|--------------|---------|
| 1-2 (tight) | 2-5% | 15-25 | $15-20 | $225-500 |
| 3-4 (medium) | 7-10% | 5-10 | $45-60 | $225-600 |
| 5-6 (wide) | 13-15% | 2-4 | $80-95 | $160-380 |
| 7-8 (widest) | 18-21% | 1-2 | $115-130 | $115-260 |

**Total expected monthly grid profit: $700-$1,740**

In high-volatility chop: $2,000-$2,800/month
In low volatility or trending: $400-$800/month

---

## Core Position — Limit Orders at Startup

### 8,000 LIT (Never Grid)

At startup, place limit sell orders for all upside targets. These orders sit on the book until filled.

| Target | LIT to Sell | Proceeds | Remaining Core |
|--------|-------------|----------|----------------|
| $2.50 | 1,000 | $2,500 | 7,000 |
| $3.00 | 1,500 | $4,500 | 5,500 |
| $3.50 | 2,000 | $7,000 | 3,500 |
| $4.00 | 2,000 | $8,000 | 1,500 |
| $4.50 | 1,500 | $6,750 | 0 |

**Total potential core proceeds: $28,750**

### Implementation

```python
CORE_TARGETS = [
    {"price": 2.50, "lit": 1000},
    {"price": 3.00, "lit": 1500},
    {"price": 3.50, "lit": 2000},
    {"price": 4.00, "lit": 2000},
    {"price": 4.50, "lit": 1500},
]

def place_core_sell_orders():
    """Place all core sell limit orders at startup."""
    for target in CORE_TARGETS:
        order_id = place_limit_sell(
            price=target["price"],
            lit_amount=target["lit"],
            label="core"
        )
        target["order_id"] = order_id
        target["executed"] = False
        log(f"Placed core sell: {target['lit']} LIT @ ${target['price']:.2f}")

    state.core_orders = CORE_TARGETS

def on_core_sell_fill(order_id, fill_price, lit_sold):
    """Handle core sell limit order fill."""
    for target in state.core_orders:
        if target["order_id"] == order_id:
            target["executed"] = True
            state.core_lit_remaining -= lit_sold
            usdc_received = lit_sold * fill_price
            state.spot_usdc += usdc_received
            log(f"Core sell filled: {lit_sold} LIT @ ${fill_price:.2f}, received ${usdc_received:.2f}")
            return
```

### Why Limit Orders (Option A)

- **Simplicity**: Set and forget — no monitoring logic needed
- **Guaranteed price**: Fills at exactly the target price (or better)
- **No slippage**: Unlike market orders triggered by price monitoring
- **Exchange handles execution**: Works even if bot is temporarily down

---

## Hedge Strategy — Dynamic Management

### Position Sizing

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Short size | 3,000 LIT | Reduces net exposure from 17,175 to 14,175 |
| Leverage | 4x | Buffer below 5x max, liquidation at ~$2.08 |
| Margin | $1,260 required ($1,500 allocated) | $240 buffer |

### Portfolio Math with Hedge

```
Portfolio(P) = 17,175 × P + $7,300 + (entry - P) × 3,000
             = 14,175 × P + $7,300 + entry × 3,000
```

At entry $1.64:
```
Portfolio(P) = 14,175P + 7,300 + 4,920 = 14,175P + 12,220
```

| Price | Without Hedge | With Hedge | Benefit |
|-------|---------------|------------|---------|
| $1.64 | $35,467 | $35,467 | $0 |
| $1.50 | $33,063 | $33,483 | +$420 |
| $1.30 | $29,628 | $30,648 | +$1,020 |
| $1.10 | $26,193 | $27,813 | +$1,620 |
| $0.90 | $22,758 | $24,978 | +$2,220 |

**Floor breach (at $1.64 entry):**
- Without hedge: $1.03
- With hedge: $0.90
- Benefit: 13% more downside room

### Dynamic Stop-Loss & Re-Entry

```python
HEDGE_CONFIG = {
    "short_size": 3000,
    "leverage": 4,
    "margin_usdc": 1500,
    "stop_loss_pct": 0.16,            # 16% above entry
    "cooldown_hours": 24,
    "reentry_pullback_pct": 0.05,     # 5% pullback from recent high
    "pause_negative_funding_hours": 24,
}

class HedgeManager:
    def __init__(self, entry_price):
        self.bot_entry_price = entry_price  # Bot startup price (for re-entry reference)
        self.active = False
        self.hedge_entry_price = 0          # Price when hedge was opened
        self.stop_price = 0
        self.last_stop_time = None
        self.recent_high = entry_price

    def update(self, current_price, funding_rate):
        self.recent_high = max(self.recent_high, current_price)

        if self.active:
            self._manage_active_hedge(current_price, funding_rate)
        else:
            self._check_reentry(current_price)

    def _manage_active_hedge(self, price, funding_rate):
        # Check stop-loss
        if price >= self.stop_price:
            self._close_hedge(price, reason="stop_loss")
            return

        # Check negative funding
        if funding_rate < 0 and negative_funding_duration() > HEDGE_CONFIG["pause_negative_funding_hours"]:
            self._close_hedge(price, reason="negative_funding")
            return

        # Trail stop down if price drops significantly (lock in gains)
        if price < self.hedge_entry_price * 0.90:
            new_stop = price * (1 + HEDGE_CONFIG["stop_loss_pct"])
            if new_stop < self.stop_price:
                self.stop_price = new_stop
                log(f"Trailed stop to ${new_stop:.3f}")

    def _check_reentry(self, price):
        if not self._cooldown_elapsed():
            return

        # Re-enter if price at or below bot entry price
        if price <= self.bot_entry_price:
            self._open_hedge(price)
            return

        # Re-enter if price pulled back 5% from recent high
        pullback_threshold = self.recent_high * (1 - HEDGE_CONFIG["reentry_pullback_pct"])
        if price <= pullback_threshold:
            self._open_hedge(price)
            self.recent_high = price  # Reset high after re-entry

    def _open_hedge(self, price):
        open_short(HEDGE_CONFIG["short_size"], price)
        self.active = True
        self.hedge_entry_price = price
        self.stop_price = price * (1 + HEDGE_CONFIG["stop_loss_pct"])
        log(f"Opened hedge: {HEDGE_CONFIG['short_size']} LIT short @ ${price:.3f}, stop @ ${self.stop_price:.3f}")

    def _close_hedge(self, price, reason):
        close_short()
        pnl = (self.hedge_entry_price - price) * HEDGE_CONFIG["short_size"]
        self.active = False
        self.last_stop_time = now()
        state.hedge_pnl += pnl
        log(f"Closed hedge ({reason}): PnL ${pnl:.2f}")

    def _cooldown_elapsed(self):
        if self.last_stop_time is None:
            return True
        hours = (now() - self.last_stop_time).total_seconds() / 3600
        return hours >= HEDGE_CONFIG["cooldown_hours"]
```

### Hedge Scenarios (at $1.64 entry)

**Scenario A: Pump -> Stop -> Crash (hedge saves you)**
```
$1.64 -> $1.90: stopped (16% above $1.64), loss = -$780
$1.90 -> $2.40: waiting, tracking high = $2.40
$2.40 -> $2.28: 5% pullback, re-enter @ $2.28, stop @ $2.64
$2.28 -> $1.30: hedge gains = +$2,940
Net hedge PnL: +$2,160
```

**Scenario B: Pump -> Stop -> Pump more (hedge cost is insurance)**
```
$1.64 -> $1.90: stopped, loss = -$780
$1.90 -> $3.00: waiting, tracking high = $3.00
$3.00 -> $2.85: 5% pullback, re-enter @ $2.85, stop @ $3.31
$2.85 -> $3.50: stopped, loss = -$1,950
Total hedge cost: -$2,730

But spot gained: 17,175 × ($3.50 - $1.64) = +$31,946
Net: +$29,216
```

**Scenario C: Slow grind down (hedge works perfectly)**
```
$1.64 -> $1.30: hedge gains = +$1,020, trail stop to $1.51
$1.30 -> $1.00: hedge gains = +$1,920 total, trail stop to $1.16
Never stopped. Continuous protection.
```

---

## Floor Protection System

### The Math

Floor protection triggers are based on absolute prices, not relative to entry. This ensures consistent behavior regardless of when the bot started.

```
With hedge active at entry E:
Portfolio(P) = 14,175P + 7,300 + (E - P) × 3,000
             = 14,175P + 7,300 + 3,000E - 3,000P
             = 11,175P + 7,300 + 3,000E

At E = $1.64:
Portfolio(P) = 11,175P + 7,300 + 4,920 = 11,175P + 12,220

Floor breach: 11,175P + 12,220 = 25,000
P = $1.14
```

Wait — let me recalculate. With hedge:
- Net LIT exposure: 17,175 - 3,000 = 14,175 LIT
- USDC: $7,300 (but $1,500 is margin, so liquid = $5,800)
- Hedge PnL at price P: (entry - P) × 3,000

```
Portfolio(P) = 14,175 × P + 5,800 + 1,500 + (entry - P) × 3,000
            = 14,175P + 7,300 + 3,000 × entry - 3,000P
            = 11,175P + 7,300 + 3,000 × entry
```

Hmm, that's different. Let me be precise:

**Correct calculation:**
- Spot LIT value: 17,175 × P
- Spot USDC: $5,800 (after margin)
- Perp margin: $1,500 (locked)
- Hedge PnL: (entry - P) × 3,000

```
Total = 17,175P + 5,800 + 1,500 + (entry - P) × 3,000
      = 17,175P + 7,300 + 3,000 × entry - 3,000P
      = 14,175P + 7,300 + 3,000 × entry
```

At entry = $1.64:
```
Portfolio(P) = 14,175P + 7,300 + 4,920 = 14,175P + 12,220
```

Floor breach: 14,175P + 12,220 = 25,000 → P = $0.90

### Tiered De-Risk Protocol

| Trigger | Action | Approx Portfolio | Notes |
|---------|--------|------------------|-------|
| $1.40 | Alert + pause grid buys | ~$32,065 | Early warning |
| $1.30 | Sell 2,000 reserve LIT | ~$30,648 | Convert to USDC |
| $1.20 | Sell 2,175 reserve LIT | ~$29,230 | More conversion |
| $1.10 | Cancel grid, sell grid LIT | ~$27,813 | Exit grid positions |
| $1.00 | Close hedge, sell to floor | ~$26,395 | Emergency exit |

### Implementation

```python
FLOOR_CONFIG = {
    "floor_value": 25000,
    "emergency_buffer": 1500,  # Start emergency at $26.5k portfolio
    "tiers": [
        {"price": 1.40, "action": "pause_grid_buys", "executed": False},
        {"price": 1.30, "action": "sell_reserve", "amount": 2000, "executed": False},
        {"price": 1.20, "action": "sell_reserve", "amount": 2175, "executed": False},
        {"price": 1.10, "action": "cancel_grid_sell_all_grid_lit", "executed": False},
        {"price": 1.00, "action": "emergency_exit", "executed": False},
    ]
}

def check_floor_protection(price):
    portfolio = calculate_portfolio_value(price)

    # Emergency exit if approaching floor
    if portfolio <= FLOOR_CONFIG["floor_value"] + FLOOR_CONFIG["emergency_buffer"]:
        execute_emergency_exit()
        return

    # Execute tiers (only once each)
    for tier in FLOOR_CONFIG["tiers"]:
        if price <= tier["price"] and not tier["executed"]:
            execute_tier(tier)
            tier["executed"] = True

def execute_tier(tier):
    action = tier["action"]

    if action == "pause_grid_buys":
        cancel_all_grid_buy_orders()
        send_alert(f"Floor warning: Grid buys paused at ${tier['price']}")

    elif action == "sell_reserve":
        market_sell(tier["amount"])
        state.reserve_lit_remaining -= tier["amount"]
        log(f"Floor tier: Sold {tier['amount']} reserve LIT")

    elif action == "cancel_grid_sell_all_grid_lit":
        cancel_all_grid_orders()
        lit_in_grid = sum(p["lit_held"] for p in state.grid_pairs_state.values())
        if lit_in_grid > 0:
            market_sell(lit_in_grid)
        log(f"Floor tier: Cancelled grid, sold {lit_in_grid} LIT")

    elif action == "emergency_exit":
        execute_emergency_exit()

def execute_emergency_exit():
    """Nuclear option: convert everything to USDC to preserve floor."""
    close_all_perp_positions()
    cancel_all_orders()
    sell_all_lit_at_market()
    halt_bot("Floor protection triggered - all positions closed")
```

---

## Expected Performance Summary

### Monthly Projections

| Component | Bear | Base | Bull |
|-----------|------|------|------|
| Grid profit | $400 | $1,200 | $2,500 |
| Funding yield | $35 | $100 | $175 |
| Hedge PnL | +$500 | -$200 | -$800 |
| **Monthly total** | **$935** | **$1,100** | **$1,875** |

### Path to $75k

| Scenario | Timeline | Price Required | Achievable? |
|----------|----------|----------------|-------------|
| Hold only | - | $3.94 | If LIT recovers |
| 6mo chop + recovery | 6 months | $3.20-$3.50 | Likely |
| 12mo chop + recovery | 12 months | $2.80-$3.00 | Very likely |
| Extended bear | - | $4.50+ | Difficult |

---

## Complete Configuration

```python
CONFIG = {
    "allocation": {
        "core_lit": 8000,
        "grid_lit": 5000,
        "reserve_lit": 4175,
        "grid_usdc": 5000,
        "hedge_margin_usdc": 1500,
        "cash_reserve_usdc": 800,
    },

    "grid": {
        "num_pairs": 8,
        "size_usdc_per_pair": 625,
        "size_lit_per_pair": 625,
        "buy_spacing_pct": 0.025,
        "pair_spread_pcts": [0.024, 0.049, 0.074, 0.100, 0.127, 0.154, 0.182, 0.211],
        "profit_retention": 0.03,
    },

    "hedge": {
        "enabled": True,
        "short_size": 3000,
        "leverage": 4,
        "margin_usdc": 1500,
        "stop_loss_pct": 0.16,
        "cooldown_hours": 24,
        "reentry_pullback_pct": 0.05,
        "pause_negative_funding_hours": 24,
    },

    "floor_protection": {
        "floor_value": 25000,
        "emergency_buffer": 1500,
        "tiers": [
            {"price": 1.40, "action": "pause_grid_buys"},
            {"price": 1.30, "action": "sell_reserve", "amount": 2000},
            {"price": 1.20, "action": "sell_reserve", "amount": 2175},
            {"price": 1.10, "action": "cancel_grid_sell_all_grid_lit"},
            {"price": 1.00, "action": "emergency_exit"},
        ],
    },

    "core_targets": [
        {"price": 2.50, "lit": 1000},
        {"price": 3.00, "lit": 1500},
        {"price": 3.50, "lit": 2000},
        {"price": 4.00, "lit": 2000},
        {"price": 4.50, "lit": 1500},
    ],

    "limits": {
        "max_lit": 22000,
        "min_lit": 8000,
        "max_short": 3000,
        "min_usdc": 800,
    },
}
```

---

## State Tracking

```python
state = {
    # Entry reference
    "entry_price": 0,           # Set at startup
    "startup_time": None,

    # Balances
    "spot_lit": 17175,
    "spot_usdc": 5800,
    "perp_margin": 1500,

    # Hedge
    "hedge_active": False,
    "hedge_entry_price": 0,
    "hedge_stop_price": 0,
    "hedge_last_stop_time": None,
    "hedge_recent_high": 0,
    "hedge_pnl_realized": 0,

    # Grid
    "grid_pairs": [],           # Generated at startup
    "grid_pairs_state": {},     # {pair_id: {buy_active, sell_active, lit_held}}
    "grid_pnl": 0,
    "grid_cycles_completed": 0,

    # Core
    "core_orders": [],          # Limit orders placed at startup
    "core_lit_remaining": 8000,

    # Floor protection
    "floor_tiers_executed": [],
    "reserve_lit_remaining": 4175,

    # Funding
    "funding_pnl": 0,
}
```

---

## Startup Sequence

```python
def main():
    # 1. Get current price
    entry_price = get_current_market_price()
    state.entry_price = entry_price
    state.startup_time = now()
    log(f"Starting bot at ${entry_price:.3f}")

    # 2. Generate and place grid orders
    generate_grid_levels(entry_price)
    place_initial_grid_orders(entry_price)
    log(f"Placed {len(state.grid_pairs)} grid pairs")

    # 3. Place core sell limit orders
    place_core_sell_orders()
    log(f"Placed {len(CORE_TARGETS)} core sell orders")

    # 4. Open initial hedge
    hedge_manager = HedgeManager(entry_price)
    hedge_manager._open_hedge(entry_price)
    log(f"Opened hedge: 3,000 LIT short @ ${entry_price:.3f}")

    # 5. Start main loop
    while True:
        current_price = get_current_market_price()
        funding_rate = get_funding_rate()

        # Update hedge
        hedge_manager.update(current_price, funding_rate)

        # Check floor protection
        check_floor_protection(current_price)

        # Process any fills (via websocket callbacks)
        process_fills()

        sleep(1)  # Or use websocket events
```

---

## Terminal Output & Monitoring

### Dashboard Display (Refreshes Every 5 Seconds)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  LIT GRID BOT v2.0                                      2025-01-19 14:32:05 │
├─────────────────────────────────────────────────────────────────────────────┤
│  PRICE: $1.6420 (+2.3%)    ENTRY: $1.6050    RUNTIME: 3d 14h 22m            │
├─────────────────────────────────────────────────────────────────────────────┤
│  PORTFOLIO                          │  P&L SUMMARY                          │
│  ─────────────────────────────────  │  ────────────────────────────────     │
│  Spot LIT:     17,175  ($28,209)    │  Grid P&L:        +$847.32            │
│  Spot USDC:     $5,800              │  Funding P&L:      +$42.18            │
│  Perp Margin:   $1,500              │  Hedge P&L:       -$124.00            │
│  Hedge PnL:      -$124              │  ─────────────────────────────────    │
│  ─────────────────────────────────  │  TOTAL P&L:       +$765.50            │
│  TOTAL:        $35,385              │  Daily Avg:       +$217.29            │
│  vs Floor:     +$10,385 (41%)       │                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│  HEDGE STATUS                                                               │
│  ─────────────────────────────────────────────────────────────────────────  │
│  [ACTIVE] 3,000 LIT SHORT @ $1.6050  │  Stop: $1.862 (13.5% away)           │
│  Unrealized PnL: -$124.00            │  Funding Rate: +0.0082%/hr (30% APY) │
├─────────────────────────────────────────────────────────────────────────────┤
│  GRID STATUS                                                8/8 pairs active│
│  ─────────────────────────────────────────────────────────────────────────  │
│  Pair │ Buy Price │ Sell Price │ Spread │ Status      │ Cycles │ Profit    │
│  ─────┼───────────┼────────────┼────────┼─────────────┼────────┼───────────│
│    1  │   $1.600  │    $1.639  │   2.4% │ BUY  active │     12 │  +$223.44 │
│    2  │   $1.580  │    $1.658  │   4.9% │ SELL active │      8 │  +$186.72 │
│    3  │   $1.559  │    $1.676  │   7.4% │ BUY  active │      5 │  +$142.80 │
│    4  │   $1.539  │    $1.695  │  10.0% │ BUY  active │      3 │  +$112.50 │
│    5  │   $1.518  │    $1.713  │  12.7% │ SELL active │      2 │   +$89.36 │
│    6  │   $1.498  │    $1.732  │  15.4% │ BUY  active │      1 │   +$52.00 │
│    7  │   $1.477  │    $1.750  │  18.2% │ BUY  active │      1 │   +$40.50 │
│    8  │   $1.457  │    $1.769  │  21.1% │ BUY  active │      0 │     $0.00 │
│  ─────┴───────────┴────────────┴────────┴─────────────┴────────┴───────────│
│  Total Cycles: 32                                    Grid Profit: +$847.32 │
├─────────────────────────────────────────────────────────────────────────────┤
│  CORE POSITION                                           8,000 LIT allocated│
│  ─────────────────────────────────────────────────────────────────────────  │
│  Target │  LIT  │ Status     │ Proceeds │  $2.50 ████████████░░░░░░░░ 52%   │
│  ───────┼───────┼────────────┼──────────│                                   │
│  $2.50  │ 1,000 │ ⏳ Pending │       -  │  Price must rise 52% to first     │
│  $3.00  │ 1,500 │ ⏳ Pending │       -  │  target. All limit orders active. │
│  $3.50  │ 2,000 │ ⏳ Pending │       -  │                                   │
│  $4.00  │ 2,000 │ ⏳ Pending │       -  │                                   │
│  $4.50  │ 1,500 │ ⏳ Pending │       -  │                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│  FLOOR PROTECTION                                         All tiers armed   │
│  ─────────────────────────────────────────────────────────────────────────  │
│  $1.40 ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ Pause buys  (17% away)│
│  $1.30 ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ Sell 2k LIT (21% away)│
│  $1.20 ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ Sell 2.2k   (27% away)│
│  $1.10 ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ Exit grid   (33% away)│
│  $1.00 ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ EMERGENCY   (39% away)│
└─────────────────────────────────────────────────────────────────────────────┘
```

### Event Log (Scrolling Below Dashboard)

```
[14:32:01] GRID     Pair 2 BUY filled: 390.6 LIT @ $1.5998 ($625.00)
[14:32:01] GRID     Pair 2 SELL placed: 390.6 LIT @ $1.6577
[14:31:45] PRICE    $1.6420 → $1.6398 (-0.13%)
[14:28:12] FUNDING  Received $0.42 funding payment (rate: +0.0082%/hr)
[14:25:33] GRID     Pair 1 SELL filled: 385.2 LIT @ $1.6388 → +$22.87 profit
[14:25:33] GRID     Pair 1 BUY placed: $606.44 @ $1.6000
[14:22:01] HEDGE    Trailing stop updated: $1.892 → $1.862
[14:15:00] SYSTEM   Heartbeat OK | API latency: 42ms | WS connected
[13:58:44] GRID     Pair 3 BUY filled: 401.2 LIT @ $1.5588 ($625.00)
[13:58:44] GRID     Pair 3 SELL placed: 401.2 LIT @ $1.6763
```

### Implementation

```python
import os
import sys
from datetime import datetime, timedelta
from colorama import Fore, Back, Style, init

init()  # Initialize colorama

class TerminalDisplay:
    def __init__(self):
        self.event_log = []  # Rolling log of recent events
        self.max_log_lines = 15

    def clear_screen(self):
        os.system('cls' if os.name == 'nt' else 'clear')

    def render(self, state, current_price, funding_rate):
        """Render the full dashboard."""
        self.clear_screen()

        lines = []
        lines.extend(self._header(state, current_price))
        lines.extend(self._portfolio_section(state, current_price))
        lines.extend(self._hedge_section(state, current_price, funding_rate))
        lines.extend(self._grid_section(state))
        lines.extend(self._core_section(state, current_price))
        lines.extend(self._floor_section(state, current_price))
        lines.extend(self._event_log_section())

        print('\n'.join(lines))

    def _header(self, state, current_price):
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        runtime = self._format_runtime(state.startup_time)
        price_change = ((current_price / state.entry_price) - 1) * 100
        price_color = Fore.GREEN if price_change >= 0 else Fore.RED

        return [
            f"┌{'─' * 77}┐",
            f"│  {Fore.CYAN}LIT GRID BOT v2.0{Style.RESET_ALL}                                      {now} │",
            f"├{'─' * 77}┤",
            f"│  PRICE: {price_color}${current_price:.4f} ({price_change:+.1f}%){Style.RESET_ALL}    "
            f"ENTRY: ${state.entry_price:.4f}    RUNTIME: {runtime}            │",
            f"├{'─' * 77}┤",
        ]

    def _portfolio_section(self, state, current_price):
        spot_value = state.spot_lit * current_price
        total = spot_value + state.spot_usdc + state.perp_margin + state.hedge_pnl_realized
        vs_floor = total - 25000
        vs_floor_pct = (vs_floor / 25000) * 100

        total_pnl = state.grid_pnl + state.funding_pnl + state.hedge_pnl_realized
        days = max(1, (datetime.now() - state.startup_time).days)
        daily_avg = total_pnl / days

        floor_color = Fore.GREEN if vs_floor > 5000 else (Fore.YELLOW if vs_floor > 2000 else Fore.RED)

        return [
            f"│  {'PORTFOLIO':<35} │  {'P&L SUMMARY':<37} │",
            f"│  {'─' * 33}  │  {'─' * 36}    │",
            f"│  Spot LIT:   {state.spot_lit:>7,}  (${spot_value:>9,.0f})    │  Grid P&L:      {Fore.GREEN}+${state.grid_pnl:>10,.2f}{Style.RESET_ALL}            │",
            f"│  Spot USDC:    ${state.spot_usdc:>7,.0f}              │  Funding P&L:    {Fore.GREEN}+${state.funding_pnl:>9,.2f}{Style.RESET_ALL}            │",
            f"│  Perp Margin:  ${state.perp_margin:>7,.0f}              │  Hedge P&L:     {self._color_pnl(state.hedge_pnl_realized):>20}            │",
            f"│  {'─' * 33}  │  {'─' * 36}    │",
            f"│  {Fore.WHITE}{Style.BRIGHT}TOTAL:        ${total:>8,.0f}{Style.RESET_ALL}              │  {Style.BRIGHT}TOTAL P&L:      {self._color_pnl(total_pnl):>20}{Style.RESET_ALL}            │",
            f"│  vs Floor:    {floor_color}+${vs_floor:>7,.0f} ({vs_floor_pct:.0f}%){Style.RESET_ALL}       │  Daily Avg:      {self._color_pnl(daily_avg):>20}            │",
            f"├{'─' * 77}┤",
        ]

    def _hedge_section(self, state, current_price, funding_rate):
        if state.hedge_active:
            status = f"{Fore.GREEN}[ACTIVE]{Style.RESET_ALL}"
            entry = f"3,000 LIT SHORT @ ${state.hedge_entry_price:.4f}"
            stop_dist = ((state.hedge_stop_price / current_price) - 1) * 100
            stop_info = f"Stop: ${state.hedge_stop_price:.3f} ({stop_dist:.1f}% away)"
            unrealized = (state.hedge_entry_price - current_price) * 3000
            unrealized_str = f"Unrealized PnL: {self._color_pnl(unrealized)}"
        else:
            status = f"{Fore.YELLOW}[INACTIVE]{Style.RESET_ALL}"
            entry = "Waiting for re-entry conditions"
            stop_info = ""
            unrealized_str = f"Last stop: {state.hedge_last_stop_time or 'N/A'}"

        funding_apy = funding_rate * 24 * 365 * 100
        funding_str = f"Funding Rate: {'+' if funding_rate >= 0 else ''}{funding_rate*100:.4f}%/hr ({funding_apy:.0f}% APY)"

        return [
            f"│  {'HEDGE STATUS':<75} │",
            f"│  {'─' * 75}  │",
            f"│  {status} {entry:<30} │  {stop_info:<35} │",
            f"│  {unrealized_str:<36} │  {funding_str:<35} │",
            f"├{'─' * 77}┤",
        ]

    def _grid_section(self, state):
        active_count = sum(1 for p in state.grid_pairs_state.values()
                         if p.get('buy_active') or p.get('sell_active'))

        lines = [
            f"│  {'GRID STATUS':<60} {active_count}/8 pairs active│",
            f"│  {'─' * 75}  │",
            f"│  {'Pair':<4} │ {'Buy Price':<9} │ {'Sell Price':<10} │ {'Spread':<6} │ {'Status':<11} │ {'Cycles':<6} │ {'Profit':<9} │",
            f"│  {'─' * 4}─┼─{'─' * 9}─┼─{'─' * 10}─┼─{'─' * 6}─┼─{'─' * 11}─┼─{'─' * 6}─┼─{'─' * 9}─│",
        ]

        total_cycles = 0
        total_profit = 0

        for pair in state.grid_pairs:
            pair_state = state.grid_pairs_state.get(pair['id'], {})
            cycles = pair_state.get('cycles', 0)
            profit = pair_state.get('profit', 0)
            total_cycles += cycles
            total_profit += profit

            if pair_state.get('sell_active'):
                status = f"{Fore.CYAN}SELL active{Style.RESET_ALL}"
            elif pair_state.get('buy_active'):
                status = f"{Fore.GREEN}BUY  active{Style.RESET_ALL}"
            else:
                status = f"{Fore.YELLOW}PENDING{Style.RESET_ALL}"

            lines.append(
                f"│  {pair['id']:>4}  │  ${pair['buy']:<7.3f} │   ${pair['sell']:<7.3f} │ {pair['spread_pct']*100:>5.1f}% │ {status:<20} │ {cycles:>6} │ {'+' if profit >= 0 else ''}{profit:>8.2f} │"
            )

        lines.extend([
            f"│  {'─' * 4}─┴─{'─' * 9}─┴─{'─' * 10}─┴─{'─' * 6}─┴─{'─' * 11}─┴─{'─' * 6}─┴─{'─' * 9}─│",
            f"│  Total Cycles: {total_cycles:<50} Grid Profit: {Fore.GREEN}+${total_profit:>8.2f}{Style.RESET_ALL} │",
            f"├{'─' * 77}┤",
        ])

        return lines

    def _core_section(self, state, current_price):
        first_target = 2.50
        pct_to_target = ((first_target / current_price) - 1) * 100
        progress = min(100, max(0, (current_price / first_target) * 100))
        bar = '█' * int(progress / 5) + '░' * (20 - int(progress / 5))

        lines = [
            f"│  {'CORE POSITION':<58} 8,000 LIT allocated│",
            f"│  {'─' * 75}  │",
            f"│  {'Target':<6} │ {'LIT':<5} │ {'Status':<10} │ {'Proceeds':<8} │  $2.50 {bar} {progress:.0f}%   │",
            f"│  {'─' * 6}─┼─{'─' * 5}─┼─{'─' * 10}─┼─{'─' * 8}─│                                   │",
        ]

        for target in state.core_orders:
            status = f"{Fore.GREEN}✓ Filled{Style.RESET_ALL}" if target.get('executed') else f"{Fore.YELLOW}⏳ Pending{Style.RESET_ALL}"
            proceeds = f"${target['lit'] * target['price']:,.0f}" if target.get('executed') else "-"
            lines.append(
                f"│  ${target['price']:<5.2f} │ {target['lit']:>5,} │ {status:<19} │ {proceeds:>8} │  Price must rise {pct_to_target:.0f}% to first     │"
            )
            pct_to_target = ((target['price'] / current_price) - 1) * 100  # Update for next iteration display

        lines.append(f"├{'─' * 77}┤")
        return lines

    def _floor_section(self, state, current_price):
        lines = [
            f"│  {'FLOOR PROTECTION':<56} All tiers armed   │",
            f"│  {'─' * 75}  │",
        ]

        tiers = [
            (1.40, "Pause buys"),
            (1.30, "Sell 2k LIT"),
            (1.20, "Sell 2.2k"),
            (1.10, "Exit grid"),
            (1.00, "EMERGENCY"),
        ]

        for price, action in tiers:
            pct_away = ((current_price / price) - 1) * 100
            executed = price in state.floor_tiers_executed

            if executed:
                bar = f"{Fore.RED}{'█' * 45}{Style.RESET_ALL}"
                status = f"{Fore.RED}TRIGGERED{Style.RESET_ALL}"
            else:
                bar = '░' * 45
                status = f"{action:<11} ({pct_away:.0f}% away)"

            lines.append(f"│  ${price:.2f} {bar} {status:<20}│")

        lines.append(f"└{'─' * 77}┘")
        return lines

    def _event_log_section(self):
        lines = [
            "",
            f"{Fore.WHITE}{Style.BRIGHT}EVENT LOG{Style.RESET_ALL}",
            "─" * 79,
        ]

        for event in self.event_log[-self.max_log_lines:]:
            lines.append(event)

        return lines

    def log_event(self, category, message):
        """Add event to rolling log."""
        timestamp = datetime.now().strftime('%H:%M:%S')

        colors = {
            'GRID': Fore.CYAN,
            'HEDGE': Fore.MAGENTA,
            'PRICE': Fore.WHITE,
            'FUNDING': Fore.GREEN,
            'FLOOR': Fore.RED,
            'SYSTEM': Fore.YELLOW,
            'CORE': Fore.BLUE,
        }

        color = colors.get(category, Fore.WHITE)
        formatted = f"[{timestamp}] {color}{category:<8}{Style.RESET_ALL} {message}"

        self.event_log.append(formatted)
        if len(self.event_log) > 100:  # Keep last 100 events
            self.event_log = self.event_log[-100:]

    def _color_pnl(self, value):
        """Color P&L values green/red."""
        if value >= 0:
            return f"{Fore.GREEN}+${value:,.2f}{Style.RESET_ALL}"
        else:
            return f"{Fore.RED}-${abs(value):,.2f}{Style.RESET_ALL}"

    def _format_runtime(self, start_time):
        """Format runtime as Xd Xh Xm."""
        if not start_time:
            return "0d 0h 0m"
        delta = datetime.now() - start_time
        days = delta.days
        hours = delta.seconds // 3600
        minutes = (delta.seconds % 3600) // 60
        return f"{days}d {hours}h {minutes}m"


# Usage in main loop
display = TerminalDisplay()

def main_loop():
    while True:
        current_price = get_current_market_price()
        funding_rate = get_funding_rate()

        # Update display every 5 seconds
        display.render(state, current_price, funding_rate)

        # Process events and log them
        for event in process_events():
            display.log_event(event.category, event.message)

        time.sleep(5)
```

### Event Categories

| Category | Color | Events |
|----------|-------|--------|
| `GRID` | Cyan | Buy/sell fills, order placements, cycle completions |
| `HEDGE` | Magenta | Open, close, stop-loss, re-entry, trailing stop updates |
| `PRICE` | White | Significant price moves (>0.5%) |
| `FUNDING` | Green | Funding payments received |
| `FLOOR` | Red | Tier triggers, warnings |
| `SYSTEM` | Yellow | Heartbeats, connection status, errors |
| `CORE` | Blue | Core sell order fills |

### Minimal Mode (For Logs/Background)

```python
def log_minimal(event):
    """Simple one-line logging for non-interactive mode."""
    timestamp = datetime.now().isoformat()
    print(f"{timestamp} | {event.category:8} | {event.message}")

# Example output:
# 2025-01-19T14:32:01 | GRID     | Pair 2 BUY filled: 390.6 LIT @ $1.5998
# 2025-01-19T14:32:01 | GRID     | Pair 2 SELL placed: 390.6 LIT @ $1.6577
# 2025-01-19T14:28:12 | FUNDING  | Received $0.42 (rate: +0.0082%/hr)
```

---

## Daily Operations Checklist

1. [ ] Verify bot is running and connected
2. [ ] Check current price vs grid range
3. [ ] Verify all expected grid orders are active
4. [ ] Check hedge status:
   - If active: current price vs stop price
   - If inactive: check re-entry conditions
5. [ ] Check funding rate — note if negative
6. [ ] Calculate portfolio value vs floor ($25k)
7. [ ] Review completed cycles and daily PnL
8. [ ] Check for stuck/failed orders
9. [ ] Verify core sell orders are still on book
10. [ ] Check state persistence / logs

---

## Key Numbers Summary

| Metric | Value |
|--------|-------|
| Starting value | ~$35,500 (at $1.64) |
| Hard floor | $25,000 |
| Target exit | $75,000 |
| Grid capital | $10,000 (5k LIT + $5k USDC) |
| Grid range | Dynamic, ~10% below to ~10% above entry |
| Expected grid yield | $700-$2,800/month |
| Hedge size | 3,000 LIT short @ 4x |
| Hedge stop | 16% above hedge entry (dynamic) |
| Hedge re-entry | At/below bot entry OR 5% pullback from high |
| Floor breach (hedged) | ~$0.90 |
| Net exposure | 14,175 LIT |
| Core sells | 5 limit orders at $2.50-$4.50 |
