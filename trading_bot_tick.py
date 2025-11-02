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
    take_profit: Decimal  # Percentage (e.g., 0.05 for 0.05%)
    tick_size: Decimal
    direction: str
    max_orders: int
    wait_time: int
    exchange: str
    grid_step: Decimal  # Percentage (e.g., 0.5 for 0.5%)
    stop_price: Decimal
    pause_price: Decimal
    boost_mode: bool
    take_profit_tick: Optional[int] = None  # Number of ticks (e.g., 5 for 5 ticks)
    grid_step_tick: Optional[int] = None    # Number of ticks (e.g., 10 for 10 ticks)

    @property
    def close_order_side(self) -> str:
        """Get the close order side based on bot direction."""
        return 'buy' if self.direction == "sell" else 'sell'
    
    def use_tick_mode(self) -> bool:
        """Check if using tick-based mode."""
        return self.take_profit_tick is not None and self.grid_step_tick is not None


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
                elif status == "CANCELED" or status == "CANCELED-MARGIN-NOT-ALLOWED" or status == "CANCELED-POST-ONLY":
                    # Handle canceled orders (including those with partial fills)
                    if order_type == "OPEN":
                        self.order_filled_amount = filled_size
                        if self.loop is not None:
                            self.loop.call_soon_threadsafe(self.order_canceled_event.set)
                        else:
                            self.order_canceled_event.set()

                        if self.order_filled_amount > 0:
                            self.logger.log_transaction(order_id, side, self.order_filled_amount, message.get('price'), status)
                    
                    # Handle CLOSE orders with partial fills (important for market order fallback)
                    if order_type == "CLOSE" and filled_size > 0:
                        self.logger.log(f"[{order_type}] [{order_id}] ⚠️ {status} with partial fill: {filled_size} @ {message.get('price')}. Order was partially executed before cancellation.", "WARNING")
                            
                    # PATCH
                    if self.config.exchange == "extended":
                        self.logger.log(f"[{order_type}] [{order_id}] {status} "
                                        f"{Decimal(message.get('size')) - filled_size} @ {message.get('price')}", "INFO")
                    else:
                        # Log with filled_size if it's > 0 to show partial execution
                        if filled_size > 0:
                            self.logger.log(f"[{order_type}] [{order_id}] {status} "
                                            f"{filled_size} filled / {message.get('size')} @ {message.get('price')}", "INFO")
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

            # Poll order status every 1 second for up to 60 seconds (or wait_time if smaller)
            max_polls = min(self.config.wait_time, 60)  # Cap at 60 seconds for polling
            self.logger.log(f"[OPEN] [{order_id}] Polling order status every 1s for up to {max_polls}s", "INFO")
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
        # Reset flag for filled_price tracking
        if hasattr(self, '_filled_price_set'):
            delattr(self, '_filled_price_set')
        
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
                    self.config.close_order_side,
                    reduce_only=True  # ✅ Boost mode is for closing, should be reduce-only
                )
            else:
                self.last_open_order_time = time.time()
                # Place close order
                close_side = self.config.close_order_side
                
                # Phase 1: Try with fixed price calculation (filled_price * (1 ± tp%) or filled_price ± tick_size*ticks)
                self.logger.log(f"[CLOSE] 📊 FULL FILL TP Order Parameters:", "INFO")
                self.logger.log(f"  - filled_quantity: {filled_quantity}", "INFO")
                self.logger.log(f"  - filled_price: {filled_price}", "INFO")
                self.logger.log(f"  - close_side: {close_side}", "INFO")
                if self.config.use_tick_mode():
                    self.logger.log(f"  - take_profit: {self.config.take_profit_tick} ticks", "INFO")
                else:
                    self.logger.log(f"  - take_profit: {self.config.take_profit}%", "INFO")
                
                # Calculate initial close price using fixed formula
                if self.config.use_tick_mode():
                    # Tick-based mode: add/subtract tick_size * number of ticks
                    tick_multiplier = Decimal(self.config.take_profit_tick)
                    if close_side == 'sell':
                        close_price = filled_price + (self.config.tick_size * tick_multiplier)
                    else:
                        close_price = filled_price - (self.config.tick_size * tick_multiplier)
                else:
                    # Percentage-based mode
                    if close_side == 'sell':
                        close_price = filled_price * (1 + self.config.take_profit/100)
                    else:
                        close_price = filled_price * (1 - self.config.take_profit/100)
                
                # Round to tick size for lighter exchange
                if self.config.exchange == "lighter":
                    close_price = Decimal(str(close_price))
                    close_price = self.exchange_client.round_to_tick(close_price)
                
                initial_close_price = close_price
                self.logger.log(f"  - initial calculated close_price (fixed): {close_price}", "INFO")
                
                # Phase 1: Fixed price retries (5 attempts with slight adjustments)
                # Check market price to ensure order won't immediately execute
                try:
                    api_bid, api_ask, _ = await self.exchange_client.fetch_order_book_from_api(int(self.config.contract_id), limit=5)
                except Exception:
                    api_bid, api_ask = None, None
                if api_bid is None:
                    api_bid = await self.exchange_client.get_order_price('buy')
                if api_ask is None:
                    api_ask = await self.exchange_client.get_order_price('sell')
                
                # Ensure buy orders are above best bid, sell orders below best ask
                if close_side == 'buy' and api_bid and close_price <= Decimal(str(api_bid)):
                    self.logger.log(f"[CLOSE] ⚠️ Buy price {close_price} <= best bid {api_bid}, adjusting to {api_bid * Decimal('1.0001')}", "WARNING")
                    close_price = api_bid * Decimal('1.0001')  # Set slightly above best bid
                    if self.config.exchange == "lighter":
                        close_price = self.exchange_client.round_to_tick(close_price)
                elif close_side == 'sell' and api_ask and close_price >= Decimal(str(api_ask)):
                    self.logger.log(f"[CLOSE] ⚠️ Sell price {close_price} >= best ask {api_ask}, adjusting to {api_ask * Decimal('0.9999')}", "WARNING")
                    close_price = api_ask * Decimal('0.9999')  # Set slightly below best ask
                    if self.config.exchange == "lighter":
                        close_price = self.exchange_client.round_to_tick(close_price)
                
                phase1_retries = 5
                close_order_result = None
                for attempt_idx in range(1, phase1_retries + 1):
                    self.logger.log(f"[CLOSE] Phase 1 - Attempt {attempt_idx}/{phase1_retries} (fixed price): {filled_quantity} @ {close_price}", "INFO")

                    close_order_result = await self.exchange_client.place_close_order(
                        self.config.contract_id,
                        filled_quantity,
                        close_price,
                        close_side
                    )
                        
                    # Only verify if API reports success (reduce unnecessary checks)
                    if close_order_result and close_order_result.success:
                        if self.config.exchange == "lighter":
                            # Quick verification with shorter wait
                            await asyncio.sleep(0.5)
                            try:
                                verify_order_id = getattr(close_order_result, 'order_id', None)
                                if verify_order_id:
                                    verify_order = await self.exchange_client.get_order_info(str(verify_order_id))
                                    if verify_order and verify_order.status in ['OPEN', 'PARTIALLY_FILLED']:
                                        self.logger.log(f"[CLOSE] ✅ Successfully placed FULL FILL close order on Phase 1 attempt {attempt_idx} (verified: status={verify_order.status})", "INFO")
                                        break
                                    elif verify_order and verify_order.status in ['CANCELED-POST-ONLY', 'CANCELED']:
                                        self.logger.log(f"[CLOSE] ⚠️ Order {verify_order_id} was {verify_order.status} (POST-ONLY violation)", "WARNING")
                                        close_order_result.success = False
                                        close_order_result.error_message = f"Order was {verify_order.status} (POST-ONLY violation)"
                                    else:
                                        # Unknown status, assume success to avoid blocking
                                        self.logger.log(f"[CLOSE] ✅ Order placed (status={getattr(verify_order, 'status', 'unknown')}), assuming success", "INFO")
                                        break
                                else:
                                    # No order_id, trust API response
                                    break
                            except Exception as ve:
                                self.logger.log(f"[CLOSE] ⚠️ Could not verify order, assuming success: {ve}", "WARNING")
                                break
                        else:
                            # For non-lighter exchanges, trust the API response
                            self.logger.log(f"[CLOSE] ✅ Successfully placed FULL FILL close order on Phase 1 attempt {attempt_idx}", "INFO")
                            break
                    
                    # If failed, adjust price slightly for next attempt
                    if not (close_order_result and close_order_result.success):
                        if attempt_idx < phase1_retries:
                            # Adjust close price: buy orders increase, sell orders decrease
                            if self.config.use_tick_mode():
                                # Tick mode: adjust by 1 tick
                                if close_side == 'sell':
                                    close_price = close_price - self.config.tick_size  # Decrease by 1 tick
                                else:
                                    close_price = close_price + self.config.tick_size  # Increase by 1 tick
                            else:
                                # Percentage mode: adjust by 0.01%
                                if close_side == 'sell':
                                    close_price = close_price * Decimal('0.9999')  # Decrease by 0.01%
                                else:
                                    close_price = close_price * Decimal('1.0001')  # Increase by 0.01%
                            # Round to tick size for lighter exchange
                            if self.config.exchange == "lighter":
                                close_price = self.exchange_client.round_to_tick(close_price)
                            self.logger.log(f"[CLOSE] Retrying with adjusted fixed price: {close_price}", "INFO")
                            await asyncio.sleep(0.3)  # Reduced wait time
                
                # Phase 2: If Phase 1 failed, switch to market-based pricing (ask/bid) - 5 attempts
                if not (close_order_result and close_order_result.success):
                    self.logger.log(f"[CLOSE] ⚠️ Phase 1 (fixed price) failed after {phase1_retries} attempts, switching to Phase 2 (market-based pricing)", "WARNING")
                    
                    # Define market-based pricing function
                    def _compute_price_for_attempt(side: str, k: int, bid: Decimal, ask: Decimal, tp_pct: Decimal) -> Decimal:
                        if self.config.use_tick_mode():
                            # Tick-based mode: add/subtract tick_size * number of ticks * k
                            tick_multiplier = Decimal(self.config.take_profit_tick) * Decimal(k)
                            if side == 'sell':
                                price = ask + (self.config.tick_size * tick_multiplier)
                            else:  # side == 'buy'
                                price = bid - (self.config.tick_size * tick_multiplier)
                        else:
                            # Percentage-based mode: use tp% * k multiplier
                            # For sell orders: use ask price and add tp% to ensure profit (sell higher)
                            # For buy orders: use bid price and subtract tp% to ensure profit (buy lower)
                            if side == 'sell':
                                price = ask * (Decimal('1') + (tp_pct/100) * Decimal(k))
                            else:  # side == 'buy'
                                price = bid * (Decimal('1') - (tp_pct/100) * Decimal(k))
                        return price
                    
                    # Get market price once at start (only refresh every 3 attempts)
                    phase2_retries = 5
                    last_price_refresh = 0
                    for attempt_idx in range(1, phase2_retries + 1):
                        # Refresh price every 2 attempts or on first attempt
                        if attempt_idx == 1 or (attempt_idx - last_price_refresh) >= 2:
                            try:
                                api_bid, api_ask, _ = await self.exchange_client.fetch_order_book_from_api(int(self.config.contract_id), limit=5)
                            except Exception:
                                api_bid, api_ask = None, None
                            # Fallbacks for missing BBO
                            if api_bid is None:
                                api_bid = await self.exchange_client.get_order_price('buy')
                            if api_ask is None:
                                api_ask = await self.exchange_client.get_order_price('sell')
                            last_price_refresh = attempt_idx

                        close_price = _compute_price_for_attempt(close_side, attempt_idx, Decimal(api_bid), Decimal(api_ask), self.config.take_profit)
                        
                        # Round to tick size for lighter exchange
                        if self.config.exchange == "lighter":
                            close_price = self.exchange_client.round_to_tick(close_price)
                        
                        self.logger.log(f"[CLOSE] Phase 2 - Attempt {attempt_idx}/{phase2_retries} (market-based): {filled_quantity} @ {close_price} (api_bid={api_bid}, api_ask={api_ask})", "INFO")

                        close_order_result = await self.exchange_client.place_close_order(
                            self.config.contract_id,
                            filled_quantity,
                            close_price,
                            close_side
                        )
                            
                        # Only verify if API reports success
                        if close_order_result and close_order_result.success:
                            if self.config.exchange == "lighter":
                                await asyncio.sleep(0.5)  # Reduced wait time
                                try:
                                    verify_order_id = getattr(close_order_result, 'order_id', None)
                                    if verify_order_id:
                                        verify_order = await self.exchange_client.get_order_info(str(verify_order_id))
                                        if verify_order and verify_order.status in ['OPEN', 'PARTIALLY_FILLED']:
                                            self.logger.log(f"[CLOSE] ✅ Successfully placed FULL FILL close order on Phase 2 attempt {attempt_idx} (verified: status={verify_order.status})", "INFO")
                                            break
                                        elif verify_order and verify_order.status in ['CANCELED-POST-ONLY', 'CANCELED']:
                                            self.logger.log(f"[CLOSE] ⚠️ Order {verify_order_id} was {verify_order.status} (POST-ONLY violation)", "WARNING")
                                            close_order_result.success = False
                                            close_order_result.error_message = f"Order was {verify_order.status} (POST-ONLY violation)"
                                            if attempt_idx < phase2_retries:
                                                await asyncio.sleep(0.3)
                                                continue
                                            else:
                                                break
                                        else:
                                            # Unknown status, assume success
                                            self.logger.log(f"[CLOSE] ✅ Order placed (status={getattr(verify_order, 'status', 'unknown')}), assuming success", "INFO")
                                            break
                                    else:
                                        break
                                except Exception as ve:
                                    self.logger.log(f"[CLOSE] ⚠️ Could not verify order, assuming success: {ve}", "WARNING")
                                    break
                            else:
                                self.logger.log(f"[CLOSE] ✅ Successfully placed FULL FILL close order on Phase 2 attempt {attempt_idx}", "INFO")
                                break
                        else:
                            error_msg = getattr(close_order_result, 'error_message', 'unknown') if close_order_result else 'Order result is None'
                            self.logger.log(f"[CLOSE] Failed to place FULL FILL close order Phase 2 (attempt {attempt_idx}/{phase2_retries}): {error_msg}", "WARNING")
                            await asyncio.sleep(0.3)  # Reduced wait time
                
                # Fallback: Market order if both phases failed
                if not (close_order_result and close_order_result.success):
                    total_attempts = phase1_retries + phase2_retries
                    self.logger.log(f"[CLOSE] CRITICAL: Failed to place FULL FILL close order after {total_attempts} attempts (Phase 1: {phase1_retries} + Phase 2: {phase2_retries})!", "ERROR")
                    self.logger.log(f"[CLOSE] CRITICAL: Position={filled_quantity} at {filled_price} has NO close order!", "ERROR")
                    if self.config.use_tick_mode():
                        self.logger.log(f"[CLOSE] 💔 All POST-ONLY attempts failed. Phase 1 last price: {initial_close_price}, Phase 2 last price: {close_price}, take_profit={self.config.take_profit_tick} ticks", "ERROR")
                    else:
                        self.logger.log(f"[CLOSE] 💔 All POST-ONLY attempts failed. Phase 1 last price: {initial_close_price}, Phase 2 last price: {close_price}, take_profit={self.config.take_profit}%", "ERROR")
                    # Fallback: use market order to immediately close the position
                    if filled_quantity <= 0:
                        self.logger.log(f"[CLOSE] ⚠️ Skip market order fallback: filled_quantity={filled_quantity} is zero or negative", "WARNING")
                    else:
                        self.logger.log(f"[CLOSE] 🚨 SWITCHING TO MARKET ORDER FALLBACK for {filled_quantity} @ {close_side}", "WARNING")
                        try:
                            market_result = await self.exchange_client.place_market_order(
                                self.config.contract_id,
                                filled_quantity,
                                close_side,
                                reduce_only=True
                            )
                            if market_result and market_result.success:
                                self.logger.log(f"[CLOSE] ✅ Fallback market close succeeded for {filled_quantity} (order_id={getattr(market_result, 'order_id', 'N/A')})", "WARNING")
                                # Clear cached partial fill to avoid reuse
                                self.last_polled_filled_size = Decimal('0')
                            else:
                                self.logger.log(f"[CLOSE] ❌ Fallback market close failed: {getattr(market_result, 'error_message', 'unknown')}", "ERROR")
                        except Exception as me:
                            self.logger.log(f"[CLOSE] Error during fallback market close: {me}", "ERROR")

                # Clear cached partial fill after processing FULL FILL (whether successful or not)
                self.last_polled_filled_size = Decimal('0')
                
                # Log success if TP order was placed
                if close_order_result and close_order_result.success:
                    self.logger.log(f"[CLOSE] ✅ FULL FILL close order processed successfully!", "INFO")
                
                return True

        else:
            new_order_price = await self.exchange_client.get_order_price(self.config.direction)

            def should_wait(direction: str, new_order_price: Decimal, order_result_price: Decimal) -> bool:
                # Only wait if the new price is better than the order price
                # For buy: wait if new price is lower (better to buy)
                # For sell: wait if new price is higher (better to sell)
                # If prices are equal, don't wait (order price is already optimal)
                if direction == "buy":
                    return new_order_price < order_result_price  # Strict <, not <=
                elif direction == "sell":
                    return new_order_price > order_result_price  # Strict >, not >=
                return False

            if self.config.exchange == "lighter":
                current_order_status = self.exchange_client.current_order.status
            else:
                order_info = await self.exchange_client.get_order_info(order_id)
                current_order_status = order_info.status

            # Add timeout mechanism: maximum wait time (e.g., 30 seconds)
            wait_start_time = time.time()
            max_wait_time = 30  # Maximum wait time in seconds
            wait_count = 0
            max_wait_count = 6  # Maximum 6 waits (6 * 5s = 30s)

            while (
                should_wait(self.config.direction, new_order_price, order_result.price)
                and current_order_status == "OPEN"
                and wait_count < max_wait_count
            ):
                self.logger.log(f"[OPEN] [{order_id}] Waiting for order to be filled @ {order_result.price} (wait {wait_count + 1}/{max_wait_count})", "INFO")
                await asyncio.sleep(5)
                wait_count += 1
                
                if self.config.exchange == "lighter":
                    current_order_status = self.exchange_client.current_order.status
                    # Check if order is fully filled
                    if current_order_status in ['FILLED', 'PARTIALLY_FILLED']:
                        filled_size = getattr(self.exchange_client.current_order, 'filled_size', Decimal('0'))
                        if filled_size and abs(Decimal(str(filled_size)) - Decimal(str(self.config.quantity))) <= Decimal('0.01'):
                            self.logger.log(f"[OPEN] [{order_id}] ✅ Order fully filled while waiting: {filled_size}/{self.config.quantity}, exiting wait loop", "INFO")
                            # Use config.quantity to ensure exact match
                            self.order_filled_amount = float(self.config.quantity)
                            break  # Exit loop, order is fully filled
                else:
                    order_info = await self.exchange_client.get_order_info(order_id)
                    if order_info is not None:
                        current_order_status = order_info.status
                        # Check if order is fully filled
                        if current_order_status in ['FILLED', 'PARTIALLY_FILLED']:
                            filled_size = getattr(order_info, 'filled_size', Decimal('0'))
                            if filled_size and abs(Decimal(str(filled_size)) - Decimal(str(self.config.quantity))) <= Decimal('0.01'):
                                self.logger.log(f"[OPEN] [{order_id}] ✅ Order fully filled while waiting: {filled_size}/{self.config.quantity}, exiting wait loop", "INFO")
                                # Use config.quantity to ensure exact match
                                self.order_filled_amount = float(self.config.quantity)
                                break  # Exit loop, order is fully filled
                
                # Update new_order_price for next iteration
                new_order_price = await self.exchange_client.get_order_price(self.config.direction)
            
            if wait_count >= max_wait_count and current_order_status == "OPEN":
                self.logger.log(f"[OPEN] [{order_id}] ⏰ Wait timeout reached ({max_wait_count * 5}s), order still OPEN, will cancel and re-place", "WARNING")

            self.order_canceled_event.clear()
            # Check if order is already filled before attempting to cancel
            if self.config.exchange == "lighter":
                final_status = self.exchange_client.current_order.status
                final_filled = getattr(self.exchange_client.current_order, 'filled_size', Decimal('0'))
            else:
                final_order_info = await self.exchange_client.get_order_info(order_id)
                final_status = final_order_info.status if final_order_info else "UNKNOWN"
                final_filled = getattr(final_order_info, 'filled_size', Decimal('0')) if final_order_info else Decimal('0')
            
            is_fully_filled_check = (final_status in ['FILLED', 'PARTIALLY_FILLED'] and 
                                    final_filled and 
                                    abs(Decimal(str(final_filled)) - Decimal(str(self.config.quantity))) <= Decimal('0.01'))
            
            if is_fully_filled_check:
                self.logger.log(f"[OPEN] [{order_id}] ✅ Order already fully filled: {final_filled}/{self.config.quantity}, skipping cancel", "INFO")
                # Set filled amounts and proceed to close order placement
                # Use config.quantity to ensure exact match (avoid API precision issues)
                self.order_filled_amount = float(self.config.quantity)
                if self.config.exchange == "lighter" and hasattr(self.exchange_client.current_order, 'price'):
                    filled_price = self.exchange_client.current_order.price
                elif not self.config.exchange == "lighter" and final_order_info:
                    filled_price = final_order_info.price
                else:
                    filled_price = order_result.price
                # Mark that filled_price is set
                self._filled_price_set = True
                # Skip cancel logic, go directly to close order placement (will be handled below at line 526)
            else:
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
                                if self.exchange_client.current_order.filled_size > 0:
                                    self.order_filled_amount = self.exchange_client.current_order.filled_size
                                self.logger.log(f"[OPEN] [{order_id}] API query failed after 3 attempts, using WebSocket data: filled_size={self.order_filled_amount}", "WARNING")
                        # If WS 也為 0，但輪詢期間看過部分成交，使用快取救援
                        try:
                            if Decimal(str(self.order_filled_amount)) == 0 and self.last_polled_filled_size > 0:
                                self.order_filled_amount = self.last_polled_filled_size
                                self.logger.log(f"[OPEN] [{order_id}] Using cached partial fill from polling: filled_size={self.order_filled_amount}", "WARNING")
                        except Exception:
                            pass
                        
                        # Check if order is fully filled (should not cancel and re-place)
                        is_fully_filled = abs(Decimal(str(self.order_filled_amount)) - Decimal(str(self.config.quantity))) <= Decimal('0.01')
                        if is_fully_filled:
                            self.logger.log(f"[OPEN] [{order_id}] ✅ Order fully filled: {self.order_filled_amount}/{self.config.quantity}, skipping cancel/replace", "INFO")
                            # Use config.quantity to ensure exact match (avoid API precision issues)
                            self.order_filled_amount = float(self.config.quantity)
                            # Set filled_price for close order placement
                            if finalized:
                                filled_price = finalized.price
                            elif order_info and hasattr(order_info, 'price'):
                                filled_price = order_info.price
                            else:
                                filled_price = order_result.price
                            # Mark that filled_price is set for lighter exchange
                            self._filled_price_set = True
                            # Continue to close order placement logic (will be handled below at line 526)
                        elif self.order_filled_amount > 0:
                            self.logger.log(f"[OPEN] [{order_id}] Partial fill detected: {self.order_filled_amount}/{self.config.quantity}", "WARNING")
                            # Update filled_price to the actual filled price from order_info
                            if finalized and hasattr(finalized, 'price'):
                                filled_price = finalized.price
                                self.logger.log(f"[OPEN] [{order_id}] Using filled price from finalized order: {filled_price}", "INFO")
                            elif order_info and hasattr(order_info, 'price'):
                                filled_price = order_info.price
                                self.logger.log(f"[OPEN] [{order_id}] Using filled price from order_info: {filled_price}", "INFO")
                            else:
                                filled_price = order_result.price
                                self.logger.log(f"[OPEN] [{order_id}] Using order_result price as filled price: {filled_price}", "INFO")
                            # Mark that filled_price is set for lighter exchange
                            self._filled_price_set = True
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
                            # Only update order_filled_amount if it's still 0 (don't overwrite cached partial fill)
                            if self.order_filled_amount == 0:
                                self.order_filled_amount = order_info.filled_size if order_info else 0

            # Only update filled_price if not already set (for lighter exchange, it's set above)
            if self.order_filled_amount > 0 and not hasattr(self, '_filled_price_set'):
                self.logger.log(f"[OPEN] [{order_id}] Partial fill detected: {self.order_filled_amount}/{self.config.quantity}", "WARNING")
                # Update filled_price to the actual filled price from cancel_result (for non-lighter exchanges)
                if self.config.exchange != "lighter" and 'cancel_result' in locals() and hasattr(cancel_result, 'price') and cancel_result.price:
                    filled_price = cancel_result.price
                    self.logger.log(f"[OPEN] [{order_id}] Using filled price from cancel_result: {filled_price}", "INFO")
                elif not hasattr(self, '_filled_price_set'):
                    # filled_price should already be set above, but log it for reference
                    self.logger.log(f"[OPEN] [{order_id}] Using filled price: {filled_price}", "INFO")

            if self.order_filled_amount > 0:
                close_side = self.config.close_order_side
                
                # Check current position before placing TP order to avoid duplicate processing
                try:
                    current_position = await self.exchange_client.get_account_positions()
                    self.logger.log(f"[CLOSE] Current position before TP: {current_position}, order_filled_amount: {self.order_filled_amount}", "INFO")
                    
                    # For sell direction (direction=sell): position should be negative (short), close_side=buy
                    # For buy direction (direction=buy): position should be positive (long), close_side=sell
                    # If position doesn't match expected direction, the fill may have already been processed
                    if close_side == 'buy':  # Closing short position (direction=sell, position should be negative)
                        if current_position >= 0:
                            self.logger.log(f"[CLOSE] ⚠️ Position {current_position} >= 0, but trying to close short. This fill may have already been processed. Skipping TP order.", "WARNING")
                            # Clear cache to avoid reuse
                            self.last_polled_filled_size = Decimal('0')
                            self.order_filled_amount = 0
                            return True
                    else:  # close_side == 'sell', closing long position (direction=buy, position should be positive)
                        if current_position <= 0:
                            self.logger.log(f"[CLOSE] ⚠️ Position {current_position} <= 0, but trying to close long. This fill may have already been processed. Skipping TP order.", "WARNING")
                            # Clear cache to avoid reuse
                            self.last_polled_filled_size = Decimal('0')
                            self.order_filled_amount = 0
                            return True
                except Exception as pos_check_error:
                    self.logger.log(f"[CLOSE] ⚠️ Could not check position, proceeding with TP order: {pos_check_error}", "WARNING")
                
                # Check if fully filled or partially filled
                is_fully_filled_status = abs(Decimal(str(self.order_filled_amount)) - Decimal(str(self.config.quantity))) <= Decimal('0.01')
                if is_fully_filled_status:
                    self.logger.log(f"[CLOSE] 🎯 FULL FILL DETECTED: {self.order_filled_amount}/{self.config.quantity} @ {filled_price}", "INFO")
                    self.logger.log(f"[CLOSE] Creating REDUCE-ONLY + POST-ONLY close order for full fill", "INFO")
                else:
                    self.logger.log(f"[CLOSE] 🎯 PARTIAL FILL DETECTED: {self.order_filled_amount}/{self.config.quantity} @ {filled_price}", "WARNING")
                    self.logger.log(f"[CLOSE] Creating REDUCE-ONLY + POST-ONLY close order for partial fill", "INFO")
                
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
                    # Phase 1: Try with fixed price calculation (filled_price * (1 ± tp%) or filled_price ± tick_size*ticks)
                    # Calculate initial close price using fixed formula
                    if self.config.use_tick_mode():
                        # Tick-based mode: add/subtract tick_size * number of ticks
                        tick_multiplier = Decimal(self.config.take_profit_tick)
                        if close_side == 'sell':
                            close_price = filled_price + (self.config.tick_size * tick_multiplier)
                        else:
                            close_price = filled_price - (self.config.tick_size * tick_multiplier)
                    else:
                        # Percentage-based mode
                        if close_side == 'sell':
                            close_price = filled_price * (1 + self.config.take_profit/100)
                        else:
                            close_price = filled_price * (1 - self.config.take_profit/100)
                    
                    # Round to tick size for lighter exchange
                    if self.config.exchange == "lighter":
                        close_price = Decimal(str(close_price))
                        close_price = self.exchange_client.round_to_tick(close_price)
                    
                    initial_close_price = close_price
                    
                    # Define market-based pricing function for Phase 2
                    def _compute_price_for_attempt(side: str, k: int, bid: Decimal, ask: Decimal, tp_pct: Decimal) -> Decimal:
                        if self.config.use_tick_mode():
                            # Tick-based mode: add/subtract tick_size * number of ticks * k
                            tick_multiplier = Decimal(self.config.take_profit_tick) * Decimal(k)
                            if side == 'sell':
                                price = ask + (self.config.tick_size * tick_multiplier)
                            else:  # side == 'buy'
                                price = bid - (self.config.tick_size * tick_multiplier)
                        else:
                            # Percentage-based mode: use tp% * k multiplier
                            # For sell orders: use ask price and add tp% to ensure profit (sell higher)
                            # For buy orders: use bid price and subtract tp% to ensure profit (buy lower)
                            if side == 'sell':
                                price = ask * (Decimal('1') + (tp_pct/100) * Decimal(k))
                            else:  # side == 'buy'
                                price = bid * (Decimal('1') - (tp_pct/100) * Decimal(k))
                        # Log detailed parameters for debugging
                        self.logger.log(f"[CLOSE] 💡 Price calculation params: side={side}, k={k}, bid={bid}, ask={ask}, tp_pct={tp_pct}, calculated_price={price}", "INFO")
                        return price
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

                    # Phase 1: Fixed price retries (5 attempts with slight adjustments)
                    # Check market price to ensure order won't immediately execute
                    try:
                        api_bid, api_ask, _ = await self.exchange_client.fetch_order_book_from_api(int(self.config.contract_id), limit=5)
                    except Exception:
                        api_bid, api_ask = None, None
                    if api_bid is None:
                        api_bid = await self.exchange_client.get_order_price('buy')
                    if api_ask is None:
                        api_ask = await self.exchange_client.get_order_price('sell')
                    
                    # Ensure buy orders are above best bid, sell orders below best ask
                    if close_side == 'buy' and api_bid and close_price <= Decimal(str(api_bid)):
                        self.logger.log(f"[CLOSE] ⚠️ Buy price {close_price} <= best bid {api_bid}, adjusting to {api_bid * Decimal('1.0001')}", "WARNING")
                        close_price = api_bid * Decimal('1.0001')  # Set slightly above best bid
                        if self.config.exchange == "lighter":
                            close_price = self.exchange_client.round_to_tick(close_price)
                    elif close_side == 'sell' and api_ask and close_price >= Decimal(str(api_ask)):
                        self.logger.log(f"[CLOSE] ⚠️ Sell price {close_price} >= best ask {api_ask}, adjusting to {api_ask * Decimal('0.9999')}", "WARNING")
                        close_price = api_ask * Decimal('0.9999')  # Set slightly below best ask
                        if self.config.exchange == "lighter":
                            close_price = self.exchange_client.round_to_tick(close_price)
                    
                    phase1_retries = 5
                    close_order_result = None
                    self.logger.log(f"[CLOSE] 📊 PARTIAL FILL TP Order Parameters:", "INFO")
                    self.logger.log(f"  - order_filled_amount: {self.order_filled_amount}", "INFO")
                    self.logger.log(f"  - filled_price: {filled_price}", "INFO")
                    self.logger.log(f"  - close_side: {close_side}", "INFO")
                    if self.config.use_tick_mode():
                        self.logger.log(f"  - take_profit: {self.config.take_profit_tick} ticks", "INFO")
                    else:
                        self.logger.log(f"  - take_profit: {self.config.take_profit}%", "INFO")
                    self.logger.log(f"  - initial calculated close_price (fixed): {close_price}", "INFO")
                    
                    for attempt_idx in range(1, phase1_retries + 1):
                        self.logger.log(f"[CLOSE] Phase 1 - Attempt {attempt_idx}/{phase1_retries} (fixed price): {self.order_filled_amount} @ {close_price}", "INFO")

                        close_order_result = await self.exchange_client.place_close_order(
                            self.config.contract_id,
                            self.order_filled_amount,
                            close_price,
                            close_side
                        )
                            
                        # Only verify if API reports success (reduce unnecessary checks)
                        if close_order_result and close_order_result.success:
                            if self.config.exchange == "lighter":
                                # Quick verification with shorter wait
                                await asyncio.sleep(0.5)
                                try:
                                    verify_order_id = getattr(close_order_result, 'order_id', None)
                                    if verify_order_id:
                                        verify_order = await self.exchange_client.get_order_info(str(verify_order_id))
                                        if verify_order and verify_order.status in ['OPEN', 'PARTIALLY_FILLED']:
                                            self.logger.log(f"[CLOSE] ✅ Successfully placed PARTIAL FILL close order on Phase 1 attempt {attempt_idx} (verified: status={verify_order.status})", "INFO")
                                            break
                                        elif verify_order and verify_order.status in ['CANCELED-POST-ONLY', 'CANCELED']:
                                            self.logger.log(f"[CLOSE] ⚠️ Order {verify_order_id} was {verify_order.status} (POST-ONLY violation)", "WARNING")
                                            close_order_result.success = False
                                            close_order_result.error_message = f"Order was {verify_order.status} (POST-ONLY violation)"
                                        else:
                                            # Unknown status, assume success to avoid blocking
                                            self.logger.log(f"[CLOSE] ✅ Order placed (status={getattr(verify_order, 'status', 'unknown')}), assuming success", "INFO")
                                            break
                                    else:
                                        # No order_id, trust API response
                                        break
                                except Exception as ve:
                                    self.logger.log(f"[CLOSE] ⚠️ Could not verify order, assuming success: {ve}", "WARNING")
                                    break
                            else:
                                self.logger.log(f"[CLOSE] ✅ Successfully placed PARTIAL FILL close order on Phase 1 attempt {attempt_idx}", "INFO")
                                break
                        
                        # If failed, adjust price slightly for next attempt
                        if not (close_order_result and close_order_result.success):
                            if attempt_idx < phase1_retries:
                                # Adjust close price: buy orders increase, sell orders decrease
                                if self.config.use_tick_mode():
                                    # Tick mode: adjust by 1 tick
                                    if close_side == 'sell':
                                        close_price = close_price - self.config.tick_size  # Decrease by 1 tick
                                    else:
                                        close_price = close_price + self.config.tick_size  # Increase by 1 tick
                                else:
                                    # Percentage mode: adjust by 0.01%
                                    if close_side == 'sell':
                                        close_price = close_price * Decimal('0.9999')  # Decrease by 0.01%
                                    else:
                                        close_price = close_price * Decimal('1.0001')  # Increase by 0.01%
                                # Round to tick size for lighter exchange
                                if self.config.exchange == "lighter":
                                    close_price = self.exchange_client.round_to_tick(close_price)
                                self.logger.log(f"[CLOSE] Retrying with adjusted fixed price: {close_price}", "INFO")
                                await asyncio.sleep(0.3)  # Reduced wait time
                    
                    # Phase 2: If Phase 1 failed, switch to market-based pricing (ask/bid) - 5 attempts
                    if not (close_order_result and close_order_result.success):
                        self.logger.log(f"[CLOSE] ⚠️ Phase 1 (fixed price) failed after {phase1_retries} attempts, switching to Phase 2 (market-based pricing)", "WARNING")
                        
                        # Get market price once at start (only refresh every 2 attempts)
                        phase2_retries = 5
                        last_price_refresh = 0
                        for attempt_idx in range(1, phase2_retries + 1):
                            # Refresh price every 2 attempts or on first attempt
                            if attempt_idx == 1 or (attempt_idx - last_price_refresh) >= 2:
                                try:
                                    api_bid, api_ask, _ = await self.exchange_client.fetch_order_book_from_api(int(self.config.contract_id), limit=5)
                                except Exception:
                                    api_bid, api_ask = None, None
                                # Fallbacks for missing BBO
                                if api_bid is None:
                                    api_bid = await self.exchange_client.get_order_price('buy')
                                if api_ask is None:
                                    api_ask = await self.exchange_client.get_order_price('sell')
                                last_price_refresh = attempt_idx

                            close_price = _compute_price_for_attempt(close_side, attempt_idx, Decimal(api_bid), Decimal(api_ask), self.config.take_profit)
                            
                            # Round to tick size for lighter exchange
                            if self.config.exchange == "lighter":
                                close_price = self.exchange_client.round_to_tick(close_price)
                            
                            self.logger.log(f"[CLOSE] Phase 2 - Attempt {attempt_idx}/{phase2_retries} (market-based): {self.order_filled_amount} @ {close_price} (api_bid={api_bid}, api_ask={api_ask})", "INFO")

                            close_order_result = await self.exchange_client.place_close_order(
                                self.config.contract_id,
                                self.order_filled_amount,
                                close_price,
                                close_side
                            )
                                
                            # Only verify if API reports success
                            if close_order_result and close_order_result.success:
                                if self.config.exchange == "lighter":
                                    await asyncio.sleep(0.5)  # Reduced wait time
                                    try:
                                        verify_order_id = getattr(close_order_result, 'order_id', None)
                                        if verify_order_id:
                                            verify_order = await self.exchange_client.get_order_info(str(verify_order_id))
                                            if verify_order and verify_order.status in ['OPEN', 'PARTIALLY_FILLED']:
                                                self.logger.log(f"[CLOSE] ✅ Successfully placed PARTIAL FILL close order on Phase 2 attempt {attempt_idx} (verified: status={verify_order.status})", "INFO")
                                                break
                                            elif verify_order and verify_order.status in ['CANCELED-POST-ONLY', 'CANCELED']:
                                                self.logger.log(f"[CLOSE] ⚠️ Order {verify_order_id} was {verify_order.status} (POST-ONLY violation)", "WARNING")
                                                close_order_result.success = False
                                                close_order_result.error_message = f"Order was {verify_order.status} (POST-ONLY violation)"
                                                if attempt_idx < phase2_retries:
                                                    await asyncio.sleep(0.3)
                                                    continue
                                                else:
                                                    break
                                            else:
                                                # Unknown status, assume success
                                                self.logger.log(f"[CLOSE] ✅ Order placed (status={getattr(verify_order, 'status', 'unknown')}), assuming success", "INFO")
                                                break
                                        else:
                                            break
                                    except Exception as ve:
                                        self.logger.log(f"[CLOSE] ⚠️ Could not verify order, assuming success: {ve}", "WARNING")
                                        break
                                else:
                                    self.logger.log(f"[CLOSE] ✅ Successfully placed PARTIAL FILL close order on Phase 2 attempt {attempt_idx}", "INFO")
                                    break
                            else:
                                error_msg = getattr(close_order_result, 'error_message', 'unknown') if close_order_result else 'Order result is None'
                                self.logger.log(f"[CLOSE] Failed to place PARTIAL FILL close order Phase 2 (attempt {attempt_idx}/{phase2_retries}): {error_msg}", "WARNING")
                                await asyncio.sleep(0.3)  # Reduced wait time
                    
                    # Fallback: Market order if both phases failed
                    if not (close_order_result and close_order_result.success):
                        total_attempts = phase1_retries + phase2_retries
                        self.logger.log(f"[CLOSE] CRITICAL: Failed to place PARTIAL FILL close order after {total_attempts} attempts (Phase 1: {phase1_retries} + Phase 2: {phase2_retries})!", "ERROR")
                        self.logger.log(f"[CLOSE] CRITICAL: Partial position={self.order_filled_amount} at {filled_price} has NO close order!", "ERROR")
                        if self.config.use_tick_mode():
                            self.logger.log(f"[CLOSE] 💔 All POST-ONLY attempts failed. Phase 1 last price: {initial_close_price}, Phase 2 last price: {close_price}, take_profit={self.config.take_profit_tick} ticks", "ERROR")
                        else:
                            self.logger.log(f"[CLOSE] 💔 All POST-ONLY attempts failed. Phase 1 last price: {initial_close_price}, Phase 2 last price: {close_price}, take_profit={self.config.take_profit}%", "ERROR")
                        # Fallback: use market order to immediately reduce the imbalance
                        if self.order_filled_amount <= 0:
                            self.logger.log(f"[CLOSE] ⚠️ Skip market order fallback: order_filled_amount={self.order_filled_amount} is zero or negative", "WARNING")
                        else:
                            self.logger.log(f"[CLOSE] 🚨 SWITCHING TO MARKET ORDER FALLBACK for {self.order_filled_amount} @ {close_side}", "WARNING")
                            try:
                                market_result = await self.exchange_client.place_market_order(
                                    self.config.contract_id,
                                    self.order_filled_amount,
                                    close_side,
                                    reduce_only=True
                                )
                                if market_result and market_result.success:
                                    self.logger.log(f"[CLOSE] ✅ Fallback market close succeeded for {self.order_filled_amount} (order_id={getattr(market_result, 'order_id', 'N/A')})", "WARNING")
                                else:
                                    self.logger.log(f"[CLOSE] ❌ Fallback market close failed: {getattr(market_result, 'error_message', 'unknown')}", "ERROR")
                            except Exception as me:
                                self.logger.log(f"[CLOSE] Error during fallback market close: {me}", "ERROR")

                self.last_open_order_time = time.time()
                if close_order_result and not close_order_result.success:
                    self.logger.log(f"[CLOSE] Failed to place partial fill close order: {close_order_result.error_message}", "ERROR")
                elif close_order_result and close_order_result.success:
                    self.logger.log(f"[CLOSE] ✅ Partial fill close order placed successfully!", "INFO")
                    # Clear cached partial fill to avoid reuse in future orders
                    self.last_polled_filled_size = Decimal('0')
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

            # Determine close side by actual position sign to reduce position to zero
            # If position > 0 (long), use 'sell' to close
            # If position < 0 (short), use 'buy' to close
            # This ensures we always reduce position regardless of user's direction setting
            close_side = 'sell' if position_amt > 0 else 'buy'
            
            # Fetch active orders and sum close-side sizes (use actual close_side based on position)
            active_orders = await self.exchange_client.get_active_orders(self.config.contract_id)
            active_close_amount = sum(
                Decimal(getattr(o, 'size', 0)) if not isinstance(o, dict) else Decimal(o.get('size', 0))
                for o in active_orders
                if (getattr(o, 'side', None) == close_side) or (isinstance(o, dict) and o.get('side') == close_side)
            )
            
            # Warn if position sign doesn't match user's direction setting
            expected_position_sign = -1 if self.config.direction == 'sell' else 1  # sell=short(negative), buy=long(positive)
            if (position_amt > 0 and expected_position_sign < 0) or (position_amt < 0 and expected_position_sign > 0):
                self.logger.log(f"[RECONCILE] ⚠️ WARNING: Position={position_amt} has opposite sign from direction={self.config.direction}. Expected {'negative' if expected_position_sign < 0 else 'positive'} but got {'positive' if position_amt > 0 else 'negative'}. Will use close_side={close_side} to reduce position to zero.", "WARNING")
            required_close = abs(position_amt)
            if active_close_amount >= required_close:
                # Has sufficient orders to cover position - this is good, not a failure
                self.logger.log(f"[RECONCILE] ✅ Sufficient coverage: Position={position_amt}, ActiveClose={active_close_amount} >= Required={required_close}. Orders are covering position.", "INFO")
                return False  # Return False but this is success case, not failure
            
            deficit = (required_close - active_close_amount).quantize(Decimal('0.00000001'))
            if deficit <= 0:
                self.logger.log(f"[RECONCILE] ✅ Sufficient coverage: Position={position_amt}, ActiveClose={active_close_amount}, Deficit={deficit} <= 0. Orders are covering position.", "INFO")
                return False  # Return False but this is success case, not failure

            # Duplicate-suppression: avoid placing the same reconcile twice within a short window
            now_ts = time.time()
            deficit_signature = f"{close_side}:{deficit}"
            last_sig = getattr(self, "_last_reconcile_signature", None)
            last_ts = getattr(self, "_last_reconcile_time", 0)
            # Use longer timeout (30s) if last attempt was for the same deficit (likely failed)
            timeout_window = 30 if (last_sig == deficit_signature) else 5
            if last_sig == deficit_signature and (now_ts - last_ts) < timeout_window:
                self.logger.log(f"[RECONCILE] Skip duplicate within {timeout_window}s window for {deficit_signature}", "INFO")
                return False

            # Define pricing function: attempt k in [1..5]
            # For sell orders: use ask price and add tp% to ensure profit (sell higher)
            # For buy orders: use bid price and subtract tp% to ensure profit (buy lower)
            #   sell: price_k = ask1 * (1 + k*tp%)
            #   buy:  price_k = bid1 * (1 - k*tp%)
            def _reconcile_price_for_attempt(side: str, k: int, bid: Decimal, ask: Decimal, tp_pct: Decimal) -> Decimal:
                if self.config.use_tick_mode():
                    # Tick-based mode: add/subtract tick_size * number of ticks * k
                    tick_multiplier = Decimal(self.config.take_profit_tick) * Decimal(k)
                    if side == 'sell':
                        return ask + (self.config.tick_size * tick_multiplier)
                    else:  # side == 'buy'
                        return bid - (self.config.tick_size * tick_multiplier)
                else:
                    # Percentage-based mode
                    if side == 'sell':
                        return ask * (Decimal('1') + (tp_pct/100) * Decimal(k))
                    else:  # side == 'buy'
                        return bid * (Decimal('1') - (tp_pct/100) * Decimal(k))

            # Pre-log high-level action
            self.logger.log(f"[RECONCILE] Position={position_amt}, ActiveClose={active_close_amount} → Deficit={deficit}.", "WARNING")

            # Skip if a similar close already exists (API may have lagged earlier)
            # Note: We must check both size AND status (OPEN/PARTIALLY_FILLED only)
            try:
                active_orders = await self.exchange_client.get_active_orders(self.config.contract_id)
                for o in active_orders:
                    if o.side != close_side:
                        continue
                    # Check order status - only count OPEN or PARTIALLY_FILLED orders
                    order_status = getattr(o, 'status', 'UNKNOWN').upper()
                    if order_status not in ['OPEN', 'PARTIALLY_FILLED']:
                        self.logger.log(f"[RECONCILE] Found order with invalid status: size={o.size} price={o.price} status={order_status}, ignoring", "WARNING")
                        continue
                    size_close_enough = abs(Decimal(o.size) - deficit) <= max(Decimal('0.1'), deficit * Decimal('0.01'))
                    if size_close_enough:
                        self.logger.log(f"[RECONCILE] Found similar TP: size={o.size} price={o.price} status={order_status}", "INFO")
                        # Re-verify after brief delay to avoid API lag false positives
                        await asyncio.sleep(2)
                        active_orders_2 = await self.exchange_client.get_active_orders(self.config.contract_id)
                        exists_after = any(
                            (ao.side == close_side) and (
                                getattr(ao, 'status', 'UNKNOWN').upper() in ['OPEN', 'PARTIALLY_FILLED']
                            ) and (
                                abs(Decimal(ao.size) - deficit) <= max(Decimal('0.1'), deficit * Decimal('0.01'))
                            ) for ao in active_orders_2
                        )
                        if exists_after:
                            self.logger.log(f"[RECONCILE] ✅ Verified: similar TP still exists after re-check, skipping", "INFO")
                            return False
                        else:
                            self.logger.log("[RECONCILE] ⚠️ Re-check found no similar TP (may have been canceled), will place now", "WARNING")
                        break
            except Exception as e:
                self.logger.log(f"[RECONCILE] Error checking for similar TP: {e}", "WARNING")
                pass

            # Retry logic up to 5 attempts using k*tp% pricing against opponent best
            max_retries = 5
            post_only_failures = 0  # Track consecutive POST-ONLY cancellations
            for attempt_idx in range(1, max_retries + 1):
                # Refresh BBO each attempt
                try:
                    api_bid, api_ask, _ = await self.exchange_client.fetch_order_book_from_api(int(self.config.contract_id), limit=5)
                except Exception:
                    api_bid, api_ask = None, None
                if api_bid is None:
                    api_bid = await self.exchange_client.get_order_price('buy')
                if api_ask is None:
                    api_ask = await self.exchange_client.get_order_price('sell')

                close_price = _reconcile_price_for_attempt(close_side, attempt_idx, Decimal(api_bid), Decimal(api_ask), self.config.take_profit)
                
                # Round to tick size for lighter exchange
                if self.config.exchange == "lighter":
                    close_price = self.exchange_client.round_to_tick(close_price)
                
                self.logger.log(f"[RECONCILE] Attempt {attempt_idx}/{max_retries} RO+PO: {deficit} @ {close_price}", "INFO")

                result = await self.exchange_client.place_close_order(
                    self.config.contract_id,
                    deficit,
                    close_price,
                    close_side
                )
                if self.config.exchange == 'lighter':
                    await asyncio.sleep(1)

                if result.success:
                    placed_order_id = getattr(result, 'order_id', None)
                    self.logger.log(f"[RECONCILE] ✅ API returned success for order {deficit} @ {close_price} on attempt {attempt_idx} (order_id={placed_order_id})", "INFO")
                    # Verify presence using order_id if available, otherwise fallback to size/price match
                    try:
                        # Wait longer for exchange to process (POST-ONLY cancellations may take time to appear)
                        # Increased wait time to allow exchange to process and update order status
                        await asyncio.sleep(5)
                        if placed_order_id:
                            # Direct verification by order_id with multiple retries
                            order_info = None
                            verification_retries = 2
                            for verify_attempt in range(verification_retries):
                                order_info = await self.exchange_client.get_order_info(str(placed_order_id))
                                
                                if order_info:
                                    break  # Found order, exit retry loop
                                
                                if verify_attempt < verification_retries - 1:
                                    wait_time = 3 if verify_attempt == 0 else 5  # Progressive wait: 3s then 5s
                                    self.logger.log(f"[RECONCILE] Order {placed_order_id} not found on attempt {verify_attempt + 1}/{verification_retries}, waiting {wait_time}s...", "WARNING")
                                    await asyncio.sleep(wait_time)
                            
                            if order_info and order_info.status in ['OPEN', 'PARTIALLY_FILLED']:
                                self.logger.log(f"[RECONCILE] ✅ Verified: order {placed_order_id} exists with status={order_info.status}", "INFO")
                                self._last_reconcile_signature = deficit_signature
                                self._last_reconcile_time = time.time()
                                return True
                            elif order_info:
                                # Order found but not in OPEN/PARTIALLY_FILLED state
                                status_str = str(order_info.status).upper()
                                self.logger.log(f"[RECONCILE] Order {placed_order_id} found with status={status_str}", "WARNING")
                                
                                # Check if it was canceled due to POST-ONLY violation
                                if 'POST-ONLY' in status_str or 'POST_ONLY' in status_str or 'CANCELED' in status_str:
                                    post_only_failures += 1
                                    self.logger.log(f"[RECONCILE] ⚠️ Order {placed_order_id} was CANCELED (likely POST-ONLY violation), consecutive failures: {post_only_failures}/3", "WARNING")
                                    # If 3 consecutive POST-ONLY failures, skip to market order immediately
                                    if post_only_failures >= 3:
                                        self.logger.log(f"[RECONCILE] ⚠️ Too many POST-ONLY failures ({post_only_failures}), switching to market order fallback", "WARNING")
                                        break
                                elif 'MARGIN' in status_str:
                                    self.logger.log(f"[RECONCILE] ⚠️ Order {placed_order_id} was CANCELED-MARGIN-NOT-ALLOWED", "WARNING")
                                    # MARGIN error usually means we can't place the order at all, skip to market order
                                    self.logger.log(f"[RECONCILE] ⚠️ MARGIN error detected, switching to market order fallback", "WARNING")
                                    break
                                else:
                                    self.logger.log(f"[RECONCILE] ⚠️ Order {placed_order_id} verification failed: status={status_str}", "WARNING")
                                # continue to next attempt
                            else:
                                # Still not found after multiple retries - check if position decreased (order may have filled immediately)
                                current_position = await self.exchange_client.get_account_positions()
                                position_decreased = abs(current_position) < abs(position_amt)
                                
                                if position_decreased:
                                    # Position decreased even though order not found - order likely filled immediately
                                    position_change = abs(position_amt) - abs(current_position)
                                    self.logger.log(f"[RECONCILE] ✅ Order {placed_order_id} not found but position decreased: {position_amt} → {current_position} (filled ~{position_change}), treating as success", "INFO")
                                    # Update deficit signature with new position for next iteration
                                    remaining_position = abs(current_position)
                                    if remaining_position > 0:
                                        # Still has remaining position, update signature to allow retry for remaining
                                        self._last_reconcile_signature = f"{close_side}:{remaining_position}"
                                        self._last_reconcile_time = time.time()
                                    else:
                                        # Position fully closed
                                        self._last_reconcile_signature = deficit_signature
                                        self._last_reconcile_time = time.time()
                                    return True  # Treat as success since position decreased
                                else:
                                    # Position unchanged - likely POST-ONLY cancel that hasn't appeared in API yet
                                    self.logger.log(f"[RECONCILE] ⚠️ Order {placed_order_id} verification failed: NOT_FOUND after {verification_retries} attempts (likely canceled immediately by POST-ONLY)", "WARNING")
                                    # Treat NOT_FOUND as POST-ONLY failure (common when price too close to market)
                                    post_only_failures += 1
                                    self.logger.log(f"[RECONCILE] ⚠️ Counting NOT_FOUND as POST-ONLY failure, consecutive failures: {post_only_failures}/3", "WARNING")
                                    if post_only_failures >= 3:
                                        self.logger.log(f"[RECONCILE] ⚠️ Multiple orders not found ({post_only_failures} consecutive), assuming POST-ONLY cancellations, switching to market order", "WARNING")
                                        break
                                # continue to next attempt
                        else:
                            # Fallback: verify by size/price match if no order_id
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
                                self.logger.log(f"[RECONCILE] ✅ Verified by size/price match", "INFO")
                                self._last_reconcile_signature = deficit_signature
                                self._last_reconcile_time = time.time()
                                return True
                            else:
                                self.logger.log("[RECONCILE] ⚠️ Verification could not find the new TP; retrying placement", "WARNING")
                                # continue to next attempt
                    except Exception as ve:
                        self.logger.log(f"[RECONCILE] Exception during verification: {ve}", "WARNING")
                        # If verification fails, assume success to avoid infinite retry (but record signature)
                        self._last_reconcile_signature = deficit_signature
                        self._last_reconcile_time = time.time()
                        return True
                else:
                    self.logger.log(f"[RECONCILE] Failed attempt {attempt_idx}/{max_retries}: {getattr(result, 'error_message', 'unknown')}", "WARNING")
                    await asyncio.sleep(1)

            self.logger.log("[RECONCILE] ❌ Failed to place top-up close order after retries", "ERROR")
            # Fallback to market order to quickly resolve imbalance
            # Validate quantity before placing market order
            if deficit <= 0:
                self.logger.log(f"[RECONCILE] ⚠️ Skip market order fallback: deficit={deficit} is zero or negative", "WARNING")
                return False
            
            try:
                market_result = await self.exchange_client.place_market_order(
                    self.config.contract_id,
                    deficit,
                    close_side,
                    reduce_only=True  # ✅ Ensure market order is reduce-only to avoid opening new position
                )
                if market_result and market_result.success:
                    market_order_id = getattr(market_result, 'order_id', None)
                    self.logger.log(f"[RECONCILE] ✅ Fallback market close API returned success for deficit {deficit} (order_id={market_order_id})", "WARNING")
                    
                    # Wait and check actual order status (market orders may be immediately canceled or partially filled)
                    await asyncio.sleep(2)
                    if market_order_id:
                        try:
                            order_info = await self.exchange_client.get_order_info(str(market_order_id))
                            if order_info:
                                filled_amount = getattr(order_info, 'filled_size', Decimal('0'))
                                if order_info.status in ['CANCELED', 'CANCELED-MARGIN-NOT-ALLOWED', 'REJECTED']:
                                    if filled_amount > 0:
                                        # Partially filled before cancellation - check if position decreased
                                        self.logger.log(f"[RECONCILE] ⚠️ Market order {market_order_id} was {order_info.status} but partially filled {filled_amount}. Checking position...", "WARNING")
                                    else:
                                        self.logger.log(f"[RECONCILE] ❌ CRITICAL: Market order {market_order_id} was {order_info.status} with no fill. Position not closed.", "ERROR")
                                        # Record signature with longer timeout to prevent rapid retry (30s instead of 5s)
                                        self._last_reconcile_signature = deficit_signature
                                        self._last_reconcile_time = time.time()
                                        await self.send_notification(f"CRITICAL: Market close order {market_order_id} was {order_info.status}. Position {deficit} remains unclosed.")
                                        return False
                        except Exception as e:
                            self.logger.log(f"[RECONCILE] Could not verify market order status: {e}", "WARNING")
                    
                    # Verify position actually decreased to avoid infinite loop
                    await asyncio.sleep(2)  # Additional wait for position update
                    new_position = await self.exchange_client.get_account_positions()
                    if abs(new_position) < abs(position_amt):
                        self.logger.log(f"[RECONCILE] ✅ Position verified decreased: {position_amt} → {new_position}", "INFO")
                        # Record signature to prevent immediate retry
                        self._last_reconcile_signature = deficit_signature
                        self._last_reconcile_time = time.time()
                        return True
                    else:
                        self.logger.log(f"[RECONCILE] ⚠️ WARNING: Market order API success but position unchanged: {position_amt} → {new_position}. Order may have been canceled.", "WARNING")
                        # Record signature with longer timeout (30s) to prevent rapid retry
                        self._last_reconcile_signature = deficit_signature
                        self._last_reconcile_time = time.time()
                        await self.send_notification(f"WARNING: Market close order succeeded but position unchanged: {position_amt} → {new_position}")
                        return False  # Return False so caller knows it didn't fully resolve
                else:
                    self.logger.log(f"[RECONCILE] ❌ Fallback market close failed: {getattr(market_result, 'error_message', 'unknown')}", "ERROR")
            except Exception as me:
                self.logger.log(f"[RECONCILE] Error during fallback market close: {me}", "ERROR")
            return False
        except Exception as e:
            self.logger.log(f"[RECONCILE] Error while reconciling close coverage: {e}", "ERROR")
            return False

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
                # BUY direction: open at best_bid, close at higher price
                # Get current opening price (where we would buy)
                new_open_price = best_bid
                # Calculate where we would close
                if self.config.use_tick_mode():
                    new_order_close_price = new_open_price + (self.config.tick_size * Decimal(self.config.take_profit_tick))
                else:
                    new_order_close_price = new_open_price * (1 + self.config.take_profit/100)
                
                # Calculate the distance between new close price and existing close price
                # For BUY: we want next_close_price (existing) - new_order_close_price (new) >= grid_step
                if self.config.use_tick_mode():
                    # Tick mode: compare tick differences
                    price_diff_ticks = abs(next_close_price - new_order_close_price) / self.config.tick_size
                    grid_step_ticks = Decimal(self.config.grid_step_tick)
                    self.logger.log(f"[GRID] BUY: open={new_open_price:.5f} new_close={new_order_close_price:.5f} existing_close={next_close_price:.5f} diff={price_diff_ticks:.1f} ticks threshold={grid_step_ticks} ticks", "INFO")
                    
                    if price_diff_ticks >= grid_step_ticks:
                        self.logger.log(f"[GRID] ✅ OK - Grid step condition met ({price_diff_ticks:.1f} ticks >= {grid_step_ticks} ticks)", "INFO")
                        return True
                    else:
                        self.logger.log(f"[GRID] ❌ SKIP - Too close ({price_diff_ticks:.1f} ticks < {grid_step_ticks} ticks)", "INFO")
                        return False
                else:
                    # Percentage mode
                    price_diff_percent = abs((next_close_price - new_order_close_price) / new_order_close_price) * 100
                    self.logger.log(f"[GRID] BUY: open={new_open_price:.5f} new_close={new_order_close_price:.5f} existing_close={next_close_price:.5f} diff={price_diff_percent:.3f}% threshold={self.config.grid_step}%", "INFO")
                    
                    if price_diff_percent >= self.config.grid_step:
                        self.logger.log(f"[GRID] ✅ OK - Grid step condition met ({price_diff_percent:.3f}% >= {self.config.grid_step}%)", "INFO")
                        return True
                    else:
                        self.logger.log(f"[GRID] ❌ SKIP - Too close ({price_diff_percent:.3f}% < {self.config.grid_step}%)", "INFO")
                        return False
            elif self.config.direction == "sell":
                # SELL direction: open at best_ask, close at lower price
                # Get current opening price (where we would sell)
                new_open_price = best_ask
                # Calculate where we would close
                if self.config.use_tick_mode():
                    new_order_close_price = new_open_price - (self.config.tick_size * Decimal(self.config.take_profit_tick))
                else:
                    new_order_close_price = new_open_price * (1 - self.config.take_profit/100)
                
                # Calculate the distance between new close price and existing close price
                # For SELL: we want abs(next_close_price - new_order_close_price) >= grid_step
                if self.config.use_tick_mode():
                    # Tick mode: compare tick differences
                    price_diff_ticks = abs(next_close_price - new_order_close_price) / self.config.tick_size
                    grid_step_ticks = Decimal(self.config.grid_step_tick)
                    self.logger.log(f"[GRID] SELL: open={new_open_price:.5f} new_close={new_order_close_price:.5f} existing_close={next_close_price:.5f} diff={price_diff_ticks:.1f} ticks threshold={grid_step_ticks} ticks", "INFO")
                    
                    if price_diff_ticks >= grid_step_ticks:
                        self.logger.log(f"[GRID] ✅ OK - Grid step condition met ({price_diff_ticks:.1f} ticks >= {grid_step_ticks} ticks)", "INFO")
                        return True
                    else:
                        self.logger.log(f"[GRID] ❌ SKIP - Too close ({price_diff_ticks:.1f} ticks < {grid_step_ticks} ticks)", "INFO")
                        return False
                else:
                    # Percentage mode
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
            if self.config.use_tick_mode():
                self.logger.log(f"Take Profit: {self.config.take_profit_tick} ticks (TICK MODE)", "INFO")
                self.logger.log(f"Grid Step: {self.config.grid_step_tick} ticks (TICK MODE)", "INFO")
            else:
                self.logger.log(f"Take Profit: {self.config.take_profit}%", "INFO")
                self.logger.log(f"Grid Step: {self.config.grid_step}%", "INFO")
            self.logger.log(f"Direction: {self.config.direction}", "INFO")
            self.logger.log(f"Max Orders: {self.config.max_orders}", "INFO")
            self.logger.log(f"Wait Time: {self.config.wait_time}s", "INFO")
            self.logger.log(f"Exchange: {self.config.exchange}", "INFO")
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
                        # Check position and active orders BEFORE reconcile to determine if we have coverage
                        position_amt = await self.exchange_client.get_account_positions()
                        if position_amt != 0:
                            close_side = 'sell' if position_amt > 0 else 'buy'
                            active_orders = await self.exchange_client.get_active_orders(self.config.contract_id)
                            active_close_amount = sum(
                                Decimal(getattr(o, 'size', 0)) if not isinstance(o, dict) else Decimal(o.get('size', 0))
                                for o in active_orders
                                if (getattr(o, 'side', None) == close_side) or (isinstance(o, dict) and o.get('side') == close_side)
                            )
                            required_close = abs(position_amt)
                            has_sufficient_coverage = active_close_amount >= required_close
                            
                            # Call reconcile which will handle deficit if needed
                            placed_topup = await self._reconcile_close_coverage()
                            if placed_topup:
                                # Give exchange a moment to register the new order
                                await asyncio.sleep(1)
                                continue
                            
                            # If reconcile returned False, check if we have sufficient coverage
                            if has_sufficient_coverage:
                                # We have enough orders covering the position - this is OK, allow trading to continue
                                self.logger.log(f"[MAIN] ✅ Position={position_amt} has sufficient coverage (ActiveClose={active_close_amount} >= Required={required_close}), allowing trading to continue", "INFO")
                                # Continue to check if we can open new orders (don't skip)
                                # Position tracking will be cleared when position becomes 0
                            else:
                                # There's an uncovered position and reconcile failed - skip opening new orders
                                last_position = getattr(self, '_last_checked_position', None)
                                position_decreasing = (last_position is not None and abs(position_amt) < abs(last_position))
                                self._last_checked_position = position_amt
                                
                                if position_decreasing:
                                    self.logger.log(f"[MAIN] Position decreasing: {last_position} → {position_amt}, waiting for orders to fill...", "INFO")
                                    await asyncio.sleep(3)  # Wait longer when position is actively decreasing
                                else:
                                    self.logger.log(f"[MAIN] Skipping open order: position={position_amt} still needs coverage (ActiveClose={active_close_amount} < Required={required_close})", "WARNING")
                                    await asyncio.sleep(2)
                                continue
                        else:
                            # Position resolved, clear tracking
                            self._last_checked_position = None
                    except Exception as e:
                        self.logger.log(f"[RECONCILE] Error: {e}", "ERROR")
                        # On error, also skip opening new orders to avoid compounding issues
                        await asyncio.sleep(2)
                        continue

                    # Check if we have capacity for new orders
                    if len(self.active_close_orders) < self.config.max_orders:
                        # Check grid step condition
                        try:
                            if await self._meet_grid_step_condition():
                                await self._place_and_monitor_open_order()
                                self.last_close_orders += 1
                            else:
                                # Grid step not met, wait a bit before checking again
                                await asyncio.sleep(2)
                        except ValueError as e:
                            if "No bid/ask data available" in str(e):
                                # Exchange temporarily unavailable (e.g., 503 error), wait and retry
                                self.logger.log(f"[MAIN] ⚠️ Exchange data temporarily unavailable: {e}. Waiting 30s before retry...", "WARNING")
                                await asyncio.sleep(30)  # Wait 30 seconds for exchange to recover
                                continue
                            else:
                                # Other ValueError, log and continue
                                self.logger.log(f"[MAIN] ⚠️ Grid step check error: {e}. Waiting 10s before retry...", "WARNING")
                                await asyncio.sleep(10)
                                continue
                        except Exception as e:
                            # Other exceptions during grid check, log and continue
                            self.logger.log(f"[MAIN] ⚠️ Error checking grid step: {e}. Waiting 10s before retry...", "WARNING")
                            await asyncio.sleep(10)
                            continue
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