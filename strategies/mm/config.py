"""
Configuration objects for the simple market-making strategy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Optional


def _to_decimal(value) -> Decimal:
    """Convert incoming numeric values to Decimal."""
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid decimal value: {value}") from exc


@dataclass
class SimpleMMConfig:
    """
    Exchange-agnostic parameters for the simple market-making strategy.

    The object is also passed directly into exchange clients, so it keeps
    compatibility fields such as ``contract_id`` and ``tick_size``.
    """

    ticker: str
    quantity: Decimal
    base_spread_pct: Optional[Decimal] = Decimal("0.30")
    spread_ticks: Optional[int] = None
    refresh_interval: float = 2.0
    target_position: Decimal = Decimal("0")
    max_position: Decimal = Decimal("2")
    position_threshold: Decimal = Decimal("0.1")
    inventory_skew: Decimal = Decimal("0")
    exchange: str = ""
    contract_id: str = ""
    tick_size: Decimal = Decimal("0")
    max_cancel_retries: int = 3
    min_price_move: Decimal = field(default_factory=lambda: Decimal("0"))

    def __post_init__(self) -> None:
        self.ticker = self.ticker.upper()
        self.quantity = _to_decimal(self.quantity)

        if self.base_spread_pct is not None:
            self.base_spread_pct = _to_decimal(self.base_spread_pct)

        self.target_position = _to_decimal(self.target_position)
        self.max_position = _to_decimal(self.max_position)
        self.position_threshold = _to_decimal(self.position_threshold)
        self.inventory_skew = _to_decimal(self.inventory_skew)

        if self.refresh_interval <= 0:
            raise ValueError("refresh_interval must be greater than zero")
        if self.max_position <= 0:
            raise ValueError("max_position must be greater than zero")
        if self.quantity <= 0:
            raise ValueError("quantity must be greater than zero")
        if not (Decimal("0") <= self.inventory_skew <= Decimal("1")):
            raise ValueError("inventory_skew must be within [0, 1]")
        if self.max_cancel_retries < 1:
            raise ValueError("max_cancel_retries must be >= 1")

        if self.base_spread_pct is None and self.spread_ticks is None:
            raise ValueError("Either base_spread_pct or spread_ticks must be provided")

    @property
    def close_order_side(self) -> str:
        """
        Some exchange clients expect ``config.close_order_side`` to exist.

        For symmetrical quoting we mark sells as "close" orders.
        """
        return "sell"
