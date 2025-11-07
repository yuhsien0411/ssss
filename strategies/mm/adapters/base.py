"""
Abstract adapter definition for simple market-making strategy.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Tuple

from exchanges.base import BaseExchangeClient, OrderResult
from ..config import SimpleMMConfig


class SimpleMMAdapter(ABC):
    """Exchange-specific bridge for the simple market-making strategy."""

    def __init__(self, client: BaseExchangeClient, config: SimpleMMConfig):
        self.client = client
        self.config = config
        self.contract_id: str = getattr(config, "contract_id", "")
        self.tick_size: Decimal = getattr(config, "tick_size", Decimal("0"))

    def get_exchange_name(self) -> str:
        """Return the adapter's exchange name."""
        return self.client.get_exchange_name()

    @abstractmethod
    async def initialize(self) -> None:
        """Connect to the exchange and load market metadata."""

    @abstractmethod
    async def shutdown(self) -> None:
        """Disconnect and cleanup any background tasks."""

    @abstractmethod
    async def fetch_bbo(self) -> Tuple[Decimal, Decimal]:
        """Return best bid/ask prices."""

    @abstractmethod
    async def place_limit_order(
        self,
        side: str,
        price: Decimal,
        quantity: Decimal,
        reduce_only: bool = False,
    ) -> OrderResult:
        """Submit a post-only limit order."""

    @abstractmethod
    async def cancel_order(self, order_id: str) -> OrderResult:
        """Cancel a specific order."""

    @abstractmethod
    async def get_signed_position(self) -> Decimal:
        """Return current signed position for the instrument."""

    @abstractmethod
    async def place_market_order(
        self,
        side: str,
        quantity: Decimal,
        reduce_only: bool = False,
    ) -> OrderResult:
        """Send a market order (typically used for risk flattening)."""

    @abstractmethod
    def round_price(self, price: Decimal) -> Decimal:
        """Round price to exchange tick."""

    @abstractmethod
    def price_step(self) -> Decimal:
        """Return the minimum price increment."""

    def normalize_quantity(self, quantity: Decimal) -> Decimal:
        """Optional quantity adjustment hook."""
        return quantity
