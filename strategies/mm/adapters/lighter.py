"""
Lighter adapter for the simple market-making strategy.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Tuple

from exchanges.base import OrderResult
from exchanges.lighter import LighterClient

from .base import SimpleMMAdapter
from ..config import SimpleMMConfig


class LighterAdapter(SimpleMMAdapter):
    """Bridge SimpleMarketMaker with the Lighter exchange client."""

    client: LighterClient

    def __init__(self, client: LighterClient, config: SimpleMMConfig):
        super().__init__(client, config)
        self.client = client

    async def initialize(self) -> None:
        """Initialise Lighter client, load market info, and connect."""
        # Ensure API client and signer client are ready
        await self.client._initialize_lighter_client()  # type: ignore[attr-defined]

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
        if reduce_only:
            return await self.client.place_close_order(
                self.contract_id,
                quantity,
                price,
                side,
            )
        return await self.client.place_open_order(
            self.contract_id,
            quantity,
            side,
        )

    async def cancel_order(self, order_id: str) -> OrderResult:
        return await self.client.cancel_order(order_id)

    async def get_signed_position(self) -> Decimal:
        return await self.client.get_account_positions()

    async def place_market_order(
        self,
        side: str,
        quantity: Decimal,
        reduce_only: bool = False,
    ) -> OrderResult:
        return await self.client.place_market_order(
            self.contract_id,
            quantity,
            side,
            reduce_only=reduce_only,
        )

    def round_price(self, price: Decimal) -> Decimal:
        return self.client.round_to_tick(price)

    def price_step(self) -> Decimal:
        return self.tick_size or Decimal("0.0001")
