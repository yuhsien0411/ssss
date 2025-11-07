"""
Backpack adapter for the simple market-making strategy.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Tuple

from exchanges.backpack import BackpackClient
from exchanges.base import OrderResult

from .base import SimpleMMAdapter
from ..config import SimpleMMConfig


class BackpackAdapter(SimpleMMAdapter):
    """Bridge SimpleMarketMaker with the Backpack exchange client."""

    client: BackpackClient

    def __init__(self, client: BackpackClient, config: SimpleMMConfig):
        super().__init__(client, config)
        self.client = client

    async def initialize(self) -> None:
        """Load contract metadata and connect."""
        self.contract_id, self.tick_size = await self.client.get_contract_attributes()
        self.config.contract_id = self.contract_id
        self.config.tick_size = self.tick_size

        if self.config.min_price_move == 0:
            self.config.min_price_move = self.tick_size or Decimal("0.0001")

        await self.client.connect()

    async def shutdown(self) -> None:
        await self.client.disconnect()

    async def fetch_bbo(self) -> Tuple[Decimal, Decimal]:
        return await self.client.fetch_bbo_prices(self.contract_id)

    async def place_limit_order(
        self,
        side: str,
        price: Decimal,
        quantity: Decimal,
        reduce_only: bool = False,
    ) -> OrderResult:
        # Backpack client does not support reduce_only flag for LIMIT orders.
        _ = reduce_only
        return await self.client.place_close_order(
            self.contract_id,
            quantity,
            price,
            side,
        )

    async def cancel_order(self, order_id: str) -> OrderResult:
        return await self.client.cancel_order(order_id)

    async def get_signed_position(self) -> Decimal:
        try:
            positions = self.client.account_client.get_open_positions()
        except Exception:
            return Decimal("0")

        if isinstance(positions, list):
            for pos in positions:
                if pos.get("symbol") == self.contract_id:
                    size = Decimal(str(pos.get("netQuantity", "0")))
                    return size
        return Decimal("0")

    async def place_market_order(
        self,
        side: str,
        quantity: Decimal,
        reduce_only: bool = False,
    ) -> OrderResult:
        # reduce_only ignored; Backpack API uses side to infer close direction.
        _ = reduce_only
        return await self.client.place_market_order(
            self.contract_id,
            quantity,
            side,
        )

    def round_price(self, price: Decimal) -> Decimal:
        return self.client.round_to_tick(price)

    def price_step(self) -> Decimal:
        return self.tick_size or Decimal("0.0001")
