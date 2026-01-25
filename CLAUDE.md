# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

LIT Grid Trading Bot - an automated trading system for the Lighter DEX platform:
1. **Spot grid trading** - volatility extraction through buy/sell cycling
2. **Floor protection** - value-based system to guarantee $25k minimum
3. **Core position sells** - upside capture at premium prices

**Current Status:** Implemented and running.

## Key Files

- `lithood/config.py` - All strategy parameters (grid, allocation, targets)
- `lithood/grid.py` - Grid trading engine with fixed-level cycling
- `lithood/floor.py` - Floor protection system
- `lithood/client.py` - Lighter API client
- `lithood/state.py` - State persistence (SQLite)
- `scripts/run_bot.py` - Main entry point

## Position & Constraints

| Metric | Value |
|--------|-------|
| Hard floor | $25,000 |
| Target exit | $75,000 |

## Strategy Summary

### Two Profit Engines
1. **Spot Grid:** 10 buy + 10 sell levels, 2% spacing, 400 LIT per order
2. **Core Position:** 11,175 LIT with sells at $2.00-$4.50

### Hedge Status
**DISABLED** - Hedge is turned off for ranging market conditions to avoid repeated stop-outs.

### Capital Allocation
- Core LIT: 11,175 (sells at premium prices)
- Grid sell: 5,000 LIT
- Reserve: 1,000 LIT (moonbag - no sell orders)
- Grid buy: $6,500 USDC
- Hedge margin: $0 USDC (disabled)
- Cash reserve: $800 USDC

### Grid Configuration
- **Levels:** 10 buy + 10 sell
- **Spacing:** 2% between levels
- **Cycle spread:** 2% (matches spacing to prevent drift)
- **Order size:** Fixed 400 LIT per order (both buys and sells)
- **Snapping:** Counter-orders snap to nearest grid level

### Core Sell Targets
| Price | LIT |
|-------|-----|
| $2.00 | 1,500 |
| $2.25 | 1,675 |
| $2.50 | 1,000 |
| $3.00 | 1,500 |
| $3.50 | 2,000 |
| $4.00 | 2,000 |
| $4.50 | 1,500 |

### Floor Protection
- **Value-based:** Triggers at $25,500 portfolio value (emergency buffer)
- **Hard floor:** $25,000

## Platform Characteristics

- **Zero trading fees** (spot and perp)
- **Hourly funding rate** (not 8-hour)
- **High volatility:** ±0.5% average hourly candle

## Architecture

```
lithood/
├── config.py        # Grid levels, risk parameters, allocation
├── grid.py          # Grid engine with fixed-level cycling
├── floor.py         # Floor protection system
├── hedge.py         # Hedge manager (currently disabled)
├── client.py        # Lighter API client
├── state.py         # SQLite state persistence
├── types.py         # Order, Position, Market types
├── logger.py        # Logging configuration
└── retry.py         # API retry logic

scripts/
└── run_bot.py       # Main entry point
```

## Key Technical Details

1. **Fill detection:** Polling-based (no websocket) with 30s grace period for new orders
2. **Auto-cycling:** Buy fill → sell 2% higher, sell fill → buy 2% lower
3. **Grid snapping:** Counter-orders snap to predefined grid levels (prevents drift)
4. **Fixed sizing:** All grid orders are exactly 400 LIT
5. **Profit capture:** Profit comes from 2% price spread, not position size reduction
6. **State persistence:** SQLite database survives restarts
