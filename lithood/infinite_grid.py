# lithood/infinite_grid.py
"""Infinite grid trading engine with adaptive recentering.

DESIGN:
-------
- Grid re-centers when price reaches top or bottom level
- Trailing sell floor prevents selling all LIT in a rally
- Floor ratchets up with each profitable cycle
- No core sells - all capital in the cycling grid
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, Set, List

from lithood.client import LighterClient
from lithood.state import StateManager
from lithood.types import Order, OrderSide, MarketType, OrderStatus
from lithood.config import SPOT_SYMBOL
from lithood.logger import log


class InfiniteGridConfig:
    """Configuration for infinite grid."""
    def __init__(
        self,
        num_levels: int = 15,  # levels per side (more than original 10)
        level_spacing_pct: Decimal = Decimal("0.02"),
        lit_per_order: Decimal = Decimal("400"),
        total_grid_lit: Decimal = Decimal("16000"),  # All LIT for grid (no core)
        recenter_threshold: int = 2,  # Recenter when within N levels of edge
    ):
        self.num_levels = num_levels
        self.level_spacing_pct = level_spacing_pct
        self.lit_per_order = lit_per_order
        self.total_grid_lit = total_grid_lit
        self.recenter_threshold = recenter_threshold


class InfiniteGridEngine:
    """Manages infinite grid trading with adaptive recentering."""

    def __init__(self, client: LighterClient, state: StateManager, config: InfiniteGridConfig = None):
        self.client = client
        self.state = state
        self.config = config or InfiniteGridConfig()
        self.symbol = SPOT_SYMBOL
        self.market_type = MarketType.SPOT

        # Grid state
        self._grid_center: Decimal = Decimal("0")
        self._buy_levels: List[Decimal] = []
        self._sell_levels: List[Decimal] = []
        self._sell_floor: Decimal = Decimal("0")  # Trailing floor

    async def initialize(self):
        """Initialize grid centered on current price."""
        pass  # Implemented in Task 2

    async def check_fills(self):
        """Check for fills and cycle orders."""
        pass  # Implemented in Task 3

    async def _maybe_recenter(self, current_price: Decimal):
        """Check if grid needs recentering."""
        pass  # Implemented in Task 4
