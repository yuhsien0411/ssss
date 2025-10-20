"""
Bybit exchange client implementation.
"""

import os
import asyncio
import json
import traceback
from decimal import Decimal
from typing import Dict, Any, List, Optional, Tuple
from pybit.unified_trading import HTTP
from pybit.unified_trading import WebSocket

from .base import BaseExchangeClient, OrderResult, OrderInfo, query_retry
from helpers.logger import TradingLogger


class BybitClient(BaseExchangeClient):
    """Bybit exchange client implementation."""

    def __init__(self, config: Dict[str, Any]):
        """Initialize Bybit client."""
        super().__init__(config)

        # Bybit credentials from environment
        self.api_key = os.getenv('BYBIT_API_KEY')
        self.api_secret = os.getenv('BYBIT_API_SECRET')
        self.testnet = os.getenv('BYBIT_TESTNET', 'false').lower() == 'true'

        if not self.api_key or not self.api_secret:
            raise ValueError("BYBIT_API_KEY and BYBIT_API_SECRET must be set in environment variables")
        
        # Determine market category based on market_type
        self.market_category = "spot" if config.market_type == 'SPOT' else "linear"

        # Initialize Bybit HTTP client
        self.client = HTTP(
            api_key=self.api_key,
            api_secret=self.api_secret,
            testnet=self.testnet
        )

        # Initialize WebSocket client
        self.ws = WebSocket(
            testnet=self.testnet,
            channel_type="private",
            api_key=self.api_key,
            api_secret=self.api_secret
        )

        # Initialize logger
        self.logger = TradingLogger(exchange="bybit", ticker=self.config.ticker, log_to_console=False)

        self._order_update_handler = None

    def _validate_config(self) -> None:
        """Validate Bybit configuration."""
        required_env_vars = ['BYBIT_API_KEY', 'BYBIT_API_SECRET']
        missing_vars = [var for var in required_env_vars if not os.getenv(var)]
        if missing_vars:
            raise ValueError(f"Missing required environment variables: {missing_vars}")

    async def connect(self) -> None:
        """Connect to Bybit WebSocket."""
        try:
            # Subscribe to order updates
            self.ws.order_stream(
                callback=self._handle_order_update
            )
            # Wait for connection to establish
            await asyncio.sleep(2)
        except Exception as e:
            self.logger.log(f"Error connecting to Bybit WebSocket: {e}", "ERROR")
            # Continue without WebSocket if it fails

    async def disconnect(self) -> None:
        """Disconnect from Bybit."""
        try:
            if hasattr(self, 'ws') and self.ws:
                self.ws.exit()
        except Exception as e:
            self.logger.log(f"Error during Bybit disconnect: {e}", "ERROR")

    def get_exchange_name(self) -> str:
        """Get the exchange name."""
        return "bybit"

    def setup_order_update_handler(self, handler) -> None:
        """Setup order update handler for WebSocket."""
        self._order_update_handler = handler

    def _handle_order_update(self, message):
        """Handle order updates from WebSocket."""
        try:
            if self._order_update_handler:
                # Parse Bybit order update format
                data = message.get('data', {})
                if data:
                    order = data[0] if isinstance(data, list) else data
                    
                    order_id = order.get('orderId')
                    status = order.get('orderStatus')
                    side = order.get('side', '').lower()
                    filled_size = Decimal(order.get('cumExecQty', 0))
                    
                    # Determine order type based on side
                    if side == self.config.close_order_side:
                        order_type = "CLOSE"
                    else:
                        order_type = "OPEN"
                    
                    # Map Bybit status to our format
                    status_map = {
                        'New': 'OPEN',
                        'PartiallyFilled': 'PARTIALLY_FILLED',
                        'Filled': 'FILLED',
                        'Canceled': 'CANCELED',
                        'Rejected': 'CANCELED'
                    }
                    
                    mapped_status = status_map.get(status, status)
                    
                    self._order_update_handler({
                        'order_id': order_id,
                        'side': side,
                        'order_type': order_type,
                        'status': mapped_status,
                        'size': order.get('qty'),
                        'price': order.get('price'),
                        'contract_id': order.get('symbol'),
                        'filled_size': filled_size
                    })

        except Exception as e:
            self.logger.log(f"Error handling order update: {e}", "ERROR")
            self.logger.log(f"Traceback: {traceback.format_exc()}", "ERROR")

    @query_retry(default_return=(0, 0))
    async def fetch_bbo_prices(self, symbol: str) -> Tuple[Decimal, Decimal]:
        """Fetch best bid and ask prices."""
        try:
            # Get order book for spot trading
            response = self.client.get_orderbook(
                category=self.market_category,
                symbol=symbol,
                limit=1
            )
            
            if response['retCode'] == 0:
                data = response['result']
                best_bid = Decimal(data['b'][0][0]) if data['b'] else 0
                best_ask = Decimal(data['a'][0][0]) if data['a'] else 0
                return best_bid, best_ask
            else:
                self.logger.log(f"Failed to fetch order book: {response['retMsg']}", "ERROR")
                return 0, 0
                
        except Exception as e:
            self.logger.log(f"Error fetching BBO prices: {e}", "ERROR")
            return 0, 0

    async def place_open_order(self, contract_id: str, quantity: Decimal, direction: str) -> OrderResult:
        """Place an open order with Bybit."""
        try:
            best_bid, best_ask = await self.fetch_bbo_prices(contract_id)
            
            if best_bid <= 0 or best_ask <= 0:
                return OrderResult(success=False, error_message='Invalid bid/ask prices')

            # Calculate order price
            if direction == 'buy':
                order_price = best_ask - self.config.tick_size
            else:
                order_price = best_bid + self.config.tick_size

            # Place order
            # For spot trading, qty should be in COIN units (e.g., 1 BTC, not USDT amount)
            order_params = {
                "category": self.market_category,
                "symbol": contract_id,
                "side": direction.capitalize(),
                "orderType": "Limit",
                "qty": str(quantity),  # This is already in COIN units (e.g., 1 BTC)
                "price": str(self.round_to_tick(order_price)),
                "timeInForce": "GTC"
            }
            
            # For spot leverage trading, set isLeverage=1
            if self.market_category == "spot" and hasattr(self.config, 'market_type') and self.config.market_type == 'SPOT':
                order_params["isLeverage"] = 1  # Enable leverage for spot trading
            
            response = self.client.place_order(**order_params)

            if response['retCode'] == 0:
                order_id = response['result']['orderId']
                return OrderResult(
                    success=True,
                    order_id=order_id,
                    side=direction,
                    size=quantity,
                    price=order_price,
                    status='OPEN'
                )
            else:
                return OrderResult(success=False, error_message=response['retMsg'])

        except Exception as e:
            return OrderResult(success=False, error_message=str(e))

    async def place_close_order(self, contract_id: str, quantity: Decimal, price: Decimal, side: str) -> OrderResult:
        """Place a close order with Bybit."""
        try:
            best_bid, best_ask = await self.fetch_bbo_prices(contract_id)
            
            if best_bid <= 0 or best_ask <= 0:
                return OrderResult(success=False, error_message='Invalid bid/ask prices')

            # Adjust price to ensure it's a maker order
            adjusted_price = price
            if side.lower() == 'sell':
                if price <= best_bid:
                    adjusted_price = best_bid + self.config.tick_size
            elif side.lower() == 'buy':
                if price >= best_ask:
                    adjusted_price = best_ask - self.config.tick_size

            adjusted_price = self.round_to_tick(adjusted_price)

            # Place order
            # For spot trading, qty should be in COIN units (e.g., 1 BTC, not USDT amount)
            order_params = {
                "category": self.market_category,
                "symbol": contract_id,
                "side": side.capitalize(),
                "orderType": "Limit",
                "qty": str(quantity),  # This is already in COIN units (e.g., 1 BTC)
                "price": str(adjusted_price),
                "timeInForce": "GTC"
            }
            
            # For spot leverage trading, set isLeverage=1
            if self.market_category == "spot" and hasattr(self.config, 'market_type') and self.config.market_type == 'SPOT':
                order_params["isLeverage"] = 1  # Enable leverage for spot trading
            
            response = self.client.place_order(**order_params)

            if response['retCode'] == 0:
                order_id = response['result']['orderId']
                return OrderResult(
                    success=True,
                    order_id=order_id,
                    side=side,
                    size=quantity,
                    price=adjusted_price,
                    status='OPEN'
                )
            else:
                return OrderResult(success=False, error_message=response['retMsg'])

        except Exception as e:
            return OrderResult(success=False, error_message=str(e))

    async def cancel_order(self, order_id: str) -> OrderResult:
        """Cancel an order with Bybit."""
        try:
            response = self.client.cancel_order(
                category=self.market_category,
                symbol=self.config.ticker + "USDT",
                orderId=order_id
            )

            if response['retCode'] == 0:
                return OrderResult(success=True)
            else:
                return OrderResult(success=False, error_message=response['retMsg'])

        except Exception as e:
            return OrderResult(success=False, error_message=str(e))

    @query_retry()
    async def get_order_info(self, order_id: str) -> Optional[OrderInfo]:
        """Get order information from Bybit."""
        try:
            response = self.client.get_open_orders(
                category=self.market_category,
                symbol=self.config.ticker + "USDT",
                orderId=order_id
            )

            if response['retCode'] == 0:
                orders = response['result']['list']
                if orders:
                    order = orders[0]
                    return OrderInfo(
                        order_id=order.get('orderId', ''),
                        side=order.get('side', '').lower(),
                        size=Decimal(order.get('qty', 0)),
                        price=Decimal(order.get('price', 0)),
                        status=order.get('orderStatus', ''),
                        filled_size=Decimal(order.get('cumExecQty', 0)),
                        remaining_size=Decimal(order.get('qty', 0)) - Decimal(order.get('cumExecQty', 0))
                    )
            return None

        except Exception as e:
            self.logger.log(f"Error getting order info: {e}", "ERROR")
            return None

    @query_retry(default_return=[])
    async def get_active_orders(self, contract_id: str) -> List[OrderInfo]:
        """Get active orders for a symbol."""
        try:
            response = self.client.get_open_orders(
                category=self.market_category,
                symbol=contract_id
            )

            if response['retCode'] == 0:
                orders = response['result']['list']
                order_list = []
                
                for order in orders:
                    order_list.append(OrderInfo(
                        order_id=order.get('orderId', ''),
                        side=order.get('side', '').lower(),
                        size=Decimal(order.get('qty', 0)),
                        price=Decimal(order.get('price', 0)),
                        status=order.get('orderStatus', ''),
                        filled_size=Decimal(order.get('cumExecQty', 0)),
                        remaining_size=Decimal(order.get('qty', 0)) - Decimal(order.get('cumExecQty', 0))
                    ))
                
                return order_list
            return []

        except Exception as e:
            self.logger.log(f"Error getting active orders: {e}", "ERROR")
            return []

    @query_retry(default_return=0)
    async def get_account_positions(self) -> Decimal:
        """Get account positions."""
        try:
            # Use UNIFIED account type for Bybit unified account
            response = self.client.get_wallet_balance(
                accountType="UNIFIED"
            )

            if response['retCode'] == 0:
                # For unified account, check individual coin balances
                account_list = response['result']['list']
                if account_list:
                    balances = account_list[0].get('coin', [])
                    usdc_balance = Decimal(0)
                    usdt_balance = Decimal(0)
                    
                    for coin in balances:
                        if coin['coin'] == 'USDC':
                            usdc_balance = Decimal(coin['walletBalance'])
                        elif coin['coin'] == 'USDT':
                            usdt_balance = Decimal(coin['walletBalance'])
                    
                    # For USDC/USDT trading, return the smaller balance as our position
                    if usdc_balance > 0 and usdt_balance > 0:
                        return min(usdc_balance, usdt_balance)
                    else:
                        # If we don't have both coins, return the balance of the ticker
                        if self.config.ticker.upper() == 'USDC':
                            return usdc_balance
                        else:
                            return usdt_balance
            return Decimal(0)

        except Exception as e:
            self.logger.log(f"Error getting account positions: {e}", "ERROR")
            return Decimal(0)

    async def get_contract_attributes(self) -> Tuple[str, Decimal]:
        """Get symbol and tick size for USDC/USDT."""
        ticker = self.config.ticker
        
        if ticker.upper() == 'USDC':
            symbol = 'USDCUSDT'
            tick_size = Decimal('0.0001')  # USDC/USDT typically has 4 decimal places
        else:
            # For other tickers, construct symbol
            symbol = ticker.upper() + 'USDT'
            tick_size = Decimal('0.01')  # Default tick size
        
        self.config.contract_id = symbol
        self.config.tick_size = tick_size
        
        return symbol, tick_size
