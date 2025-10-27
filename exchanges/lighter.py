"""
Lighter exchange client implementation.
"""

import os
import asyncio
import time
import logging
from decimal import Decimal
from typing import Dict, Any, List, Optional, Tuple

from .base import BaseExchangeClient, OrderResult, OrderInfo, query_retry
from helpers.logger import TradingLogger

# Import official Lighter SDK for API client
import lighter
from lighter import SignerClient, ApiClient, Configuration

# Import custom WebSocket implementation
from .lighter_custom_websocket import LighterCustomWebSocketManager

# Suppress Lighter SDK debug logs
logging.getLogger('lighter').setLevel(logging.WARNING)
# Also suppress root logger DEBUG messages that might be coming from Lighter SDK
root_logger = logging.getLogger()
if root_logger.level == logging.DEBUG:
    root_logger.setLevel(logging.WARNING)


class LighterClient(BaseExchangeClient):
    """Lighter exchange client implementation."""

    def __init__(self, config: Dict[str, Any]):
        """Initialize Lighter client."""
        super().__init__(config)

        # Lighter credentials from environment
        self.api_key_private_key = os.getenv('API_KEY_PRIVATE_KEY')
        self.account_index = int(os.getenv('LIGHTER_ACCOUNT_INDEX', '0'))
        self.api_key_index = int(os.getenv('LIGHTER_API_KEY_INDEX', '0'))
        self.base_url = "https://mainnet.zklighter.elliot.ai"

        if not self.api_key_private_key:
            raise ValueError("API_KEY_PRIVATE_KEY must be set in environment variables")

        # Initialize logger
        self.logger = TradingLogger(exchange="lighter", ticker=self.config.ticker, log_to_console=False)
        self._order_update_handler = None

        # Initialize Lighter client (will be done in connect)
        self.lighter_client = None

        # Initialize API client (will be done in connect)
        self.api_client = None

        # Market configuration
        self.base_amount_multiplier = None
        self.price_multiplier = None
        self.orders_cache = {}
        self.current_order_client_id = None
        self.current_order = None

    def _validate_config(self) -> None:
        """Validate Lighter configuration."""
        required_env_vars = ['API_KEY_PRIVATE_KEY', 'LIGHTER_ACCOUNT_INDEX', 'LIGHTER_API_KEY_INDEX']
        missing_vars = [var for var in required_env_vars if not os.getenv(var)]
        if missing_vars:
            raise ValueError(f"Missing required environment variables: {missing_vars}")

    async def _get_market_config(self, ticker: str) -> Tuple[int, int, int]:
        """Get market configuration for a ticker using official SDK."""
        try:
            # Use shared API client
            order_api = lighter.OrderApi(self.api_client)

            # Get order books to find market info
            order_books = await order_api.order_books()

            for market in order_books.order_books:
                if market.symbol == ticker:
                    market_id = market.market_id
                    base_multiplier = pow(10, market.supported_size_decimals)
                    price_multiplier = pow(10, market.supported_price_decimals)

                    # Store market info for later use
                    self.config.market_info = market

                    self.logger.log(
                        f"Market config for {ticker}: ID={market_id}, "
                        f"Base multiplier={base_multiplier}, Price multiplier={price_multiplier}",
                        "INFO"
                    )
                    return market_id, base_multiplier, price_multiplier

            raise Exception(f"Ticker {ticker} not found in available markets")

        except Exception as e:
            self.logger.log(f"Error getting market config: {e}", "ERROR")
            raise

    async def _initialize_lighter_client(self):
        """Initialize the Lighter client using official SDK."""
        if self.lighter_client is None:
            try:
                # Import nonce manager to use API-based nonce management for reliability
                from lighter.nonce_manager import NonceManagerType
                
                self.lighter_client = SignerClient(
                    url=self.base_url,
                    private_key=self.api_key_private_key,
                    account_index=self.account_index,
                    api_key_index=self.api_key_index,
                    nonce_management_type=NonceManagerType.API  # Use API-based nonce to avoid sync issues
                )

                # Check client
                err = self.lighter_client.check_client()
                if err is not None:
                    raise Exception(f"CheckClient error: {err}")

                # Get market configuration if not already set
                if self.base_amount_multiplier is None or self.price_multiplier is None:
                    try:
                        # Initialize API client first
                        self.api_client = ApiClient(configuration=Configuration(host=self.base_url))
                        
                        # Get market config
                        market_id, base_multiplier, price_multiplier = await self._get_market_config(self.config.ticker)
                        
                        # Set the multipliers
                        self.base_amount_multiplier = base_multiplier
                        self.price_multiplier = price_multiplier
                        
                        # Update contract_id in config
                        self.config.contract_id = str(market_id)
                        
                        self.logger.log(f"Market config loaded: {self.config.ticker} -> ID={market_id}, "
                                       f"Base multiplier={base_multiplier}, Price multiplier={price_multiplier}", "INFO")
                    except Exception as e:
                        self.logger.log(f"Failed to get market config: {e}", "ERROR")
                        # Set default values to avoid None errors
                        self.base_amount_multiplier = 1000000  # Default for ETH
                        self.price_multiplier = 100000000  # Default for ETH

                self.logger.log("Lighter client initialized successfully with API nonce management", "INFO")
            except Exception as e:
                self.logger.log(f"Failed to initialize Lighter client: {e}", "ERROR")
                raise
        return self.lighter_client

    async def connect(self) -> None:
        """Connect to Lighter."""
        try:
            # Initialize shared API client
            self.api_client = ApiClient(configuration=Configuration(host=self.base_url))

            # Initialize Lighter client
            await self._initialize_lighter_client()

            # Add market config to config for WebSocket manager
            self.config.market_index = self.config.contract_id
            self.config.account_index = self.account_index
            self.config.lighter_client = self.lighter_client

            # Initialize WebSocket manager (using custom implementation)
            self.ws_manager = LighterCustomWebSocketManager(
                config=self.config,
                order_update_callback=self._handle_websocket_order_update
            )

            # Set logger for WebSocket manager
            self.ws_manager.set_logger(self.logger)

            # Start WebSocket connection in background task
            asyncio.create_task(self.ws_manager.connect())
            # Wait a moment for connection to establish
            await asyncio.sleep(2)

        except Exception as e:
            self.logger.log(f"Error connecting to Lighter: {e}", "ERROR")
            raise

    async def disconnect(self) -> None:
        """Disconnect from Lighter."""
        try:
            if hasattr(self, 'ws_manager') and self.ws_manager:
                await self.ws_manager.disconnect()

            # Close Lighter client (which has its own API client)
            if hasattr(self, 'lighter_client') and self.lighter_client:
                try:
                    await self.lighter_client.close()
                    self.lighter_client = None
                except Exception as e:
                    self.logger.log(f"Error closing Lighter client: {e}", "WARNING")

            # Close shared API client
            if self.api_client:
                await self.api_client.close()
                self.api_client = None
                
            self.logger.log("Lighter client disconnected successfully", "INFO")
        except Exception as e:
            self.logger.log(f"Error during Lighter disconnect: {e}", "ERROR")

    def get_exchange_name(self) -> str:
        """Get the exchange name."""
        return "lighter"

    def setup_order_update_handler(self, handler) -> None:
        """Setup order update handler for WebSocket."""
        self._order_update_handler = handler

    def _handle_websocket_order_update(self, order_data_list: List[Dict[str, Any]]):
        """Handle order updates from WebSocket."""
        for order_data in order_data_list:
            if order_data['market_index'] != self.config.contract_id:
                continue

            side = 'sell' if order_data['is_ask'] else 'buy'
            if side == self.config.close_order_side:
                order_type = "CLOSE"
            else:
                order_type = "OPEN"

            order_id = order_data['order_index']
            status = order_data['status'].upper()
            filled_size = Decimal(order_data['filled_base_amount'])
            size = Decimal(order_data['initial_base_amount'])
            price = Decimal(order_data['price'])
            remaining_size = Decimal(order_data['remaining_base_amount'])

            if order_id in self.orders_cache.keys():
                if (self.orders_cache[order_id]['status'] == 'OPEN' and
                        status == 'OPEN' and
                        filled_size == self.orders_cache[order_id]['filled_size']):
                    continue
                elif status in ['FILLED', 'CANCELED']:
                    del self.orders_cache[order_id]
                else:
                    self.orders_cache[order_id]['status'] = status
                    self.orders_cache[order_id]['filled_size'] = filled_size
            elif status == 'OPEN':
                self.orders_cache[order_id] = {'status': status, 'filled_size': filled_size}

            if status == 'OPEN' and filled_size > 0:
                status = 'PARTIALLY_FILLED'

            if status == 'OPEN':
                self.logger.log(f"[{order_type}] [{order_id}] {status} "
                                f"{size} @ {price}", "INFO")
            else:
                self.logger.log(f"[{order_type}] [{order_id}] {status} "
                                f"{filled_size} @ {price}", "INFO")

            if order_data['client_order_index'] == self.current_order_client_id or order_type == 'OPEN':
                current_order = OrderInfo(
                    order_id=order_id,
                    side=side,
                    size=size,
                    price=price,
                    status=status,
                    filled_size=filled_size,
                    remaining_size=remaining_size,
                    cancel_reason=''
                )
                self.current_order = current_order

            if status in ['FILLED', 'CANCELED']:
                self.logger.log_transaction(order_id, side, filled_size, price, status)

    @query_retry(default_return=(0, 0))
    async def fetch_bbo_prices(self, contract_id: str) -> Tuple[Decimal, Decimal]:
        """Get orderbook using official SDK."""
        # Use WebSocket data if available
        if (hasattr(self, 'ws_manager') and
                self.ws_manager.best_bid and self.ws_manager.best_ask):
            best_bid = Decimal(str(self.ws_manager.best_bid))
            best_ask = Decimal(str(self.ws_manager.best_ask))

            if best_bid <= 0 or best_ask <= 0 or best_bid >= best_ask:
                self.logger.log("Invalid bid/ask prices", "ERROR")
                raise ValueError("Invalid bid/ask prices")
        else:
            self.logger.log("Unable to get bid/ask prices from WebSocket.", "ERROR")
            raise ValueError("WebSocket not running. No bid/ask prices available")

        return best_bid, best_ask

    async def _submit_order_with_retry(self, order_params: Dict[str, Any], max_retries: int = 3) -> OrderResult:
        """Submit an order with Lighter using official SDK with retry on nonce errors."""
        # Ensure client is initialized
        if self.lighter_client is None:
            raise ValueError("Lighter client not initialized. Call connect() first.")

        last_error = None
        for attempt in range(max_retries):
            try:
                # Create order using official SDK
                create_order, tx_hash, error = await self.lighter_client.create_order(**order_params)
                
                if error is not None:
                    # Check if it's a nonce error
                    if 'invalid nonce' in str(error).lower():
                        self.logger.log(f"Nonce error on attempt {attempt + 1}/{max_retries}, refreshing nonce...", "WARNING")
                        # Nonce manager should have already refreshed, wait a bit before retry
                        await asyncio.sleep(0.5)
                        last_error = error
                        continue
                    else:
                        # Non-nonce error, don't retry
                        return OrderResult(
                            success=False, 
                            order_id=str(order_params['client_order_index']),
                            error_message=f"Order creation error: {error}")
                else:
                    # Success
                    return OrderResult(success=True, order_id=str(order_params['client_order_index']))
                    
            except Exception as e:
                self.logger.log(f"Exception on attempt {attempt + 1}/{max_retries}: {e}", "WARNING")
                last_error = str(e)
                await asyncio.sleep(0.5)
        
        # All retries exhausted
        return OrderResult(
            success=False, 
            order_id=str(order_params['client_order_index']),
            error_message=f"Order creation failed after {max_retries} attempts: {last_error}")

    async def place_limit_order(self, contract_id: str, quantity: Decimal, price: Decimal,
                                side: str) -> OrderResult:
        """Place a limit order with Lighter using official SDK."""
        # Ensure client is initialized
        if self.lighter_client is None:
            await self._initialize_lighter_client()

        # Determine order side and price
        if side.lower() == 'buy':
            is_ask = False
        elif side.lower() == 'sell':
            is_ask = True
        else:
            raise Exception(f"Invalid side: {side}")

        # Generate unique client order index
        client_order_index = int(time.time() * 1000) % 1000000  # Simple unique ID
        self.current_order_client_id = client_order_index

        # Create order parameters
        order_params = {
            'market_index': self.config.contract_id,
            'client_order_index': client_order_index,
            'base_amount': int(quantity * self.base_amount_multiplier),
            'price': int(price * self.price_multiplier),
            'is_ask': is_ask,
            'order_type': self.lighter_client.ORDER_TYPE_LIMIT,
            'time_in_force': self.lighter_client.ORDER_TIME_IN_FORCE_GOOD_TILL_TIME,
            'reduce_only': False,
            'trigger_price': 0,
        }

        order_result = await self._submit_order_with_retry(order_params)
        return order_result

    async def place_post_only_order(self, contract_id: str, quantity: Decimal, price: Decimal,
                                    side: str) -> OrderResult:
        """Place a post-only order with Lighter using official SDK."""
        # Ensure client is initialized
        if self.lighter_client is None:
            await self._initialize_lighter_client()

        # Determine order side and price
        if side.lower() == 'buy':
            is_ask = False
        elif side.lower() == 'sell':
            is_ask = True
        else:
            raise Exception(f"Invalid side: {side}")

        # Generate unique client order index
        client_order_index = int(time.time() * 1000) % 1000000
        self.current_order_client_id = client_order_index

        try:
            # Use the official SDK's create_order method with POST_ONLY time in force
            tx, tx_hash, error = await self.lighter_client.create_order(
                market_index=self.config.contract_id,
                client_order_index=client_order_index,
                base_amount=int(quantity * self.base_amount_multiplier),
                price=int(price * self.price_multiplier),
                is_ask=is_ask,
                order_type=self.lighter_client.ORDER_TYPE_LIMIT,
                time_in_force=self.lighter_client.ORDER_TIME_IN_FORCE_POST_ONLY,
                order_expiry=self.lighter_client.DEFAULT_28_DAY_ORDER_EXPIRY,
                reduce_only=False,
                trigger_price=0
            )

            if error is not None:
                return OrderResult(
                    success=False,
                    order_id=str(client_order_index),
                    error_message=error
                )

            return OrderResult(
                success=True,
                order_id=str(client_order_index),
                side=side,
                size=quantity,
                price=price,
                status='SUBMITTED'
            )

        except Exception as e:
            self.logger.log(f"Error placing post-only order: {e}", "ERROR")
            return OrderResult(
                success=False,
                order_id=str(client_order_index),
                error_message=str(e)
            )

    async def place_market_order(self, contract_id: str, quantity: Decimal, side: str) -> OrderResult:
        """Place a market order with Lighter using official SDK with improved slippage protection."""
        # Ensure client is initialized
        if self.lighter_client is None:
            await self._initialize_lighter_client()

        # Determine order side
        if side.lower() == 'buy':
            is_ask = False
        elif side.lower() == 'sell':
            is_ask = True
        else:
            raise Exception(f"Invalid side: {side}")

        # Generate unique client order index
        client_order_index = int(time.time() * 1000) % 1000000
        self.current_order_client_id = client_order_index

        try:
            # Ensure multipliers are set
            if self.base_amount_multiplier is None or self.price_multiplier is None:
                self.logger.log("Base amount or price multiplier is None, using defaults", "WARNING")
                base_amount_multiplier = 1000000  # Default for ETH
                price_multiplier = 100000000  # Default for ETH
            else:
                base_amount_multiplier = self.base_amount_multiplier
                price_multiplier = self.price_multiplier

            # Convert quantity to base amount (following official examples)
            base_amount = int(quantity * base_amount_multiplier)
            
            self.logger.log(f"Placing market order: side={side}, quantity={quantity}, "
                           f"base_amount={base_amount}, market_index={self.config.contract_id}", "INFO")

            # Use the official SDK's limited slippage method for better price protection
            tx, tx_hash, error = await self.lighter_client.create_market_order_limited_slippage(
                market_index=int(self.config.contract_id),  # Convert to int
                client_order_index=client_order_index,
                base_amount=base_amount,
                max_slippage=0.02,  # 2% max slippage
                is_ask=is_ask
            )

            if error is not None:
                return OrderResult(
                    success=False,
                    order_id=str(client_order_index),
                    error_message=error
                )

            return OrderResult(
                success=True,
                order_id=str(client_order_index),
                side=side,
                size=quantity,
                price=Decimal(0),  # Market order price will be determined by execution
                status='SUBMITTED'
            )

        except Exception as e:
            self.logger.log(f"Error placing market order: {e}", "ERROR")
            import traceback
            self.logger.log(f"Full traceback: {traceback.format_exc()}", "ERROR")
            return OrderResult(
                success=False,
                order_id=str(client_order_index),
                error_message=str(e)
            )

    async def place_open_order(self, contract_id: str, quantity: Decimal, direction: str) -> OrderResult:
        """Place an open order with Lighter using official SDK."""

        self.current_order = None
        self.current_order_client_id = None
        order_price = await self.get_order_price(direction)

        order_price = self.round_to_tick(order_price)
        
        # Use retry mechanism for nonce errors
        max_retries = 3
        for attempt in range(max_retries):
            try:
                order_result = await self.place_limit_order(contract_id, quantity, order_price, direction)
                if order_result.success:
                    break
                elif 'invalid nonce' in str(order_result.error_message).lower() and attempt < max_retries - 1:
                    self.logger.log(f"[OPEN] Nonce error on attempt {attempt + 1}/{max_retries}, retrying...", "WARNING")
                    await asyncio.sleep(1)  # Wait longer for nonce refresh
                    continue
                else:
                    raise Exception(f"[OPEN] Error placing order: {order_result.error_message}")
            except Exception as e:
                if 'invalid nonce' in str(e).lower() and attempt < max_retries - 1:
                    self.logger.log(f"[OPEN] Nonce error on attempt {attempt + 1}/{max_retries}, retrying...", "WARNING")
                    await asyncio.sleep(1)
                    continue
                else:
                    raise e

        # Simplified - don't wait for order to fill, just return success
        # The order will be monitored by WebSocket and trading bot logic
        return OrderResult(
            success=True,
            order_id=order_result.order_id,
            side=direction,
            size=quantity,
            price=order_price,
            status='OPEN'
        )

    async def _get_active_close_orders(self, contract_id: str) -> int:
        """Get active close orders for a contract using official SDK."""
        active_orders = await self.get_active_orders(contract_id)
        active_close_orders = 0
        for order in active_orders:
            if order.side == self.config.close_order_side:
                active_close_orders += 1
        return active_close_orders

    async def place_close_order(self, contract_id: str, quantity: Decimal, price: Decimal, side: str) -> OrderResult:
        """Place a close order with Lighter using official SDK."""
        self.current_order = None
        self.current_order_client_id = None
        order_result = await self.place_limit_order(contract_id, quantity, price, side)

        # wait for 5 seconds to ensure order is placed
        await asyncio.sleep(5)
        if order_result.success:
            return OrderResult(
                success=True,
                order_id=order_result.order_id,
                side=side,
                size=quantity,
                price=price,
                status='OPEN'
            )
        else:
            raise Exception(f"[CLOSE] Error placing order: {order_result.error_message}")
    
    async def get_order_price(self, side: str = '') -> Decimal:
        """Get the price of an order with Lighter using official SDK - Improved pricing."""
        # Get current market prices
        best_bid, best_ask = await self.fetch_bbo_prices(self.config.contract_id)
        if best_bid <= 0 or best_ask <= 0 or best_bid >= best_ask:
            self.logger.log("Invalid bid/ask prices", "ERROR")
            raise ValueError("Invalid bid/ask prices")

        # Use more aggressive pricing - closer to market price
        if side.lower() == 'buy':
            # For buy orders, use price slightly below best ask (more likely to fill)
            order_price = best_ask * Decimal('0.999')  # 0.1% below best ask
        else:
            # For sell orders, use price slightly above best bid (more likely to fill)
            order_price = best_bid * Decimal('1.001')  # 0.1% above best bid

        # Round to tick size
        if hasattr(self, 'config') and hasattr(self.config, 'tick_size'):
            order_price = self.round_to_tick(order_price)

        # Check existing close orders to avoid conflicts
        active_orders = await self.get_active_orders(self.config.contract_id)
        close_orders = [order for order in active_orders if order.side == self.config.close_order_side]
        for order in close_orders:
            if side == 'buy':
                order_price = min(order_price, order.price - self.config.tick_size)
            else:
                order_price = max(order_price, order.price + self.config.tick_size)

        self.logger.log(f"Price calculation: side={side}, best_bid={best_bid}, best_ask={best_ask}, order_price={order_price}", "DEBUG")
        return order_price

    async def cancel_order(self, order_id: str, max_retries: int = 3) -> OrderResult:
        """Cancel an order with Lighter with retry on nonce errors."""
        # Ensure client is initialized
        if self.lighter_client is None:
            await self._initialize_lighter_client()

        last_error = None
        for attempt in range(max_retries):
            try:
                # Cancel order using official SDK
                cancel_order, tx_hash, error = await self.lighter_client.cancel_order(
                    market_index=self.config.contract_id,
                    order_index=int(order_id)
                )

                if error is not None:
                    # Check if it's a nonce error
                    if 'invalid nonce' in str(error).lower():
                        self.logger.log(f"Nonce error canceling order on attempt {attempt + 1}/{max_retries}, refreshing...", "WARNING")
                        await asyncio.sleep(0.5)
                        last_error = error
                        continue
                    else:
                        # Non-nonce error, don't retry
                        return OrderResult(success=False, error_message=f"Cancel order error: {error}")

                if tx_hash:
                    return OrderResult(success=True)
                else:
                    return OrderResult(success=False, error_message='Failed to send cancellation transaction')
                    
            except Exception as e:
                self.logger.log(f"Exception canceling order on attempt {attempt + 1}/{max_retries}: {e}", "WARNING")
                last_error = str(e)
                await asyncio.sleep(0.5)
        
        # All retries exhausted
        return OrderResult(success=False, error_message=f"Cancel failed after {max_retries} attempts: {last_error}")

    async def get_order_info(self, order_id: str) -> Optional[OrderInfo]:
        """Get order information from Lighter using official SDK."""
        try:
            # Use shared API client to get account info
            account_api = lighter.AccountApi(self.api_client)

            # Get account orders
            account_data = await account_api.account(by="index", value=str(self.account_index))

            # Look for the specific order in account positions
            for position in account_data.positions:
                if position.symbol == self.config.ticker:
                    position_amt = abs(float(position.position))
                    if position_amt > 0.001:  # Only include significant positions
                        return OrderInfo(
                            order_id=order_id,
                            side="buy" if float(position.position) > 0 else "sell",
                            size=Decimal(str(position_amt)),
                            price=Decimal(str(position.avg_price)),
                            status="FILLED",  # Positions are filled orders
                            filled_size=Decimal(str(position_amt)),
                            remaining_size=Decimal('0')
                        )

            return None

        except Exception as e:
            self.logger.log(f"Error getting order info: {e}", "ERROR")
            return None

    @query_retry(reraise=True)
    async def _fetch_orders_with_retry(self) -> List[Dict[str, Any]]:
        """Get orders using official SDK."""
        # Ensure client is initialized
        if self.lighter_client is None:
            await self._initialize_lighter_client()

        # Generate auth token for API call
        auth_token, error = self.lighter_client.create_auth_token_with_expiry()
        if error is not None:
            self.logger.log(f"Error creating auth token: {error}", "ERROR")
            raise ValueError(f"Error creating auth token: {error}")

        # Use OrderApi to get active orders
        order_api = lighter.OrderApi(self.api_client)

        # Get active orders for the specific market
        orders_response = await order_api.account_active_orders(
            account_index=self.account_index,
            market_id=self.config.contract_id,
            auth=auth_token
        )

        if not orders_response:
            self.logger.log("Failed to get orders", "ERROR")
            raise ValueError("Failed to get orders")

        return orders_response.orders

    async def get_active_orders(self, contract_id: str) -> List[OrderInfo]:
        """Get active orders for a contract using official SDK."""
        order_list = await self._fetch_orders_with_retry()

        # Handle case when order_list is None (API error)
        if order_list is None:
            self.logger.log("Failed to get active orders, API returned None", "WARNING")
            return []

        # Filter orders for the specific market
        contract_orders = []
        for order in order_list:
            # Convert Lighter Order to OrderInfo
            side = "sell" if order.is_ask else "buy"
            size = Decimal(order.initial_base_amount)
            price = Decimal(order.price)

            # Only include orders with remaining size > 0
            if size > 0:
                contract_orders.append(OrderInfo(
                    order_id=str(order.order_index),
                    side=side,
                    size=Decimal(order.remaining_base_amount),  # FIXME: This is wrong. Should be size
                    price=price,
                    status=order.status.upper(),
                    filled_size=Decimal(order.filled_base_amount),
                    remaining_size=Decimal(order.remaining_base_amount)
                ))

        return contract_orders

    @query_retry(reraise=True)
    async def _fetch_positions_with_retry(self) -> List[Dict[str, Any]]:
        """Get positions using official SDK."""
        try:
            # Use shared API client
            account_api = lighter.AccountApi(self.api_client)

            # Debug logging
            self.logger.log(f"Fetching positions for account_index: {self.account_index}", "DEBUG")
            
            # Get account info using correct parameters
            account_data = await account_api.account(by="index", value=str(self.account_index))

            if not account_data or not account_data.accounts:
                self.logger.log("Failed to get positions", "ERROR")
                raise ValueError("Failed to get positions")

            # Return positions from the first account
            positions = account_data.accounts[0].positions
            self.logger.log(f"Found {len(positions)} positions", "DEBUG")
            return positions
            
        except Exception as e:
            self.logger.log(f"Error fetching positions: {e}", "ERROR")
            self.logger.log(f"Account index: {self.account_index}, Type: {type(self.account_index)}", "ERROR")
            raise

    async def get_account_positions(self) -> Decimal:
        """Get account positions using official SDK."""
        try:
            # Get account info which includes positions
            positions = await self._fetch_positions_with_retry()

            # Find position for current market
            for position in positions:
                if position.market_id == int(self.config.contract_id):
                    # Convert position string to Decimal
                    # position.sign: 1 for Long, -1 for Short
                    # position.position: the amount of position
                    position_amount = Decimal(position.position)
                    if position.sign == -1:  # Short position
                        position_amount = -position_amount
                    return position_amount

            return Decimal(0)
        except Exception as e:
            self.logger.log(f"Error getting account positions: {e}", "ERROR")
            return Decimal(0)

    async def get_contract_attributes(self) -> Tuple[str, Decimal]:
        """Get contract ID for a ticker."""
        ticker = self.config.ticker
        if len(ticker) == 0:
            self.logger.log("Ticker is empty", "ERROR")
            raise ValueError("Ticker is empty")

        order_api = lighter.OrderApi(self.api_client)
        # Get all order books to find the market for our ticker
        order_books = await order_api.order_books()

        # Find the market that matches our ticker
        market_info = None
        for market in order_books.order_books:
            if market.symbol == ticker:
                market_info = market
                break

        if market_info is None:
            self.logger.log("Failed to get markets", "ERROR")
            raise ValueError("Failed to get markets")

        market_summary = await order_api.order_book_details(market_id=market_info.market_id)
        order_book_details = market_summary.order_book_details[0]
        # Set contract_id to market name (Lighter uses market IDs as identifiers)
        self.config.contract_id = market_info.market_id
        self.base_amount_multiplier = pow(10, market_info.supported_size_decimals)
        self.price_multiplier = pow(10, market_info.supported_price_decimals)

        try:
            self.config.tick_size = Decimal("1") / (Decimal("10") ** order_book_details.price_decimals)
        except Exception:
            self.logger.log("Failed to get tick size", "ERROR")
            raise ValueError("Failed to get tick size")

        return self.config.contract_id, self.config.tick_size

    def round_to_tick(self, price: Decimal) -> Decimal:
        """Round price to tick size."""
        if hasattr(self, 'config') and hasattr(self.config, 'tick_size') and self.config.tick_size:
            return (price / self.config.tick_size).quantize(Decimal('1')) * self.config.tick_size
        return price
