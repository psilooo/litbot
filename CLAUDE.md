# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

LIT Grid Trading Bot - an automated trading system for the Lighter DEX platform combining:
1. **Spot grid trading** - volatility extraction through buy/sell cycling
2. **Funding yield farming** - delta-neutral short position earning positive funding
3. **Floor protection** - tiered de-risk system to guarantee $25k minimum

**Current Status:** Strategy specification complete. Ready for implementation.

## Key Files

- `summary.md` - Complete strategy specification with all parameters
- `lighter_api_full.json` - Lighter API OpenAPI documentation (34MB, 59 endpoints)

## Position & Constraints

| Metric | Value |
|--------|-------|
| Starting capital | $36,154 (17,175 LIT + $7,300 USDC) |
| Hard floor | $25,000 |
| Target exit | $75,000 |
| Current price | $1.68 |

## Strategy Summary

### Three Profit Engines
1. **Spot Grid:** 8 buy levels ($1.48-$1.655), 8 sell levels ($1.705-$1.88), 1.5% spreads
2. **Hedge + Funding:** 3,000 LIT short for downside protection + 10-50% APY funding
3. **Core Position:** 8,000 LIT held for upside capture at $2.50-$4.50+

### Hedge Details
- Short 3,000 LIT perp (reduces net exposure from 17,175 to 14,175 LIT)
- Stop-loss at $1.95 (max loss $810)
- Re-entry at $1.75 after 24h cooldown
- Moves floor breach from $1.03 to $0.89 (14% more room)

### Capital Allocation
- Core (never grid): 8,000 LIT
- Grid sell: 5,000 LIT
- Reserve: 4,175 LIT
- Grid buy: $5,000 USDC
- Funding margin: $1,500 USDC
- Cash reserve: $800 USDC

### Floor Protection Tiers
| Price | Action |
|-------|--------|
| $1.50 | Pause grid buys |
| $1.40 | Sell 2,000 reserve LIT |
| $1.30 | Sell 2,175 reserve LIT |
| $1.20 | Cancel grid, sell 3,000 LIT |
| $1.10 | Exit all to guarantee $25k |

## Platform Characteristics

- **Zero trading fees** (spot and perp)
- **Hourly funding rate** (not 8-hour)
- **Funding typically positive:** 10-50% APY (longs pay shorts)
- **High volatility:** ±0.5% average hourly candle

## Architecture (Planned)

```
bot/
├── config/          # Grid levels, risk parameters
├── core/            # Grid engine, funding manager, state tracker
├── api/             # Lighter API client, websocket handler
├── risk/            # Floor protection, delta monitor
├── utils/           # Logger, state persistence
└── tests/
```

## Key Technical Requirements

1. **WebSocket connection** for real-time fill detection and price monitoring
2. **Auto-cycling logic:** buy fill → place sell, sell fill → place buy
3. **Hedge management:**
   - Open 3k LIT short on startup
   - Stop-loss at $1.95
   - Re-entry at $1.75 after 24h cooldown
4. **Floor protection:** automatic tier execution on price triggers
5. **State persistence:** survive restarts, reconcile with exchange state
