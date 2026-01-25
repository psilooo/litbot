## Context Summary for Claude Code: LIT Grid Trading Bot

---

## Background

### Token: Lighter ($LIT)
- Perpetual DEX on Ethereum L2 using ZK-SNARKs
- Launched December 30, 2025 at ~$3.37, ATH $4.04
- Current price: $1.68 (down ~50% from ATH)
- FDV: ~$1.68B (1B token supply)
- Zero trading fees for retail on Lighter platform
- Backed by a16z, Founders Fund, Robinhood, Haun Ventures ($68M raised at $1.5B valuation)

### Platform Characteristics
- Zero trading fees (spot and perp)
- Hourly funding rate (not 8-hour like most exchanges)
- Funding typically positive: 10-50% annualized (longs pay shorts)
- High volatility: ±0.5% average hourly candle

### Bull Thesis
- David Sacks connection (Trump's AI/Crypto czar)
- Vlad-Vlad childhood friendship (Lighter CEO and Robinhood CEO)
- US regulatory tailwinds for compliant infrastructure
- ZK-proof verification of order matching (technical moat vs Hyperliquid)
- Potential Robinhood integration pipeline

### Fair Value Estimate
- Probability-weighted expected value: ~$4.20/token
- Bear case: $0.80 (15% probability) - volume collapses
- Base case: $2.50 (40% probability) - retains 30% volume
- Bull case: $6.00 (30% probability) - Robinhood integration
- Moon case: $15.00 (10% probability) - becomes THE US-regulated perp DEX

---

## Current Position

Position varies based on grid fills and market price. Check bot output for live values.

---

## Goals & Constraints

| Constraint | Value | Notes |
|------------|-------|-------|
| Hard floor | $25,000 | Cannot breach at any point |
| Target exit | $75,000 | ~$3.94 LIT price with hold |
| Net exposure | Always long | Matches bull thesis |
| Max drawdown | -$11,154 | Buffer from current to floor |

---

## Strategy: Grid Trading + Upside Capture

### Why This Strategy

1. **High volatility (±0.5%/hr)** = excellent for 2% grid spreads
2. **Zero fees** = pure spread capture
3. **$25k floor requirement** = value-based protection
4. **Bull thesis** = maintain long exposure for upside

### Two Profit Engines

1. **Spot Grid** - Extract volatility through buy/sell cycling (400 LIT × 2% = ~$8/cycle)
2. **Core Position** - Upside capture at $2.00-$4.50 sell targets

---

## Capital Allocation

| Category | LIT | USDC | Purpose |
|----------|-----|------|---------|
| Core (sell targets) | 11,175 | - | Upside capture at $2.00-$4.50 |
| Grid Sell | 5,000 | - | Active grid cycling |
| Reserve (moonbag) | 1,000 | - | Never sold |
| Grid Buy | - | $6,500 | Active grid cycling |
| Hedge Margin | - | $0 | Disabled |
| Cash Reserve | - | $800 | Emergency buffer |

---

## Spot Grid (Fixed-Level Cycling)

### Design Rationale
- ±0.5%/hr volatility supports 2% spreads
- Fixed 400 LIT per order (both buys and sells)
- 2% level spacing = 2% cycle spread (prevents drift)
- Counter-orders snap to predefined grid levels
- Zero fees = pure spread capture

### Grid Configuration

| Parameter | Value |
|-----------|-------|
| Buy levels | 10 (below entry price) |
| Sell levels | 10 (above entry price) |
| Level spacing | 2% |
| Cycle spread | 2% |
| Order size | 400 LIT (fixed) |

Levels are generated dynamically based on entry price when bot starts:
- Buy levels: entry × 0.98^1, entry × 0.98^2, ... entry × 0.98^10
- Sell levels: entry × 1.02^1, entry × 1.02^2, ... entry × 1.02^10

### Cycling Logic

```python
def on_buy_fill(order, filled_size):
    # Place sell 2% higher, snapped to grid
    raw_price = order.price * 1.02
    sell_price = snap_to_grid(raw_price)
    place_limit_sell(sell_price, filled_size)  # Same LIT amount

def on_sell_fill(order, filled_size):
    # Place buy 2% lower, snapped to grid
    raw_price = order.price * 0.98
    buy_price = snap_to_grid(raw_price)
    # Profit = filled_size * (sell_price - buy_price)
    place_limit_buy(buy_price, filled_size)  # Same LIT amount
```

### Key Design Points
- **Fixed sizing:** All orders are exactly 400 LIT (no position decay)
- **Grid snapping:** Prevents floating-point drift over time
- **Matched spacing:** 2% level spacing = 2% cycle spread ensures counter-orders land on grid levels
- **Profit from spread:** ~8 USDC per cycle at ~$1.60 price (400 × $1.63 - 400 × $1.60)

---

## Hedge + Funding Strategy

### Status: DISABLED

The hedge is currently **disabled** to avoid repeated stop-outs in ranging market conditions.

```python
HEDGE_CONFIG = {
    "enabled": False,  # Hedge disabled
    "short_size": Decimal("0"),
    "margin_usdc": Decimal("0"),
    ...
}
```

### Original Design (for reference)
The hedge was designed to:
1. Short 3,000 LIT perp for downside protection
2. Earn positive funding (10-50% APY)
3. Move floor breach from $1.03 to $0.89

It was disabled because:
- Ranging market caused repeated stop-outs at $1.95
- Each stop-out cost ~$810
- Net effect was negative in choppy conditions

---

## Floor Protection System

### Value-Based Approach

Floor protection is now **value-based** rather than price-tier based:

```python
FLOOR_CONFIG = {
    "floor_value": Decimal("25000"),      # Hard floor
    "emergency_buffer": Decimal("25500"), # Trigger emergency at $25.5k
}
```

### How It Works

1. **Portfolio calculation:** `LIT balance × current price + USDC balance`
   - Uses available balances only (not locked in orders)
2. **Emergency trigger:** When portfolio value drops to $25,500
3. **Action:** Pause grid, alert user

This is simpler and more robust than price-tier based protection because it:
- Accounts for actual position sizes (which vary with grid fills)
- Doesn't require predefined price levels
- Works regardless of entry price

---

## Upside Capture (Core Position)

### Core: 11,175 LIT

Limit sell orders placed at startup at premium prices:

| Target | LIT to Sell | Proceeds | Cumulative |
|--------|-------------|----------|------------|
| $2.00 | 1,500 | $3,000 | $3,000 |
| $2.25 | 1,675 | $3,769 | $6,769 |
| $2.50 | 1,000 | $2,500 | $9,269 |
| $3.00 | 1,500 | $4,500 | $13,769 |
| $3.50 | 2,000 | $7,000 | $20,769 |
| $4.00 | 2,000 | $8,000 | $28,769 |
| $4.50+ | 1,500 | $6,750+ | $35,519+ |

**Total:** 11,175 LIT = $35,519+ if all targets hit

---

## Configuration (Current)

```python
# Capital Allocation
ALLOCATION = {
    "core_lit": Decimal("11175"),       # 8000 + 3175 from reserve
    "grid_sell_lit": Decimal("5000"),
    "reserve_lit": Decimal("1000"),     # Moonbag - no sell orders
    "grid_buy_usdc": Decimal("6500"),
    "hedge_margin_usdc": Decimal("0"),  # Hedge disabled
    "cash_reserve_usdc": Decimal("800"),
}

# Grid Configuration
GRID_CONFIG = {
    "num_buy_levels": 10,
    "num_sell_levels": 10,
    "lit_per_order": Decimal("400"),        # Fixed 400 LIT per order
    "level_spacing_pct": Decimal("0.02"),   # 2% spacing between levels
    "cycle_spread_pct": Decimal("0.02"),    # 2% spread (MUST equal level_spacing)
}

# Hedge (DISABLED)
HEDGE_CONFIG = {
    "enabled": False,
    "short_size": Decimal("0"),
    "margin_usdc": Decimal("0"),
    ...
}

# Floor Protection (Value-based)
FLOOR_CONFIG = {
    "floor_value": Decimal("25000"),
    "emergency_buffer": Decimal("25500"),
}

# Core Position Sell Targets
CORE_TARGETS = [
    {"price": Decimal("2.00"), "lit": Decimal("1500")},
    {"price": Decimal("2.25"), "lit": Decimal("1675")},
    {"price": Decimal("2.50"), "lit": Decimal("1000")},
    {"price": Decimal("3.00"), "lit": Decimal("1500")},
    {"price": Decimal("3.50"), "lit": Decimal("2000")},
    {"price": Decimal("4.00"), "lit": Decimal("2000")},
    {"price": Decimal("4.50"), "lit": Decimal("1500")},
]

# Limits
LIMITS = {
    "max_lit": Decimal("22000"),
    "min_lit": Decimal("8000"),
    "max_short": Decimal("3000"),
    "min_usdc": Decimal("800"),
}
```

---

## State Tracking

State is persisted in SQLite database (`lithood_state.db`). Key tracked values:

- **Orders:** All pending, filled, cancelled orders with price/size/side
- **Grid stats:** Buy fills, sell fills, cycles completed, total profit
- **Entry price:** Grid entry price for level generation
- **Paused status:** Whether grid is paused

---

## Edge Cases

| Scenario | Action |
|----------|--------|
| Price gaps through multiple levels | Fill detection handles each fill, places counter-orders |
| Price exceeds grid range | Grid becomes dormant, core sells capture upside |
| Price below grid range | Floor protection triggers at $25,500 portfolio value |
| Bot restart | Cancels all exchange orders, clears local state, regenerates grid |
| Partial fill | Tracks partial amount, places proportional counter-order |
| Fill detection grace period | 30 seconds - new orders not checked until settled |

---

## Expected Performance

Grid performance depends on market choppiness:
- **High chop:** Multiple cycles/day, ~$8+ profit per cycle
- **Trending:** Grid becomes dormant (no fills on one side)
- **Range-bound:** Ideal conditions for grid cycling

With hedge disabled, strategy is pure long exposure + grid yield.

---

## Bot Operation

### Startup
1. Fetches current mid price
2. Cancels all existing orders on exchange
3. Generates grid levels (10 buy + 10 sell at 2% spacing)
4. Places initial grid orders (400 LIT each)
5. Places core sell orders at target prices

### Main Loop (every 2 seconds)
1. Check for filled orders
2. Place counter-orders for any fills (2% away, snapped to grid)
3. Check floor protection (portfolio value)
4. Log statistics

---

## Key Numbers

| Metric | Value |
|--------|-------|
| Hard floor | $25,000 |
| Target exit | $75,000 |
| Grid levels | 10 buy + 10 sell |
| Grid spacing | 2% |
| Order size | 400 LIT fixed |
| Profit per cycle | ~$8 at $1.60 price |
| Core sell targets | $2.00 - $4.50 |
| Core LIT | 11,175 |
