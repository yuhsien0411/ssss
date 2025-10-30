"""
Modular Trading Bot - Supports multiple exchanges
"""

import os
import time
import asyncio
import traceback
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from exchanges import ExchangeFactory
from helpers import TradingLogger
from helpers.lark_bot import LarkBot
from helpers.telegram_bot import TelegramBot


@dataclass
class TradingConfig:
    """Configuration class for trading parameters."""
    ticker: str
    contract_id: str
    quantity: Decimal
    take_profit: Decimal
    tick_size: Decimal
    direction: str
    max_orders: int
    wait_time: int
    exchange: str
    grid_step: Decimal
    stop_price: Decimal
    pause_price: Decimal
    boost_mode: bool

    @property
    def close_order_side(self) -> str:
        """Get the close order side based on bot direction."""
        return 'buy' if self.direction == "sell" else 'sell'


@dataclass
class OrderMonitor:
    """Thread-safe order monitoring state."""
    order_id: Optional[str] = None
    filled: bool = False
    filled_price: Optional[Decimal] = None
    filled_qty: Decimal = 0.0

    def reset(self):
        """Reset the monitor state."""
        self.order_id = None
        self.filled = False
        self.filled_price = None
        self.filled_qty = 0.0


class TradingBot:
    """Modular Trading Bot - Main trading logic supporting multiple exchanges."""

    def __init__(self, config: TradingConfig):
        self.config = config
        self.logger = TradingLogger(config.exchange, config.ticker, log_to_console=True)

        # Create exchange client
        try:
            self.exchange_client = ExchangeFactory.create_exchange(
                config.exchange,
                config
            )
        except ValueError as e:
            raise ValueError(f"Failed to create exchange client: {e}")

        # Trading state
        self.active_close_orders = []
        self.last_close_orders = 0
        self.last_open_order_time = 0
        self.last_log_time = 0
        self.current_order_status = None
        self.order_filled_event = asyncio.Event()
        self.order_canceled_event = asyncio.Event()
        self.shutdown_requested = False
        self.loop = None
        # Cache last seen partial fill during polling to rescue after cancel
        self.last_polled_filled_size = Decimal('0')

        # Register order callback
        self._setup_websocket_handlers()

    async def graceful_shutdown(self, reason: str = "Unknown"):
        """Perform graceful shutdown of the trading bot."""
        self.logger.log(f"Starting graceful shutdown: {reason}", "INFO")
        self.shutdown_requested = True

        try:
            # Disconnect from exchange
            await self.exchange_client.disconnect()
            self.logger.log("Graceful shutdown completed", "INFO")

        except Exception as e:
            self.logger.log(f"Error during graceful shutdown: {e}", "ERROR")

    def _setup_websocket_handlers(self):
        """Setup WebSocket handlers for order updates."""
        def order_update_handler(message):
            """Handle order updates from WebSocket."""
            try:
                # Check if this is for our contract
                if message.get('contract_id') != self.config.contract_id:
                    return

                order_id = message.get('order_id')
                status = message.get('status')
                side = message.get('side', '')
                order_type = message.get('order_type', '')
                filled_size = Decimal(message.get('filled_size'))
                if order_type == "OPEN":
                    self.current_order_status = status

                if status == 'FILLED':
                    if order_type == "OPEN":
                        self.order_filled_amount = filled_size
                        # Ensure thread-safe interaction with asyncio event loop
                        if self.loop is not None:
                            self.loop.call_soon_threadsafe(self.order_filled_event.set)
                        else:
                            # Fallback (should not happen after run() starts)
                            self.order_filled_event.set()

                    self.logger.log(f"[{order_type}] [{order_id}] {status} "
                                    f"{message.get('size')} @ {message.get('price')}", "INFO")
                    self.logger.log_transaction(order_id, side, message.get('size'), message.get('price'), status)
                elif status == "CANCELED":
                    if order_type == "OPEN":
                        self.order_filled_amount = filled_size
                        if self.loop is not None:
                            self.loop.call_soon_threadsafe(self.order_canceled_event.set)
                        else:
                            self.order_canceled_event.set()

                        if self.order_filled_amount > 0:
                            self.logger.log_transaction(order_id, side, self.order_filled_amount, message.get('price'), status)
                            
                    # PATCH
                    if self.config.exchange == "extended":
                        self.logger.log(f"[{order_type}] [{order_id}] {status} "
                                        f"{Decimal(message.get('size')) - filled_size} @ {message.get('price')}", "INFO")
                    else:
                        self.logger.log(f"[{order_type}] [{order_id}] {status} "
                                        f"{message.get('size')} @ {message.get('price')}", "INFO")
                elif status == "PARTIALLY_FILLED":
                    self.logger.log(f"[{order_type}] [{order_id}] {status} "
                                    f"{filled_size} @ {message.get('price')}", "INFO")
                else:
                    self.logger.log(f"[{order_type}] [{order_id}] {status} "
                                    f"{message.get('size')} @ {message.get('price')}", "INFO")

            except Exception as e:
                self.logger.log(f"Error handling order update: {e}", "ERROR")
                self.logger.log(f"Traceback: {traceback.format_exc()}", "ERROR")

        # Setup order update handler
        self.exchange_client.setup_order_update_handler(order_update_handler)

    def _calculate_wait_time(self) -> Decimal:
        """Calculate wait time between orders with position limits."""
        # Check if we have too many active orders
        if len(self.active_close_orders) >= self.config.max_orders:
            return 1

        # Check if we have too much position (more than max_orders * quantity)
        # This ensures position limit scales with max_orders setting
        if hasattr(self, 'current_position') and self.current_position:
            max_position = self.config.quantity * self.config.max_orders  # e.g., 100 * 100 = 10000
            if abs(self.current_position) > max_position:
                self.logger.log(f"Position too large ({self.current_position}), pausing new orders for 5s", "WARNING")
                return 5  # Wait 5 seconds if position is too large
        
        # Minimal wait time for normal cases
        return 0

    async def _place_and_monitor_open_order(self) -> bool:
        """Place an order and monitor its execution."""
        try:
            # Reset state before placing order
            self.order_filled_event.clear()
            self.current_order_status = 'OPEN'
            self.order_filled_amount = 0.0

            # Place the order
            order_result = await self.exchange_client.place_open_order(
                self.config.contract_id,
                self.config.quantity,
                self.config.direction
            )

            if not order_result.success:
                return False

            order_id = order_result.order_id

            # Check if immediately filled
            if order_result.status == 'FILLED':
                self.logger.log(f"[OPEN] [{order_id}] Order filled immediately", "INFO")
                return await self._handle_order_result(order_result)

            # Poll order status every 1 second for up to 10 seconds
            self.logger.log(f"[OPEN] [{order_id}] Polling order status every 1s for 10s", "INFO")
            max_polls = 10
            for poll_count in range(max_polls):
                await asyncio.sleep(1)
                
                # Get current order status
                if self.config.exchange == "lighter":
                    self.logger.log(f"[API] Checking current_order from WebSocket", "INFO")
                    current_order = self.exchange_client.current_order
                    if current_order:
                        self.logger.log(f"[API] current_order found: order_id={current_order.order_id}, status={current_order.status}, filled={current_order.filled_size}", "INFO")
                    else:
                        self.logger.log(f"[API] current_order is None", "INFO")
                    
                    if current_order and str(current_order.order_id) == str(order_id):
                        current_status = current_order.status
                        filled_size = current_order.filled_size
                        self.logger.log(f"[API] Using current_order data: status={current_status}, filled={filled_size}", "INFO")
                    else:
                        # Fallback: query order info
                        self.logger.log(f"[API] Calling get_order_info({order_id})", "INFO")
                        order_info = await self.exchange_client.get_order_info(order_id)
                        if order_info:
                            current_status = order_info.status
                            filled_size = order_info.filled_size
                            self.logger.log(f"[API] get_order_info returned: status={current_status}, filled={filled_size}", "INFO")
                        else:
                            self.logger.log(f"[API] get_order_info returned None, skipping this poll", "WARNING")
                            continue
                else:
                    self.logger.log(f"[API] Calling get_order_info({order_id})", "INFO")
                    order_info = await self.exchange_client.get_order_info(order_id)
                    if order_info:
                        current_status = order_info.status
                        filled_size = order_info.filled_size
                        self.logger.log(f"[API] get_order_info returned: status={current_status}, filled={filled_size}", "INFO")
                    else:
                        self.logger.log(f"[API] get_order_info returned None, skipping this poll", "WARNING")
                        continue
                
                self.logger.log(f"[OPEN] [{order_id}] Poll {poll_count + 1}/{max_polls}: status={current_status}, filled={filled_size}", "INFO")
                
                # Check if filled
                if current_status == 'FILLED':
                    self.logger.log(f"[OPEN] [{order_id}] Order filled after {poll_count + 1}s", "INFO")
                    self.order_filled_amount = filled_size
                    self.order_filled_event.set()
                    # Update order_result status
                    order_result.status = 'FILLED'
                    break
                elif current_status in ['CANCELED', 'REJECTED', 'CANCELED-POST-ONLY']:
                    self.logger.log(f"[OPEN] [{order_id}] Order {current_status}", "WARNING")
                    break
                else:
                    # Track partial fills seen during polling
                    try:
                        if Decimal(str(filled_size)) > 0:
                            self.last_polled_filled_size = Decimal(str(filled_size))
                    except Exception:
                        pass

            # Handle order result
            return await self._handle_order_result(order_result)

        except Exception as e:
            self.logger.log(f"Error placing order: {e}", "ERROR")
            self.logger.log(f"Traceback: {traceback.format_exc()}", "ERROR")
            return False

    async def _handle_order_result(self, order_result) -> bool:
        """Handle the result of an order placement."""
        order_id = order_result.order_id
        filled_price = order_result.price
        
        # Use actual filled amount, or order_filled_amount if set
        filled_quantity = self.order_filled_amount if self.order_filled_amount > 0 else self.config.quantity
        
        # Log the filled quantity
        if filled_quantity != self.config.quantity:
            self.logger.log(f"[OPEN] Partial fill detected: filled={filled_quantity}, requested={self.config.quantity}", "WARNING")

        if self.order_filled_event.is_set() or order_result.status == 'FILLED':
            if self.config.boost_mode:
                close_order_result = await self.exchange_client.place_market_order(
                    self.config.contract_id,
                    filled_quantity,  # ✅ Use actual filled quantity
                    self.config.close_order_side
                )
            else:
                self.last_open_order_time = time.time()
                # Place close order
                close_side = self.config.close_order_side
                if close_side == 'sell':
                    close_price = filled_price * (1 + self.config.take_profit/100)
                else:
                    close_price = filled_price * (1 - self.config.take_profit/100)

                self.logger.log(f"[CLOSE] Placing close order for filled quantity: {filled_quantity} @ {close_price}", "INFO")
                
                # Retry logic for close order placement
                max_retries = 3
                for retry in range(max_retries):
                    close_order_result = await self.exchange_client.place_close_order(
                        self.config.contract_id,
                        filled_quantity,  # ✅ Use actual filled quantity instead of config.quantity
                        close_price,
                        close_side
                    )
                    if self.config.exchange == "lighter":
                        await asyncio.sleep(1)

                    if close_order_result.success:
                        self.logger.log(f"[CLOSE] Successfully placed close order on attempt {retry + 1}", "INFO")
                        break
                    else:
                        self.logger.log(f"[CLOSE] Failed to place close order (attempt {retry + 1}/{max_retries}): {close_order_result.error_message}", "WARNING")
                        
                        if retry < max_retries - 1:
                            # Adjust close price slightly to avoid Post-Only rejection
                            if close_side == 'sell':
                                close_price = close_price * Decimal('1.0001')  # Increase by 0.01%
                            else:
                                close_price = close_price * Decimal('0.9999')  # Decrease by 0.01%
                            
                            self.logger.log(f"[CLOSE] Retrying with adjusted price: {close_price}", "INFO")
                            await asyncio.sleep(1)
                        else:
                            self.logger.log(f"[CLOSE] CRITICAL: Failed to place close order after {max_retries} attempts!", "ERROR")
                            self.logger.log(f"[CLOSE] CRITICAL: Position={filled_quantity} at {filled_price} has NO close order!", "ERROR")
                            # Don't raise exception - continue trading but log the issue
                            # raise Exception(f"[CLOSE] Failed to place close order: {close_order_result.error_message}")

                return True

        else:
            new_order_price = await self.exchange_client.get_order_price(self.config.direction)

            def should_wait(direction: str, new_order_price: Decimal, order_result_price: Decimal) -> bool:
                if direction == "buy":
                    return new_order_price <= order_result_price
                elif direction == "sell":
                    return new_order_price >= order_result_price
                return False

            if self.config.exchange == "lighter":
                current_order_status = self.exchange_client.current_order.status
            else:
                order_info = await self.exchange_client.get_order_info(order_id)
                current_order_status = order_info.status

            while (
                should_wait(self.config.direction, new_order_price, order_result.price)
                and current_order_status == "OPEN"
            ):
                self.logger.log(f"[OPEN] [{order_id}] Waiting for order to be filled @ {order_result.price}", "INFO")
                await asyncio.sleep(5)
                if self.config.exchange == "lighter":
                    current_order_status = self.exchange_client.current_order.status
                else:
                    order_info = await self.exchange_client.get_order_info(order_id)
                    if order_info is not None:
                        current_order_status = order_info.status
                new_order_price = await self.exchange_client.get_order_price(self.config.direction)

            self.order_canceled_event.clear()
            # Cancel the order if it's still open
            self.logger.log(f"[OPEN] [{order_id}] Cancelling order and placing a new order", "INFO")
            if self.config.exchange == "lighter":
                cancel_result = await self.exchange_client.cancel_order(order_id)
                start_time = time.time()
                while (time.time() - start_time < 10 and self.exchange_client.current_order.status not in ['CANCELED', 'FILLED', 'CANCELED-POST-ONLY']):
                    await asyncio.sleep(0.1)

                if self.exchange_client.current_order.status not in ['CANCELED', 'FILLED', 'CANCELED-POST-ONLY']:
                    raise Exception(f"[OPEN] Error cancelling order: {self.exchange_client.current_order.status}")
                else:
                    # ⚠️ WebSocket's filled_size may be inaccurate, force API query
                    self.logger.log(f"[OPEN] [{order_id}] Order canceled, querying API for accurate filled_size...", "INFO")
                    await asyncio.sleep(0.5)  # Wait for exchange to process
                    
                    # First: query inactive orders via API to get finalized status & filled size
                    self.order_filled_amount = 0.0
                    requested_order_id = str(order_id)
                    finalized = await self.exchange_client.get_finalized_order_from_api(requested_order_id)
                    order_info = None
                    if finalized and finalized.filled_size > 0:
                        self.order_filled_amount = finalized.filled_size
                        filled_price = finalized.price
                        self.logger.log(f"[OPEN] [{order_id}] Finalized via API: status={finalized.status}, filled_size={self.order_filled_amount}", "INFO")
                    else:
                        # Fallback: Force API query to get accurate filled amount with retry (current_order)
                        for api_retry in range(3):
                            order_info = await self.exchange_client.get_order_info(requested_order_id)
                            if order_info and order_info.filled_size > 0:
                                self.order_filled_amount = order_info.filled_size
                                filled_price = order_info.price
                                self.logger.log(f"[OPEN] [{order_id}] API query result (attempt {api_retry + 1}): filled_size={self.order_filled_amount}", "INFO")
                                break
                            else:
                                self.logger.log(f"[OPEN] [{order_id}] API query attempt {api_retry + 1} failed or filled_size=0, retrying...", "WARNING")
                                await asyncio.sleep(1)  # Wait 1 second before retry
                        
                        # If API still fails, try WebSocket data
                        if self.order_filled_amount == 0:
                            self.order_filled_amount = self.exchange_client.current_order.filled_size
                            self.logger.log(f"[OPEN] [{order_id}] API query failed after 3 attempts, using WebSocket data: filled_size={self.order_filled_amount}", "WARNING")
                    # If WS 也為 0，但輪詢期間看過部分成交，使用快取救援
                    try:
                        if Decimal(str(self.order_filled_amount)) == 0 and self.last_polled_filled_size > 0:
                            self.order_filled_amount = self.last_polled_filled_size
                            self.logger.log(f"[OPEN] [{order_id}] Using cached partial fill from polling: filled_size={self.order_filled_amount}", "WARNING")
                    except Exception:
                        pass
                    
                    if self.order_filled_amount > 0:
                        self.logger.log(f"[OPEN] [{order_id}] Partial fill detected: {self.order_filled_amount}/{self.config.quantity}", "WARNING")
                        # Update filled_price to the actual filled price from order_info
                        if order_info and hasattr(order_info, 'price'):
                            filled_price = order_info.price
                            self.logger.log(f"[OPEN] [{order_id}] Using filled price from order_info: {filled_price}", "INFO")
                        else:
                            self.logger.log(f"[OPEN] [{order_id}] Using order_result price as filled price: {filled_price}", "INFO")
            else:
                try:
                    cancel_result = await self.exchange_client.cancel_order(order_id)
                    if not cancel_result.success:
                        self.order_canceled_event.set()
                        self.logger.log(f"[CLOSE] Failed to cancel order {order_id}: {cancel_result.error_message}", "WARNING")
                    else:
                        self.current_order_status = "CANCELED"

                except Exception as e:
                    self.order_canceled_event.set()
                    self.logger.log(f"[CLOSE] Error canceling order {order_id}: {e}", "ERROR")

                if self.config.exchange == "backpack" or self.config.exchange == "extended":
                    self.order_filled_amount = cancel_result.filled_size
                else:
                    # Wait for cancel event or timeout
                    if not self.order_canceled_event.is_set():
                        try:
                            await asyncio.wait_for(self.order_canceled_event.wait(), timeout=5)
                        except asyncio.TimeoutError:
                            order_info = await self.exchange_client.get_order_info(order_id)
                            self.order_filled_amount = order_info.filled_size

                if self.order_filled_amount > 0:
                    self.logger.log(f"[OPEN] [{order_id}] Partial fill detected: {self.order_filled_amount}/{self.config.quantity}", "WARNING")
                    # Update filled_price to the actual filled price from cancel_result
                    if hasattr(cancel_result, 'price') and cancel_result.price:
                        filled_price = cancel_result.price
                        self.logger.log(f"[OPEN] [{order_id}] Using filled price from cancel_result: {filled_price}", "INFO")
                    else:
                        self.logger.log(f"[OPEN] [{order_id}] Using order_result price as filled price: {filled_price}", "INFO")

            if self.order_filled_amount > 0:
                self.logger.log(f"[CLOSE] 🎯 PARTIAL FILL DETECTED: {self.order_filled_amount}/{self.config.quantity} @ {filled_price}", "WARNING")
                self.logger.log(f"[CLOSE] Creating REDUCE-ONLY + POST-ONLY close order for partial fill", "INFO")
                close_side = self.config.close_order_side
                
                # Initialize close_order_result to avoid UnboundLocalError
                close_order_result = None
                
                if self.config.boost_mode:
                    close_order_result = await self.exchange_client.place_close_order(
                        self.config.contract_id,
                        self.order_filled_amount,
                        filled_price,
                        close_side
                    )
                else:
                    # Use opponent best price (ask for sell, bid for buy) × TP
                    try:
                        api_bid, api_ask, _ = await self.exchange_client.fetch_order_book_from_api(int(self.config.contract_id), limit=5)
                    except Exception:
                        api_bid, api_ask = None, None, 
                    base_price = None
                    if close_side == 'sell':
                        base_price = api_ask
                        if not base_price:
                            base_price = await self.exchange_client.get_order_price('sell')
                        close_price = base_price * (1 + self.config.take_profit/100)
                    else:
                        base_price = api_bid
                        if not base_price:
                            base_price = await self.exchange_client.get_order_price('buy')
                        close_price = base_price * (1 - self.config.take_profit/100)
                    # Deduplicate: skip if similar close already exists
                    try:
                        active_orders = await self.exchange_client.get_active_orders(self.config.contract_id)
                        tick = getattr(self.config, 'tick_size', Decimal('0')) or Decimal('0')
                        for o in active_orders:
                            if o.side != close_side:
                                continue
                            size_close_enough = abs(Decimal(o.size) - self.order_filled_amount) <= max(Decimal('0.1'), self.order_filled_amount * Decimal('0.01'))
                            price_close_enough = (tick > 0 and abs(Decimal(o.price) - close_price) <= tick) or (abs(Decimal(o.price) - close_price) / close_price <= Decimal('0.0005'))
                            if size_close_enough and price_close_enough:
                                self.logger.log(f"[CLOSE] Skip duplicate TP: existing size={o.size} price={o.price}", "INFO")
                                # Re-verify after brief delay to avoid API lag false positives
                                await asyncio.sleep(2)
                                active_orders_2 = await self.exchange_client.get_active_orders(self.config.contract_id)
                                exists_after = any(
                                    (ao.side == close_side) and (
                                        abs(Decimal(ao.size) - self.order_filled_amount) <= max(Decimal('0.1'), self.order_filled_amount * Decimal('0.01')) and (
                                            (tick > 0 and abs(Decimal(ao.price) - close_price) <= tick) or (abs(Decimal(ao.price) - close_price) / close_price <= Decimal('0.0005'))
                                        )
                                    ) for ao in active_orders_2
                                )
                                if exists_after:
                                    return
                                else:
                                    self.logger.log("[CLOSE] Re-check found no similar TP, will place now", "WARNING")
                                break
                    except Exception:
                        pass

                    self.logger.log(f"[CLOSE] Placing REDUCE-ONLY + POST-ONLY close order: {self.order_filled_amount} @ {close_price}", "INFO")
                    
                    # Retry logic for partial fill close order placement
                    max_retries = 3
                    for retry in range(max_retries):
                        close_order_result = await self.exchange_client.place_close_order(
                            self.config.contract_id,
                            self.order_filled_amount,
                            close_price,
                            close_side
                        )
                        if self.config.exchange == "lighter":
                            await asyncio.sleep(1)

                        if close_order_result.success:
                            self.logger.log(f"[CLOSE] ✅ Successfully placed REDUCE-ONLY + POST-ONLY partial fill close order on attempt {retry + 1}", "INFO")
                            break
                        else:
                            self.logger.log(f"[CLOSE] Failed to place partial fill close order (attempt {retry + 1}/{max_retries}): {close_order_result.error_message}", "WARNING")
                            
                            if retry < max_retries - 1:
                                # Adjust close price slightly to avoid Post-Only rejection
                                if close_side == 'sell':
                                    close_price = close_price * Decimal('1.0001')  # Increase by 0.01%
                                else:
                                    close_price = close_price * Decimal('0.9999')  # Decrease by 0.01%
                                
                                self.logger.log(f"[CLOSE] Retrying partial fill close order with adjusted price: {close_price}", "INFO")
                                await asyncio.sleep(1)
                    else:
                        self.logger.log(f"[CLOSE] CRITICAL: Failed to place partial fill close order after {max_retries} attempts!", "ERROR")
                        self.logger.log(f"[CLOSE] CRITICAL: Partial position={self.order_filled_amount} at {filled_price} has NO close order!", "ERROR")
                        # Fallback: use market order to immediately reduce the imbalance
                        try:
                            market_result = await self.exchange_client.place_market_order(
                                self.config.contract_id,
                                self.order_filled_amount,
                                close_side
                            )
                            if market_result and market_result.success:
                                self.logger.log(f"[CLOSE] ✅ Fallback market close succeeded for {self.order_filled_amount}", "WARNING")
                            else:
                                self.logger.log(f"[CLOSE] ❌ Fallback market close failed", "ERROR")
                        except Exception as me:
                            self.logger.log(f"[CLOSE] Error during fallback market close: {me}", "ERROR")

                self.last_open_order_time = time.time()
                if close_order_result and not close_order_result.success:
                    self.logger.log(f"[CLOSE] Failed to place partial fill close order: {close_order_result.error_message}", "ERROR")
                elif close_order_result and close_order_result.success:
                    self.logger.log(f"[CLOSE] ✅ Partial fill close order placed successfully!", "INFO")
                else:
                    self.logger.log(f"[CLOSE] ❌ CRITICAL: close_order_result is None!", "ERROR")

            return True

        return False

    async def _log_status_periodically(self):
        """Log status information periodically, including positions."""
        if time.time() - self.last_log_time > 60 or self.last_log_time == 0:
            print("--------------------------------")

    async def _reconcile_close_coverage(self) -> bool:
        """Ensure active close orders cover current position size using fresh API reads.
        Returns True if a top-up close order was placed, else False.
        """
        try:
            # Always fetch fresh position and active orders
            position_amt = await self.exchange_client.get_account_positions()
            if position_amt == 0:
                return False

            # Fetch active orders and sum close-side sizes
            active_orders = await self.exchange_client.get_active_orders(self.config.contract_id)
            active_close_amount = sum(
                Decimal(getattr(o, 'size', 0)) if not isinstance(o, dict) else Decimal(o.get('size', 0))
                for o in active_orders
                if (getattr(o, 'side', None) == self.config.close_order_side) or (isinstance(o, dict) and o.get('side') == self.config.close_order_side)
            )

            close_side = self.config.close_order_side
            required_close = abs(position_amt)
            if active_close_amount >= required_close:
                return False

            deficit = (required_close - active_close_amount).quantize(Decimal('0.00000001'))
            if deficit <= 0:
                return False

            # Duplicate-suppression: avoid placing the same reconcile twice within a short window
            now_ts = time.time()
            deficit_signature = f"{close_side}:{deficit}"
            last_sig = getattr(self, "_last_reconcile_signature", None)
            last_ts = getattr(self, "_last_reconcile_time", 0)
            if last_sig == deficit_signature and (now_ts - last_ts) < 5:
                self.logger.log(f"[RECONCILE] Skip duplicate within 5s window for {deficit_signature}", "INFO")
                return False

            # Use opponent best price × TP (ask for sell, bid for buy)
            try:
                api_bid, api_ask, _ = await self.exchange_client.fetch_order_book_from_api(int(self.config.contract_id), limit=5)
            except Exception:
                api_bid, api_ask = None, None
            if close_side == 'sell':
                base_price = api_ask if api_ask else await self.exchange_client.get_order_price('sell')
                close_price = base_price * (Decimal('1') + self.config.take_profit/100)
            else:
                base_price = api_bid if api_bid else await self.exchange_client.get_order_price('buy')
                close_price = base_price * (Decimal('1') - self.config.take_profit/100)

            self.logger.log(f"[RECONCILE] Position={position_amt}, ActiveClose={active_close_amount} → Deficit={deficit}. Placing RO+PO close at {close_price}", "WARNING")

            # Skip if a similar close already exists (API may have lagged earlier)
            try:
                active_orders = await self.exchange_client.get_active_orders(self.config.contract_id)
                tick = getattr(self.config, 'tick_size', Decimal('0')) or Decimal('0')
                for o in active_orders:
                    if o.side != close_side:
                        continue
                    size_close_enough = abs(Decimal(o.size) - deficit) <= max(Decimal('0.1'), deficit * Decimal('0.01'))
                    price_close_enough = (tick > 0 and abs(Decimal(o.price) - close_price) <= tick) or (abs(Decimal(o.price) - close_price) / close_price <= Decimal('0.0005'))
                    if size_close_enough and price_close_enough:
                        self.logger.log(f"[RECONCILE] Skip: similar TP exists size={o.size} price={o.price}", "INFO")
                        # Re-verify after brief delay to avoid API lag false positives
                        await asyncio.sleep(2)
                        active_orders_2 = await self.exchange_client.get_active_orders(self.config.contract_id)
                        exists_after = any(
                            (ao.side == close_side) and (
                                abs(Decimal(ao.size) - deficit) <= max(Decimal('0.1'), deficit * Decimal('0.01')) and (
                                    (tick > 0 and abs(Decimal(ao.price) - close_price) <= tick) or (abs(Decimal(ao.price) - close_price) / close_price <= Decimal('0.0005'))
                                )
                            ) for ao in active_orders_2
                        )
                        if exists_after:
                            return False
                        else:
                            self.logger.log("[RECONCILE] Re-check found no similar TP, will place now", "WARNING")
                        break
            except Exception:
                pass

            # Retry logic with micro-adjust to satisfy PO
            max_retries = 3
            for retry in range(max_retries):
                result = await self.exchange_client.place_close_order(
                    self.config.contract_id,
                    deficit,
                    close_price,
                    close_side
                )
                if self.config.exchange == 'lighter':
                    await asyncio.sleep(1)

                if result.success:
                    self.logger.log(f"[RECONCILE] ✅ Placed top-up close order {deficit} on attempt {retry+1}", "INFO")
                    # Verify presence to avoid false success due to API lag
                    try:
                        await asyncio.sleep(2)
                        verify_orders = await self.exchange_client.get_active_orders(self.config.contract_id)
                        tick = getattr(self.config, 'tick_size', Decimal('0')) or Decimal('0')
                        exists = any(
                            (o.side == close_side) and (
                                abs(Decimal(o.size) - deficit) <= max(Decimal('0.1'), deficit * Decimal('0.01')) and (
                                    (tick > 0 and abs(Decimal(o.price) - close_price) <= tick) or (abs(Decimal(o.price) - close_price) / close_price <= Decimal('0.0005'))
                                )
                            ) for o in verify_orders
                        )
                        if exists:
                            # record signature to prevent rapid duplicate in case API-active-orders lag
                            self._last_reconcile_signature = deficit_signature
                            self._last_reconcile_time = time.time()
                            return True
                        else:
                            self.logger.log("[RECONCILE] Verification could not find the new TP; retrying placement", "WARNING")
                            # fall through to retry by continuing the loop
                            result.success = False
                    except Exception:
                        # If verification fails, still record signature to avoid spamming
                        self._last_reconcile_signature = deficit_signature
                        self._last_reconcile_time = time.time()
                        return True
                else:
                    if close_side == 'sell':
                        close_price = close_price * Decimal('1.0001')
                    else:
                        close_price = close_price * Decimal('0.9999')
                    await asyncio.sleep(1)

            self.logger.log("[RECONCILE] ❌ Failed to place top-up close order after retries", "ERROR")
            # Fallback to market order to quickly resolve imbalance
            try:
                market_result = await self.exchange_client.place_market_order(
                    self.config.contract_id,
                    deficit,
                    close_side
                )
                if market_result and market_result.success:
                    self.logger.log(f"[RECONCILE] ✅ Fallback market close succeeded for deficit {deficit}", "WARNING")
                    return True
                else:
                    self.logger.log("[RECONCILE] ❌ Fallback market close failed", "ERROR")
            except Exception as me:
                self.logger.log(f"[RECONCILE] Error during fallback market close: {me}", "ERROR")
            return False
        except Exception as e:
            self.logger.log(f"[RECONCILE] Error while reconciling close coverage: {e}", "ERROR")
            return False
            try:
                # Get active orders
                active_orders = await self.exchange_client.get_active_orders(self.config.contract_id)

                # Filter close orders
                self.active_close_orders = []
                for order in active_orders:
                    if order.side == self.config.close_order_side:
                        self.active_close_orders.append({
                            'id': order.order_id,
                            'price': order.price,
                            'size': order.size
                        })

                # Get positions
                position_amt = await self.exchange_client.get_account_positions()

                # Calculate active closing amount
                active_close_amount = sum(
                    Decimal(order.get('size', 0))
                    for order in self.active_close_orders
                    if isinstance(order, dict)
                )

                self.logger.log(f"Current Position: {position_amt} | Active closing amount: {active_close_amount} | "
                                f"Order quantity: {len(self.active_close_orders)}")
                self.last_log_time = time.time()
                
                # Store current position for wait time calculation
                self.current_position = position_amt
                
                # Check for excessive position (more than 20x quantity)
                max_position = self.config.quantity * 20
                if abs(position_amt) > max_position:
                    self.logger.log(f"WARNING: Position too large ({position_amt}), limiting new orders", "WARNING")
                    mismatch_detected = True  # Stop trading if position is too large
                else:
                    mismatch_detected = False

                return mismatch_detected

            except Exception as e:
                self.logger.log(f"Error in periodic status check: {e}", "ERROR")
                self.logger.log(f"Traceback: {traceback.format_exc()}", "ERROR")

            print("--------------------------------")

    async def _meet_grid_step_condition(self) -> bool:
        """Check if new order meets grid step requirement (matches original logic)."""
        if self.active_close_orders:
            picker = min if self.config.direction == "buy" else max
            next_close_order = picker(self.active_close_orders, key=lambda o: o["price"])
            next_close_price = next_close_order["price"]

            # For Lighter, prefer WS BBO for grid-step check; fall back to API if WS invalid
            if self.config.exchange == "lighter":
                try:
                    best_bid, best_ask = await self.exchange_client.fetch_bbo_prices(self.config.contract_id)
                except Exception as e:
                    self.logger.log(f"[GRID] WS BBO unavailable: {e}. Falling back to API.", "WARNING")
                    api_bid, api_ask, _ = await self.exchange_client.fetch_order_book_from_api(int(self.config.contract_id), limit=5)
                    if api_bid and api_ask:
                        best_bid, best_ask = api_bid, api_ask
                    else:
                        raise ValueError("No bid/ask data available from WS or API")
            else:
                best_bid, best_ask = await self.exchange_client.fetch_bbo_prices(self.config.contract_id)
            if best_bid <= 0 or best_ask <= 0 or best_bid >= best_ask:
                raise ValueError("No bid/ask data available")

            if self.config.direction == "buy":
                # BUY direction: open at best_bid, close at higher price (best_bid * (1 + tp))
                # Get current opening price (where we would buy)
                new_open_price = best_bid
                # Calculate where we would close
                new_order_close_price = new_open_price * (1 + self.config.take_profit/100)
                
                # Calculate the distance between new close price and existing close price
                # For BUY: we want next_close_price (existing) - new_order_close_price (new) >= grid_step
                price_diff_percent = abs((next_close_price - new_order_close_price) / new_order_close_price) * 100
                
                self.logger.log(f"[GRID] BUY: open={new_open_price:.5f} new_close={new_order_close_price:.5f} existing_close={next_close_price:.5f} diff={price_diff_percent:.3f}% threshold={self.config.grid_step}%", "INFO")
                
                if price_diff_percent >= self.config.grid_step:
                    self.logger.log(f"[GRID] ✅ OK - Grid step condition met ({price_diff_percent:.3f}% >= {self.config.grid_step}%)", "INFO")
                    return True
                else:
                    self.logger.log(f"[GRID] ❌ SKIP - Too close ({price_diff_percent:.3f}% < {self.config.grid_step}%)", "INFO")
                    return False
            elif self.config.direction == "sell":
                # SELL direction: open at best_ask, close at lower price (best_ask * (1 - tp))
                # Get current opening price (where we would sell)
                new_open_price = best_ask
                # Calculate where we would close
                new_order_close_price = new_open_price * (1 - self.config.take_profit/100)
                
                # Calculate the distance between new close price and existing close price
                # For SELL: we want abs(next_close_price - new_order_close_price) >= grid_step
                price_diff_percent = abs((next_close_price - new_order_close_price) / new_order_close_price) * 100
                
                self.logger.log(f"[GRID] SELL: open={new_open_price:.5f} new_close={new_order_close_price:.5f} existing_close={next_close_price:.5f} diff={price_diff_percent:.3f}% threshold={self.config.grid_step}%", "INFO")
                
                if price_diff_percent >= self.config.grid_step:
                    self.logger.log(f"[GRID] ✅ OK - Grid step condition met ({price_diff_percent:.3f}% >= {self.config.grid_step}%)", "INFO")
                    return True
                else:
                    self.logger.log(f"[GRID] ❌ SKIP - Too close ({price_diff_percent:.3f}% < {self.config.grid_step}%)", "INFO")
                    return False
            else:
                raise ValueError(f"Invalid direction: {self.config.direction}")
        else:
            self.logger.log(f"[GRID] ✅ First order - no grid step check needed", "INFO")
            return True

    async def _check_price_condition(self) -> bool:
        stop_trading = False
        pause_trading = False

        if self.config.pause_price == self.config.stop_price == -1:
            return stop_trading, pause_trading

        best_bid, best_ask = await self.exchange_client.fetch_bbo_prices(self.config.contract_id)
        if best_bid <= 0 or best_ask <= 0 or best_bid >= best_ask:
            raise ValueError("No bid/ask data available")

        if self.config.stop_price != -1:
            if self.config.direction == "buy":
                if best_ask >= self.config.stop_price:
                    stop_trading = True
            elif self.config.direction == "sell":
                if best_bid <= self.config.stop_price:
                    stop_trading = True

        if self.config.pause_price != -1:
            if self.config.direction == "buy":
                if best_ask >= self.config.pause_price:
                    pause_trading = True
            elif self.config.direction == "sell":
                if best_bid <= self.config.pause_price:
                    pause_trading = True

        return stop_trading, pause_trading

    async def send_notification(self, message: str):
        lark_token = os.getenv("LARK_TOKEN")
        if lark_token:
            async with LarkBot(lark_token) as lark_bot:
                await lark_bot.send_text(message)

        telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
        telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if telegram_token and telegram_chat_id:
            with TelegramBot(telegram_token, telegram_chat_id) as tg_bot:
                tg_bot.send_text(message)

    async def run(self):
        """Main trading loop."""
        try:
            self.config.contract_id, self.config.tick_size = await self.exchange_client.get_contract_attributes()

            # Log current TradingConfig
            self.logger.log("=== Trading Configuration ===", "INFO")
            self.logger.log(f"Ticker: {self.config.ticker}", "INFO")
            self.logger.log(f"Contract ID: {self.config.contract_id}", "INFO")
            self.logger.log(f"Quantity: {self.config.quantity}", "INFO")
            self.logger.log(f"Take Profit: {self.config.take_profit}%", "INFO")
            self.logger.log(f"Direction: {self.config.direction}", "INFO")
            self.logger.log(f"Max Orders: {self.config.max_orders}", "INFO")
            self.logger.log(f"Wait Time: {self.config.wait_time}s", "INFO")
            self.logger.log(f"Exchange: {self.config.exchange}", "INFO")
            self.logger.log(f"Grid Step: {self.config.grid_step}%", "INFO")
            self.logger.log(f"Stop Price: {self.config.stop_price}", "INFO")
            self.logger.log(f"Pause Price: {self.config.pause_price}", "INFO")
            self.logger.log(f"Boost Mode: {self.config.boost_mode}", "INFO")
            self.logger.log("=============================", "INFO")

            # Capture the running event loop for thread-safe callbacks
            self.loop = asyncio.get_running_loop()
            # Connect to exchange
            await self.exchange_client.connect()

            # wait for connection to establish
            await asyncio.sleep(5)

            # Main trading loop
            while not self.shutdown_requested:
                # Update active orders
                active_orders = await self.exchange_client.get_active_orders(self.config.contract_id)

                # Filter close orders
                self.active_close_orders = []
                # Handle case when active_orders is None (API error)
                if active_orders is None:
                    self.logger.log("Failed to get active orders, using cached data", "WARNING")
                    active_orders = []
                
                for order in active_orders:
                    if order.side == self.config.close_order_side:
                        self.active_close_orders.append({
                            'id': order.order_id,
                            'price': order.price,
                            'size': order.size
                        })

                # Periodic logging
                mismatch_detected = await self._log_status_periodically()

                stop_trading, pause_trading = await self._check_price_condition()
                if stop_trading:
                    msg = f"\n\nWARNING: [{self.config.exchange.upper()}_{self.config.ticker.upper()}] \n"
                    msg += "Stopped trading due to stop price triggered\n"
                    msg += "价格已经达到停止交易价格，脚本将停止交易\n"
                    await self.send_notification(msg.lstrip())
                    await self.graceful_shutdown(msg)
                    continue

                if pause_trading:
                    await asyncio.sleep(5)
                    continue

                if not mismatch_detected:
                    # Check wait time
                    wait_time = self._calculate_wait_time()

                    if wait_time > 0:
                        await asyncio.sleep(wait_time)
                        continue
                    
                    # Ensure TP coverage first
                    try:
                        placed_topup = await self._reconcile_close_coverage()
                        if placed_topup:
                            # Give exchange a moment to register the new order
                            await asyncio.sleep(1)
                            continue
                    except Exception as e:
                        self.logger.log(f"[RECONCILE] Error: {e}", "ERROR")

                    # Check if we have capacity for new orders
                    if len(self.active_close_orders) < self.config.max_orders:
                        # Check grid step condition
                        if await self._meet_grid_step_condition():
                            await self._place_and_monitor_open_order()
                            self.last_close_orders += 1
                        else:
                            # Grid step not met, wait a bit before checking again
                            await asyncio.sleep(2)
                    else:
                        # If we have max orders, wait a bit
                        await asyncio.sleep(1)

        except KeyboardInterrupt:
            self.logger.log("Bot stopped by user")
            await self.graceful_shutdown("User interruption (Ctrl+C)")
        except Exception as e:
            self.logger.log(f"Critical error: {e}", "ERROR")
            self.logger.log(f"Traceback: {traceback.format_exc()}", "ERROR")
            await self.graceful_shutdown(f"Critical error: {e}")
            raise
        finally:
            # Ensure all connections are closed even if graceful shutdown fails
            try:
                await self.exchange_client.disconnect()
            except Exception as e:
                self.logger.log(f"Error disconnecting from exchange: {e}", "ERROR")
