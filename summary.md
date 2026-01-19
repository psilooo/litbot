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

| Asset | Amount | Value @ $1.68 |
|-------|--------|---------------|
| LIT spot | 17,175 | $28,854 |
| USDC | 7,300 | $7,300 |
| **Total** | | **$36,154** |

---

## Goals & Constraints

| Constraint | Value | Notes |
|------------|-------|-------|
| Hard floor | $25,000 | Cannot breach at any point |
| Target exit | $75,000 | ~$3.94 LIT price with hold |
| Net exposure | Always long | Matches bull thesis |
| Max drawdown | -$11,154 | Buffer from current to floor |

---

## Strategy: Hybrid Volatility Extraction + Funding Yield

### Why This Strategy

1. **High volatility (±0.5%/hr)** = excellent for tight grids
2. **Positive funding (10-50% APY)** = shorts earn yield
3. **Zero fees** = pure spread capture
4. **$25k floor requirement** = must have active risk management

### Three Profit Engines

1. **Spot Grid** - Extract volatility through buy/sell cycling
2. **Funding Yield** - Delta-neutral short position earns funding
3. **Core Position** - Upside capture on recovery to $4+

---

## Capital Allocation

| Category | LIT | USDC | Purpose |
|----------|-----|------|---------|
| Core (never grid) | 8,000 | - | Upside capture to $75k |
| Grid Sell | 5,000 | - | Active grid cycling |
| Reserve | 4,175 | - | Deploy on dips / liquidate on stops |
| Grid Buy | - | $5,000 | Active grid cycling |
| Funding Farm | - | $1,500 | Delta-neutral short margin |
| Cash Reserve | - | $800 | Emergency buffer |
| **Total** | **17,175** | **$7,300** | |

---

## Spot Grid (High-Frequency, Tight Spreads)

### Design Rationale
- ±0.5%/hr volatility supports 1.5% spreads
- Expected cycles: 3-5 per day in choppy conditions
- Profit per cycle: ~1.5% on level size
- Zero fees = pure spread capture

### Buy Levels (Accumulation Bias)

| Level | Price | USDC | LIT Acquired |
|-------|-------|------|--------------|
| 1 | $1.655 | $550 | 332 |
| 2 | $1.630 | $550 | 337 |
| 3 | $1.605 | $600 | 374 |
| 4 | $1.580 | $600 | 380 |
| 5 | $1.555 | $650 | 418 |
| 6 | $1.530 | $650 | 425 |
| 7 | $1.505 | $700 | 465 |
| 8 | $1.480 | $700 | 473 |
| **Total** | | **$5,000** | **~3,204** |

### Sell Levels

| Level | Price | LIT | USDC Received |
|-------|-------|-----|---------------|
| 1 | $1.705 | 600 | $1,023 |
| 2 | $1.730 | 600 | $1,038 |
| 3 | $1.755 | 600 | $1,053 |
| 4 | $1.780 | 650 | $1,157 |
| 5 | $1.805 | 650 | $1,173 |
| 6 | $1.830 | 700 | $1,281 |
| 7 | $1.855 | 700 | $1,299 |
| 8 | $1.880 | 500 | $940 |
| **Total** | | **5,000** | **$8,964** |

### Cycling Logic

```python
def on_buy_fill(order):
    # Place corresponding sell at next level up
    sell_price = order.price * 1.03  # 3% above buy
    sell_size = order.lit_received
    place_limit_sell(sell_price, sell_size)

def on_sell_fill(order):
    # Place corresponding buy at next level down
    buy_price = order.price * 0.97  # 3% below sell
    buy_usdc = order.usdc_received * 0.97  # Keep 3% as profit
    place_limit_buy(buy_price, buy_usdc)
```

### Expected Grid Performance

| Condition | Daily Cycles | Daily Profit | Monthly |
|-----------|--------------|--------------|---------|
| High chop | 5 | ~$375 | ~$11,250 |
| Medium chop | 3 | ~$225 | ~$6,750 |
| Low chop | 1 | ~$75 | ~$2,250 |
| Trending | 0 | $0 | $0 |

---

## Hedge + Funding Strategy

### Purpose
1. **Downside protection** - Reduces net exposure, moves floor breach from $1.03 to $0.89
2. **Funding income** - Earn 10-50% APY while hedged
3. **Insurance** - Peace of mind if this isn't the bottom

### Position Details
- Short size: 3,000 LIT perp
- Entry price: ~$1.68
- Margin required: $1,500 USDC (3.36x leverage)
- Liquidation price: ~$2.15 (but we stop-loss before)
- Net exposure: 14,175 LIT (vs 17,175 unhedged)

### Downside Protection Value

| Price | Hedge PnL | Portfolio With Hedge | Without Hedge | Benefit |
|-------|-----------|---------------------|---------------|---------|
| $1.50 | +$540 | $33,603 | $33,063 | +$540 |
| $1.30 | +$1,140 | $30,768 | $29,628 | +$1,140 |
| $1.10 | +$1,740 | $27,933 | $26,193 | +$1,740 |
| $0.90 | +$2,340 | $25,098 | $22,758 | +$2,340 |

### Floor Breach Comparison
- Without hedge: $1.03
- With hedge: $0.89
- **Benefit: 14% more room before floor**

### Funding Income Estimate

| APY | Daily Rate | Daily Income | Monthly |
|-----|------------|--------------|---------|
| 10% | 0.027% | $1.36 | $41 |
| 30% | 0.082% | $4.13 | $124 |
| 50% | 0.137% | $6.90 | $207 |

### Stop-Loss Management (Critical)

To avoid full liquidation loss ($1,500), use stop-loss:

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Stop-loss | $1.95 | Close before liquidation |
| Max loss | $810 | (1.95-1.68) × 3,000 |
| Re-entry | $1.75 | Re-open short after pullback |
| Cooldown | 24 hours | Don't re-enter immediately |

### Management Rules

```python
HEDGE_CONFIG = {
    "short_size": 3000,
    "entry_price": 1.68,
    "margin_usdc": 1500,
    "stop_loss": 1.95,              # Close before liquidation
    "max_loss_per_cycle": 810,      # Controlled loss
    "re_entry_price": 1.75,         # Re-open after pullback
    "re_entry_cooldown_hours": 24,  # Wait before re-entering
    "pause_if_negative_funding": True,  # Close if funding negative >24h
}
```

### Hedge Lifecycle

```
1. OPEN: Short 3,000 LIT at market when bot starts
2. MONITOR: Track price and funding rate
3. STOP-LOSS: If price >= $1.95, close short (realize ~$810 loss)
4. WAIT: Cooldown 24 hours, wait for price < $1.75
5. RE-ENTER: Open new short at current price
6. REPEAT: Back to step 2
```

---

## Floor Protection System

### The Math
```
At price $X: Portfolio = (17,175 × $X) + $7,300
Floor breach at: 17,175X + 7,300 = 25,000
X = $1.03
```

Current buffer: $36,154 - $25,000 = **$11,154**

### Tiered De-Risk Protocol

| Trigger | Action | Portfolio After |
|---------|--------|-----------------|
| $1.50 | Alert + pause grid buys | ~$33,063 |
| $1.40 | Sell 2,000 reserve LIT | ~$31,345 |
| $1.30 | Sell 2,175 reserve LIT | ~$29,555 |
| $1.20 | Cancel grid, sell 3,000 grid LIT | ~$27,210 |
| $1.10 | Sell remaining to hit floor | ~$25,000 |

### Implementation

```python
def check_floor_protection(price):
    portfolio = state.lit_balance * price + state.usdc_balance

    if price <= 1.50:
        pause_grid_buys()
        send_alert("Price warning: $1.50")

    if price <= 1.40 and state.reserve_lit >= 2000:
        market_sell(2000)
        log("Sold 2000 reserve LIT at $1.40")

    if price <= 1.30 and state.reserve_lit >= 2175:
        market_sell(2175)
        log("Sold 2175 reserve LIT at $1.30")

    if price <= 1.20:
        cancel_all_grid_orders()
        if state.grid_lit >= 3000:
            market_sell(3000)

    if portfolio <= 26000:  # Emergency buffer
        market_sell_all()
        close_all_perp_positions()
        halt_bot("Floor protection triggered")
```

---

## Upside Capture (Core Position)

### Core: 8,000 LIT (Never Grid)

Sell only at premium prices to capture recovery:

| Target | LIT to Sell | Proceeds | Cumulative |
|--------|-------------|----------|------------|
| $2.50 | 1,000 | $2,500 | $2,500 |
| $3.00 | 1,500 | $4,500 | $7,000 |
| $3.50 | 2,000 | $7,000 | $14,000 |
| $4.00 | 2,000 | $8,000 | $22,000 |
| $4.50+ | 1,500 | $6,750+ | $28,750+ |

### Path to $75k Target

At $3.94 with optimized strategy:
- Remaining core (if held): ~5,000 LIT × $3.94 = $19,700
- Core sells: $22,000
- Grid profits (3 months est.): $15,000
- Funding income: $500
- Starting USDC: $7,300
- **Total: ~$64,500 + remaining LIT**

Full $75k requires either:
- Price reaches ~$4.50, OR
- Extended chop period generating grid profits

---

## Perp Grid (Optional, Tactical)

### When to Use
- Only after significant pumps (+20%+)
- When funding is strongly positive (>30% APY)
- Not as permanent hedge

### Short Levels (If Enabled)

| Trigger | Size | Purpose |
|---------|------|---------|
| $2.10 | 500 | Tactical short after +25% pump |
| $2.40 | 500 | Add on continued strength |
| $2.70 | 500 | Final tranche |

### Cover Levels

| Trigger | Action |
|---------|--------|
| $2.00 | Close 50% |
| $1.80 | Close remaining |

### Stop Loss
- Hard stop: $3.00 (close all shorts)
- Max loss: ~$450 per tranche

---

## Risk Parameters

```python
CONFIG = {
    "allocation": {
        "core_lit": 8000,
        "grid_sell_lit": 5000,
        "reserve_lit": 4175,
        "grid_buy_usdc": 5000,
        "hedge_margin_usdc": 1500,
        "cash_reserve_usdc": 800,
    },
    "grid": {
        "buy_range": [1.48, 1.655],
        "sell_range": [1.705, 1.88],
        "levels": 8,
        "spread_target": 0.015,
        "cycle_profit_retain": 0.03,
    },
    "hedge": {
        "enabled": True,
        "short_size": 3000,
        "margin_usdc": 1500,
        "stop_loss": 1.95,
        "re_entry_price": 1.75,
        "re_entry_cooldown_hours": 24,
        "pause_if_negative_funding_hours": 24,
    },
    "floor_protection": {
        "floor_value": 25000,
        "warning_price": 1.50,
        "tier1": {"price": 1.40, "sell_lit": 2000},
        "tier2": {"price": 1.30, "sell_lit": 2175},
        "tier3": {"price": 1.20, "sell_lit": 3000},
        "emergency": {"price": 1.10, "action": "sell_all"},
    },
    "upside_targets": {
        2.50: 1000,
        3.00: 1500,
        3.50: 2000,
        4.00: 2000,
        4.50: 1500,
    },
    "perp_grid": {
        "enabled": False,  # Enable manually after pumps
        "short_levels": [2.10, 2.40, 2.70],
        "short_size": 500,
        "cover_levels": [2.00, 1.80],
        "stop_loss": 3.00,
    },
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
    # Balances
    "spot_lit": 17175,
    "spot_usdc": 5800,      # After $1,500 to perp margin
    "perp_margin": 1500,

    # Hedge Position
    "hedge_active": False,
    "hedge_short_size": 0,
    "hedge_entry_price": 0,
    "hedge_stopped_out_at": None,  # Timestamp of last stop-loss
    "hedge_pnl_realized": 0,

    # Grid Orders
    "active_buy_orders": [],   # [{price, usdc, order_id, status}]
    "active_sell_orders": [],  # [{price, lit, order_id, status}]

    # P&L Tracking
    "grid_pnl": 0,
    "funding_pnl": 0,
    "total_grid_cycles": 0,
    "total_hedge_cycles": 0,

    # Risk
    "floor_tier_triggered": 0,
    "last_price_check": None,
}
```

---

## Edge Cases

| Scenario | Action |
|----------|--------|
| Price gaps through multiple buy levels | Fill all, place corresponding sells |
| Price gaps through multiple sell levels | Fill all, place corresponding buys |
| Price exceeds grid range ($1.88+) | Let core position run, pause grid sells |
| Price below grid range ($1.48-) | Trigger floor protection tiers |
| Hedge stop-loss triggered ($1.95) | Close short, start 24h cooldown, wait for $1.75 re-entry |
| Hedge re-entry conditions met | Open new short at current price |
| Funding turns negative >24h | Close hedge short, wait for positive funding |
| Bot restart | Sync state from exchange, reconcile orders and positions |
| Partial fill | Track fill amount, place proportional opposite |
| Floor protection while hedge active | Close hedge first, then execute floor tier |

---

## Expected Performance by Scenario

| Scenario | Path | Final Value | Grid P&L | Hedge P&L | vs Hold |
|----------|------|-------------|----------|-----------|---------|
| Dump to $1.10 | Straight down | $27,933 | $0 | +$1,740 | +$1,740 |
| Dump to $0.90 | Straight down | $25,098 | $0 | +$2,340 | +$2,340 |
| Chop 3 months | $1.50-$1.90 | $48,000+ | $15,000+ | ~$0 (net) | +$12,000 |
| Slow recovery | Choppy to $2.50 | $53,000 | $8,000 | -$810 | +$4,000 |
| Fast pump | Straight to $4 | $67,000 | $0 | -$810 | -$8,000 |
| Moon | Straight to $6 | $89,000 | $0 | -$810 | -$16,000 |

**Strategy optimizes for downside protection and chop. Hedge adds ~$1,740 protection at $1.10 but costs ~$810 if price pumps past $1.95. Underperforms straight pumps but protects floor.**

---

## Daily Operations Checklist

1. Check current price vs grid range ($1.48-$1.88)
2. Verify all grid orders are active
3. Check hedge status:
   - If active: monitor vs stop-loss ($1.95)
   - If stopped out: check re-entry conditions ($1.75 + 24h cooldown)
4. Check funding rate - pause hedge if negative >24h
5. Monitor portfolio value vs floor ($25k)
6. Review filled orders and cycles completed
7. Check for any stuck/failed orders
8. Log daily P&L (grid + funding + hedge)

---

## Bot Requirements

1. Connect to Lighter API (spot + perps)
2. Place initial grid orders based on current price
3. Monitor fills via websocket
4. Auto-cycle: filled buy → place sell, filled sell → place buy
5. Manage funding short position
6. Execute floor protection tiers automatically
7. Track state persistently (survive restarts)
8. Log all trades for audit/tax

---

## Key Numbers

| Metric | Value |
|--------|-------|
| Starting value | $36,154 |
| Hard floor | $25,000 |
| Target exit | $75,000 |
| Grid capital | ~$13,400 (5k LIT + $5k USDC) |
| Hedge size | 3,000 LIT short |
| Net exposure | 14,175 LIT (vs 17,175 unhedged) |
| Expected grid yield (chop) | $150-$350/day |
| Expected funding yield | $1-$7/day |
| Hedge stop-loss | $1.95 (max loss $810) |
| Hedge re-entry | $1.75 (after 24h cooldown) |
| Floor breach (unhedged) | $1.03 |
| Floor breach (hedged) | $0.89 |
| Target exit price | ~$3.94-$4.50 |
