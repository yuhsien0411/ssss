"""
Exchange-agnostic simple market-making strategy.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Dict, Optional, Union

from helpers import TradingLogger

from .config import SimpleMMConfig
from .adapters.base import SimpleMMAdapter


class SimpleMarketMaker:
    """Core event loop for the simple market-making strategy."""

    def __init__(
        self,
        adapter: SimpleMMAdapter,
        config: SimpleMMConfig,
        logger: Optional[TradingLogger] = None,
    ) -> None:
        self.adapter = adapter
        self.config = config
        self.logger = logger or TradingLogger(
            exchange=self.adapter.get_exchange_name(),
            ticker=self.config.ticker,
            log_to_console=True,
        )

        self._active_orders: Dict[str, Dict[str, Optional[Union[str, Decimal]]]] = {
            "buy": {"id": None, "price": None},
            "sell": {"id": None, "price": None},
        }
        self._running = False
        self._price_step = Decimal("0")

    async def run(self) -> None:
        """Main loop."""
        self._running = True
        await self.adapter.initialize()

        self._price_step = self.adapter.price_step() or Decimal("0.0001")
        if self.config.min_price_move == 0:
            self.config.min_price_move = self._price_step

        self.logger.log(
            f"Starting simple MM on {self.adapter.get_exchange_name().upper()}",
            "INFO",
        )
        self.logger.log(f"Ticker: {self.config.ticker}", "INFO")
        self.logger.log(f"Quantity: {self.config.quantity}", "INFO")
        if self.config.base_spread_pct is not None:
            self.logger.log(f"Spread: {self.config.base_spread_pct}%", "INFO")
        if self.config.spread_ticks is not None:
            self.logger.log(f"Spread: {self.config.spread_ticks} ticks", "INFO")
        self.logger.log(
            f"Refresh interval: {self.config.refresh_interval}s", "INFO"
        )
        self.logger.log(f"Target position: {self.config.target_position}", "INFO")
        self.logger.log(f"Max position: {self.config.max_position}", "INFO")

        try:
            while self._running:
                try:
                    await self._quote_cycle()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # pylint: disable=broad-except
                    self.logger.log(f"Quote cycle error: {exc}", "ERROR")
                await asyncio.sleep(self.config.refresh_interval)
        finally:
            await self._shutdown()

    async def stop(self) -> None:
        """Request graceful stop."""
        self._running = False

    async def _shutdown(self) -> None:
        """Cancel active orders and disconnect."""
        await self._cancel_side("buy")
        await self._cancel_side("sell")
        await self.adapter.shutdown()
        self.logger.log("Strategy stopped", "INFO")

    async def _quote_cycle(self) -> None:
        """Single quoting iteration."""
        best_bid, best_ask = await self.adapter.fetch_bbo()
        if best_bid <= 0 or best_ask <= 0 or best_bid >= best_ask:
            self.logger.log("Invalid market data, skip quoting cycle", "WARNING")
            return

        mid_price = (best_bid + best_ask) / Decimal("2")
        if mid_price <= 0:
            self.logger.log(f"Mid price invalid: {mid_price}", "WARNING")
            return

        net_position = await self.adapter.get_signed_position()
        if abs(net_position) >= self.config.max_position:
            await self._flatten_position(net_position)
            return

        buy_enabled, sell_enabled = self._position_based_toggles(net_position)
        skewed_mid = self._apply_inventory_skew(mid_price, net_position)
        buy_price, sell_price = self._compute_quotes(skewed_mid)

        if buy_enabled:
            await self._ensure_order("buy", buy_price)
        else:
            await self._cancel_side("buy")

        if sell_enabled:
            await self._ensure_order("sell", sell_price)
        else:
            await self._cancel_side("sell")

    def _position_based_toggles(self, net: Decimal) -> tuple[bool, bool]:
        """Decide whether buy/sell orders should be active given current position."""
        target = self.config.target_position
        threshold = self.config.position_threshold

        long_cutoff = target + threshold
        short_cutoff = -target - threshold

        buy_enabled = net <= long_cutoff
        sell_enabled = net >= short_cutoff

        return buy_enabled, sell_enabled

    def _apply_inventory_skew(self, mid_price: Decimal, net_position: Decimal) -> Decimal:
        """Shift the mid price based on current position and inventory skew."""
        if self.config.max_position == 0:
            return mid_price

        position_ratio = net_position / self.config.max_position
        position_ratio = max(min(position_ratio, Decimal("1")), Decimal("-1"))

        shift = mid_price * self.config.inventory_skew * position_ratio
        return mid_price - shift

    def _compute_quotes(self, mid_price: Decimal) -> tuple[Decimal, Decimal]:
        """Return bid/ask quotes rounded to the exchange tick size."""
        if self.config.spread_ticks is not None:
            offset = Decimal(self.config.spread_ticks) * self._price_step
            buy_price = mid_price - offset
            sell_price = mid_price + offset
        else:
            spread_fraction = (self.config.base_spread_pct or Decimal("0")) / Decimal("100")
            half_spread = spread_fraction / Decimal("2")
            buy_price = mid_price * (Decimal("1") - half_spread)
            sell_price = mid_price * (Decimal("1") + half_spread)

        buy_price = self.adapter.round_price(buy_price)
        sell_price = self.adapter.round_price(sell_price)

        if sell_price <= buy_price:
            sell_price = buy_price + self._price_step

        return buy_price, sell_price

    async def _ensure_order(self, side: str, price: Decimal) -> None:
        """Place or update the order on the given side."""
        state = self._active_orders[side]
        current_id = state["id"]
        current_price = state["price"]

        price_change = (
            abs(price - current_price)
            if isinstance(current_price, Decimal)
            else self.config.min_price_move
        )

        if current_id and isinstance(current_price, Decimal):
            if price_change < max(self.config.min_price_move, self._price_step):
                return
            await self._cancel_order(current_id, side)

        order_result = await self.adapter.place_limit_order(
            side,
            price,
            self.adapter.normalize_quantity(self.config.quantity),
        )

        if not order_result.success or not order_result.order_id:
            error_msg = order_result.error_message or "unknown error"
            self.logger.log(f"Failed to place {side} order: {error_msg}", "ERROR")
            self._active_orders[side] = {"id": None, "price": None}
            return

        actual_price = order_result.price or price
        self._active_orders[side] = {"id": order_result.order_id, "price": actual_price}
        self.logger.log(
            f"[QUOTE] {side.upper()} order {order_result.order_id} @ {actual_price}",
            "INFO",
        )

    async def _cancel_side(self, side: str) -> None:
        """Cancel any outstanding order on the specified side."""
        state = self._active_orders.get(side)
        if not state or not state["id"]:
            return
        await self._cancel_order(state["id"], side)
        self._active_orders[side] = {"id": None, "price": None}

    async def _cancel_order(self, order_id: str, side: str) -> None:
        """Cancel a specific order with retries."""
        for attempt in range(1, self.config.max_cancel_retries + 1):
            try:
                result = await self.adapter.cancel_order(order_id)
                if result.success:
                    self.logger.log(
                        f"[CANCEL] {side.upper()} order {order_id} cancelled",
                        "INFO",
                    )
                    return
                self.logger.log(
                    f"[CANCEL] Attempt {attempt} failed for {order_id}: {result.error_message}",
                    "WARNING",
                )
            except Exception as exc:  # pylint: disable=broad-except
                self.logger.log(
                    f"[CANCEL] Exception cancelling {order_id}: {exc}", "ERROR"
                )
            await asyncio.sleep(0.5 * attempt)

        self.logger.log(
            f"[CANCEL] Giving up on cancelling order {order_id} after retries",
            "ERROR",
        )

    async def _flatten_position(self, net_position: Decimal) -> None:
        """Execute a market order to bring exposure back within max limits."""
        side = "sell" if net_position > 0 else "buy"
        quantity = abs(net_position) - self.config.target_position
        if quantity <= 0:
            quantity = abs(net_position)
        quantity = max(quantity, self.config.quantity)

        self.logger.log(
            f"[RISK] Net position {net_position} breached limits, sending market {side} {quantity}",
            "WARNING",
        )
        result = await self.adapter.place_market_order(
            side,
            quantity,
            reduce_only=True,
        )
        if not result.success:
            self.logger.log(
                f"[RISK] Failed to flatten position: {result.error_message}", "ERROR"
            )
        else:
            self.logger.log(
                f"[RISK] Flatten order {result.order_id} sent ({side} {quantity})",
                "INFO",
            )
