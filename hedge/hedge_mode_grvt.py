import asyncio
import json
import signal
import logging
import os
import sys
import time
import requests
import argparse
import traceback
import csv
from decimal import Decimal
from typing import Tuple

from lighter.signer_client import SignerClient
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from exchanges.grvt import GrvtClient
import websockets
from datetime import datetime
import pytz


class Config:
    """Simple config class to wrap dictionary for GRVT client."""
    def __init__(self, config_dict):
        for key, value in config_dict.items():
            setattr(self, key, value)


class HedgeBot:
    """Trading bot that places post-only orders on GRVT and hedges with market orders on Lighter."""

    def __init__(self, ticker: str, order_quantity: Decimal, fill_timeout: int = 5, iterations: int = 20):
        self.ticker = ticker
        self.order_quantity = order_quantity
        self.fill_timeout = fill_timeout
        self.lighter_order_filled = False
        self.iterations = iterations
        self.grvt_position = Decimal('0')
        self.lighter_position = Decimal('0')
        self.current_order = {}
        
        # 對沖寬限期機制 (1秒)
        self.hedge_grace_period = 1.0  # 秒
        self.hedge_grace_until = None  # 寬限期截止時間
        self.hedge_in_progress = False  # 是否正在進行對沖
        
        # 持倉監控任務
        self.position_monitor_task = None
        
        # API 速率限制管理
        self.last_grvt_position_call = 0
        self.last_lighter_position_call = 0
        # GRVT Level 3-4: 讀取操作 75-100 次/10秒 = 每秒 7.5-10 次
        # 設置為 0.5 秒間隔 = 每秒 2 次，遠低於限制
        self.grvt_rate_limit = 0.5
        
        # Lighter 帳戶類型檢測
        self.lighter_account_type = os.getenv('LIGHTER_ACCOUNT_TYPE', 'standard')  # 'standard' 或 'premium'
        if self.lighter_account_type == 'premium':
            self.lighter_rate_limit = 0.1  # 0.1 秒間隔，符合進階帳戶限制（24000 次/分鐘）
        else:
            # 標準帳戶：60 次/分鐘 = 1 次/秒
            self.lighter_rate_limit = 1.0  # 1 秒間隔，符合標準帳戶限制（60 次/分鐘）

        # Initialize logging to file
        os.makedirs("logs", exist_ok=True)
        self.log_filename = f"logs/grvt_{ticker}_hedge_mode_log.txt"
        self.csv_filename = f"logs/grvt_{ticker}_hedge_mode_trades.csv"
        self.original_stdout = sys.stdout

        # Initialize CSV file with headers if it doesn't exist
        self._initialize_csv_file()

        # Setup logger
        self.logger = logging.getLogger(f"hedge_bot_{ticker}")
        # 設置日誌級別 - 恢復 INFO 級別
        self.logger.setLevel(logging.INFO)

        # Clear any existing handlers to avoid duplicates
        self.logger.handlers.clear()

        # Disable verbose logging from external libraries
        logging.getLogger('urllib3').setLevel(logging.WARNING)
        logging.getLogger('requests').setLevel(logging.WARNING)
        logging.getLogger('websockets').setLevel(logging.WARNING)
        # 抑制 GRVT SDK 的過多日誌
        logging.getLogger('root').setLevel(logging.WARNING)
        logging.getLogger('pysdk').setLevel(logging.WARNING)
        logging.getLogger('pysdk.grvt_ccxt_base').setLevel(logging.WARNING)
        logging.getLogger('pysdk').setLevel(logging.WARNING)
        logging.getLogger('pysdk.grvt_ccxt_logging_selector').setLevel(logging.WARNING)

        # Create file handler with UTF-8 encoding
        file_handler = logging.FileHandler(self.log_filename, encoding='utf-8')
        file_handler.setLevel(logging.INFO)

        # Create console handler with UTF-8 encoding
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        
        # Set UTF-8 encoding for stdout on Windows
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8')

        # Create different formatters for file and console
        file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        console_formatter = logging.Formatter('%(asctime)s - %(levelname)s:%(name)s:%(message)s')

        file_handler.setFormatter(file_formatter)
        console_handler.setFormatter(console_formatter)

        # Add handlers to logger
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)

        # Prevent propagation to root logger to avoid duplicate messages
        self.logger.propagate = False

        # State management
        self.stop_flag = False
        self.order_counter = 0

        # GRVT state
        self.grvt_client = None
        self.grvt_contract_id = None
        self.grvt_tick_size = None
        self.grvt_order_status = None

        # Lighter order book state
        self.lighter_client = None
        self.lighter_order_book = {"bids": {}, "asks": {}}
        self.lighter_best_bid = None
        self.lighter_best_ask = None
        self.lighter_order_book_ready = False
        self.lighter_order_book_offset = 0
        self.lighter_order_book_sequence_gap = False
        self.lighter_snapshot_loaded = False
        self.lighter_order_book_lock = asyncio.Lock()

        # Lighter WebSocket state
        self.lighter_ws_task = None
        self.lighter_order_result = None

        # Lighter order management
        self.lighter_order_status = None
        self.lighter_order_price = None
        self.lighter_order_side = None
        self.lighter_order_size = None
        self.lighter_order_start_time = None

        # Strategy state
        self.waiting_for_lighter_fill = False
        self.wait_start_time = None
        self.hedge_grace_period = 1.0  # 對沖寬限期
        self.hedge_grace_until = 0
        self.hedge_in_progress = False

        # Order execution tracking
        self.order_execution_complete = False

        # Current order details for immediate execution
        self.current_lighter_side = None
        self.current_lighter_quantity = None
        self.current_lighter_price = None
        self.lighter_order_info = None

        # Lighter API configuration
        self.lighter_base_url = "https://mainnet.zklighter.elliot.ai"
        self.account_index = int(os.getenv('LIGHTER_ACCOUNT_INDEX'))
        self.api_key_index = int(os.getenv('LIGHTER_API_KEY_INDEX'))

        # GRVT configuration
        self.grvt_trading_account_id = os.getenv('GRVT_TRADING_ACCOUNT_ID')
        self.grvt_private_key = os.getenv('GRVT_PRIVATE_KEY')
        self.grvt_api_key = os.getenv('GRVT_API_KEY')
        self.grvt_environment = os.getenv('GRVT_ENVIRONMENT', 'prod')

    def shutdown(self, signum=None, frame=None):
        """Graceful shutdown handler."""
        self.stop_flag = True
        self.logger.info("\n🛑 Stopping...")

        # Close WebSocket connections
        if self.grvt_client:
            try:
                # Note: disconnect() is async, but shutdown() is sync
                # We'll let the cleanup happen naturally
                self.logger.info("🔌 GRVT WebSocket will be disconnected")
            except Exception as e:
                self.logger.error(f"Error disconnecting GRVT WebSocket: {e}")

        # Cancel Lighter WebSocket task
        if self.lighter_ws_task and not self.lighter_ws_task.done():
            try:
                self.lighter_ws_task.cancel()
                self.logger.info("🔌 Lighter WebSocket task cancelled")
            except Exception as e:
                self.logger.error(f"Error cancelling Lighter WebSocket task: {e}")
                
        # Cancel position monitor task
        if self.position_monitor_task and not self.position_monitor_task.done():
            try:
                self.position_monitor_task.cancel()
                self.logger.info("🔌 Position monitor task cancelled")
            except Exception as e:
                self.logger.error(f"Error cancelling position monitor task: {e}")

        # Close logging handlers properly
        for handler in self.logger.handlers[:]:
            try:
                handler.close()
                self.logger.removeHandler(handler)
            except Exception:
                pass

    async def async_shutdown(self):
        """Async shutdown handler for proper resource cleanup."""
        self.stop_flag = True
        self.logger.info("\n🛑 Stopping...")

        # Close GRVT WebSocket
        if self.grvt_client and hasattr(self.grvt_client, 'disconnect'):
            try:
                await self.grvt_client.disconnect()
                self.logger.info("🔌 GRVT WebSocket disconnected")
            except Exception as e:
                self.logger.error(f"Error disconnecting GRVT WebSocket: {e}")

        # Cancel Lighter WebSocket task
        if self.lighter_ws_task and not self.lighter_ws_task.done():
            try:
                self.lighter_ws_task.cancel()
                await asyncio.gather(self.lighter_ws_task, return_exceptions=True)
                self.logger.info("🔌 Lighter WebSocket task cancelled")
            except Exception as e:
                self.logger.error(f"Error cancelling Lighter WebSocket task: {e}")

        # Close logging handlers properly
        for handler in self.logger.handlers[:]:
            try:
                handler.close()
                self.logger.removeHandler(handler)
            except Exception:
                pass

    def _initialize_csv_file(self):
        """Initialize CSV file with headers if it doesn't exist."""
        if not os.path.exists(self.csv_filename):
            with open(self.csv_filename, 'w', newline='') as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(['exchange', 'timestamp', 'side', 'price', 'quantity'])

    def log_trade_to_csv(self, exchange: str, side: str, price: str, quantity: str):
        """Log trade details to CSV file."""
        timestamp = datetime.now(pytz.UTC).isoformat()

        with open(self.csv_filename, 'a', newline='') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow([
                exchange,
                timestamp,
                side,
                price,
                quantity
            ])

        self.logger.info(f"📊 Trade logged to CSV: {exchange} {side} {quantity} @ {price}")

    def handle_lighter_order_result(self, order_data):
        """Handle Lighter order result from WebSocket."""
        try:
            order_data["avg_filled_price"] = (Decimal(order_data["filled_quote_amount"]) /
                                              Decimal(order_data["filled_base_amount"]))
            if order_data["is_ask"]:
                order_data["side"] = "SHORT"
                order_type = "OPEN"
                self.lighter_position -= Decimal(order_data["filled_base_amount"])  # 賣出增加空頭持倉
            else:
                order_data["side"] = "LONG"
                order_type = "CLOSE"
                self.lighter_position += Decimal(order_data["filled_base_amount"])  # 買入增加多頭持倉

            client_order_index = order_data["client_order_id"]

            self.logger.info(f"[{client_order_index}] [{order_type}] [Lighter] [FILLED]: "
                             f"{order_data['filled_base_amount']} @ {order_data['avg_filled_price']}")

            # Log Lighter trade to CSV
            self.log_trade_to_csv(
                exchange='Lighter',
                side=order_data['side'],
                price=str(order_data['avg_filled_price']),
                quantity=str(order_data['filled_base_amount'])
            )

            # Mark execution as complete
            self.lighter_order_filled = True  # Mark order as filled
            self.order_execution_complete = True

        except Exception as e:
            self.logger.error(f"Error handling Lighter order result: {e}")

    async def reset_lighter_order_book(self):
        """Reset Lighter order book state."""
        async with self.lighter_order_book_lock:
            self.lighter_order_book["bids"].clear()
            self.lighter_order_book["asks"].clear()
            self.lighter_order_book_offset = 0
            self.lighter_order_book_sequence_gap = False
            self.lighter_snapshot_loaded = False
            self.lighter_best_bid = None
            self.lighter_best_ask = None

    def update_lighter_order_book(self, side: str, levels: list):
        """Update Lighter order book with new levels."""
        for level in levels:
            # Handle different data structures - could be list [price, size] or dict {"price": ..., "size": ...}
            if isinstance(level, list) and len(level) >= 2:
                price = Decimal(level[0])
                size = Decimal(level[1])
            elif isinstance(level, dict):
                price = Decimal(level.get("price", 0))
                size = Decimal(level.get("size", 0))
            else:
                self.logger.warning(f"⚠️ Unexpected level format: {level}")
                continue

            if size > 0:
                self.lighter_order_book[side][price] = size
            else:
                # Remove zero size orders
                self.lighter_order_book[side].pop(price, None)

    def validate_order_book_offset(self, new_offset: int) -> bool:
        """Validate order book offset sequence."""
        if new_offset <= self.lighter_order_book_offset:
            self.logger.warning(
                f"⚠️ Out-of-order update: new_offset={new_offset}, current_offset={self.lighter_order_book_offset}")
            return False
        return True

    def validate_order_book_integrity(self) -> bool:
        """Validate order book integrity."""
        # Check for negative prices or sizes
        for side in ["bids", "asks"]:
            for price, size in self.lighter_order_book[side].items():
                if price <= 0 or size <= 0:
                    self.logger.error(f"❌ Invalid order book data: {side} price={price}, size={size}")
                    return False
        return True

    def get_lighter_best_levels(self) -> Tuple[Tuple[Decimal, Decimal], Tuple[Decimal, Decimal]]:
        """Get best bid and ask levels from Lighter order book."""
        best_bid = None
        best_ask = None

        if self.lighter_order_book["bids"]:
            best_bid_price = max(self.lighter_order_book["bids"].keys())
            best_bid_size = self.lighter_order_book["bids"][best_bid_price]
            best_bid = (best_bid_price, best_bid_size)

        if self.lighter_order_book["asks"]:
            best_ask_price = min(self.lighter_order_book["asks"].keys())
            best_ask_size = self.lighter_order_book["asks"][best_ask_price]
            best_ask = (best_ask_price, best_ask_size)

        return best_bid, best_ask

    def get_lighter_mid_price(self) -> Decimal:
        """Get mid price from Lighter order book."""
        best_bid, best_ask = self.get_lighter_best_levels()

        if best_bid is None or best_ask is None:
            raise Exception("Cannot calculate mid price - missing order book data")

        mid_price = (best_bid[0] + best_ask[0]) / Decimal('2')
        return mid_price

    def get_lighter_order_price(self, is_ask: bool) -> Decimal:
        """Get order price from Lighter order book."""
        best_bid, best_ask = self.get_lighter_best_levels()

        if best_bid is None or best_ask is None:
            raise Exception("Cannot calculate order price - missing order book data")

        if is_ask:
            order_price = best_bid[0] + Decimal('0.1')
        else:
            order_price = best_ask[0] - Decimal('0.1')

        return order_price

    def calculate_adjusted_price(self, original_price: Decimal, side: str, adjustment_percent: Decimal) -> Decimal:
        """Calculate adjusted price for order modification."""
        adjustment = original_price * adjustment_percent

        if side.lower() == 'buy':
            # For buy orders, increase price to improve fill probability
            return original_price + adjustment
        else:
            # For sell orders, decrease price to improve fill probability
            return original_price - adjustment

    async def request_fresh_snapshot(self, ws):
        """Request fresh order book snapshot."""
        await ws.send(json.dumps({"type": "subscribe", "channel": f"order_book/{self.lighter_market_index}"}))

    async def handle_lighter_ws(self):
        """Handle Lighter WebSocket connection and messages."""
        url = "wss://mainnet.zklighter.elliot.ai/stream"
        cleanup_counter = 0

        while not self.stop_flag:
            timeout_count = 0
            try:
                # Reset order book state before connecting
                await self.reset_lighter_order_book()

                async with websockets.connect(url) as ws:
                    # Subscribe to order book updates
                    await ws.send(json.dumps({"type": "subscribe", "channel": f"order_book/{self.lighter_market_index}"}))

                    # Subscribe to account orders updates
                    account_orders_channel = f"account_orders/{self.lighter_market_index}/{self.account_index}"

                    # Get auth token for the subscription
                    try:
                        # Set auth token to expire in 10 minutes
                        ten_minutes_deadline = int(time.time() + 10 * 60)
                        auth_token, err = self.lighter_client.create_auth_token_with_expiry(ten_minutes_deadline)
                        if err is not None:
                            self.logger.warning(f"⚠️ Failed to create auth token for account orders subscription: {err}")
                        else:
                            auth_message = {
                                "type": "subscribe",
                                "channel": account_orders_channel,
                                "auth": auth_token
                            }
                            await ws.send(json.dumps(auth_message))
                            self.logger.info("✅ Subscribed to account orders with auth token (expires in 10 minutes)")
                    except Exception as e:
                        self.logger.warning(f"⚠️ Error creating auth token for account orders subscription: {e}")

                    while not self.stop_flag:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=1)

                            try:
                                data = json.loads(msg)
                            except json.JSONDecodeError as e:
                                self.logger.warning(f"⚠️ JSON parsing error in Lighter websocket: {e}")
                                continue

                            # Reset timeout counter on successful message
                            timeout_count = 0

                            async with self.lighter_order_book_lock:
                                if data.get("type") == "subscribed/order_book":
                                    # Initial snapshot - clear and populate the order book
                                    self.lighter_order_book["bids"].clear()
                                    self.lighter_order_book["asks"].clear()

                                    # Handle the initial snapshot
                                    order_book = data.get("order_book", {})
                                    if order_book and "offset" in order_book:
                                        self.lighter_order_book_offset = order_book["offset"]
                                        self.logger.info(f"✅ Initial order book offset set to: {self.lighter_order_book_offset}")

                                    # Debug: Log the structure of bids and asks
                                    bids = order_book.get("bids", [])
                                    asks = order_book.get("asks", [])
                                    if bids:
                                        self.logger.debug(f"📊 Sample bid structure: {bids[0] if bids else 'None'}")
                                    if asks:
                                        self.logger.debug(f"📊 Sample ask structure: {asks[0] if asks else 'None'}")

                                    self.update_lighter_order_book("bids", bids)
                                    self.update_lighter_order_book("asks", asks)
                                    self.lighter_snapshot_loaded = True
                                    self.lighter_order_book_ready = True

                                    self.logger.info(f"✅ Lighter order book snapshot loaded with "
                                                     f"{len(self.lighter_order_book['bids'])} bids and "
                                                     f"{len(self.lighter_order_book['asks'])} asks")

                                elif data.get("type") == "update/order_book" and self.lighter_snapshot_loaded:
                                    # Extract offset from the message
                                    order_book = data.get("order_book", {})
                                    if not order_book or "offset" not in order_book:
                                        self.logger.warning("⚠️ Order book update missing offset, skipping")
                                        continue

                                    new_offset = order_book["offset"]

                                    # Validate offset sequence
                                    if not self.validate_order_book_offset(new_offset):
                                        self.lighter_order_book_sequence_gap = True
                                        break

                                    # Update the order book with new data
                                    self.update_lighter_order_book("bids", order_book.get("bids", []))
                                    self.update_lighter_order_book("asks", order_book.get("asks", []))

                                    # Validate order book integrity after update
                                    if not self.validate_order_book_integrity():
                                        self.logger.warning("🔄 Order book integrity check failed, requesting fresh snapshot...")
                                        break

                                    # Get the best bid and ask levels
                                    best_bid, best_ask = self.get_lighter_best_levels()

                                    # Update global variables
                                    if best_bid is not None:
                                        self.lighter_best_bid = best_bid[0]
                                    if best_ask is not None:
                                        self.lighter_best_ask = best_ask[0]

                                elif data.get("type") == "ping":
                                    # Respond to ping with pong
                                    await ws.send(json.dumps({"type": "pong"}))
                                elif data.get("type") == "update/account_orders":
                                    # Handle account orders updates
                                    orders = data.get("orders", {}).get(str(self.lighter_market_index), [])
                                    for order in orders:
                                        if order.get("status") == "filled":
                                            self.handle_lighter_order_result(order)
                                elif data.get("type") == "update/order_book" and not self.lighter_snapshot_loaded:
                                    # Ignore updates until we have the initial snapshot
                                    continue

                            # Periodic cleanup outside the lock
                            cleanup_counter += 1
                            if cleanup_counter >= 1000:
                                cleanup_counter = 0

                            # Handle sequence gap and integrity issues outside the lock
                            if self.lighter_order_book_sequence_gap:
                                try:
                                    await self.request_fresh_snapshot(ws)
                                    self.lighter_order_book_sequence_gap = False
                                except Exception as e:
                                    self.logger.error(f"⚠️ Failed to request fresh snapshot: {e}")
                                    break

                        except asyncio.TimeoutError:
                            timeout_count += 1
                            if timeout_count % 3 == 0:
                                self.logger.warning(f"⏰ No message from Lighter websocket for {timeout_count} seconds")
                            continue
                        except websockets.exceptions.ConnectionClosed as e:
                            self.logger.warning(f"⚠️ Lighter websocket connection closed: {e}")
                            break
                        except websockets.exceptions.WebSocketException as e:
                            self.logger.warning(f"⚠️ Lighter websocket error: {e}")
                            break
                        except Exception as e:
                            self.logger.error(f"⚠️ Error in Lighter websocket: {e}")
                            self.logger.error(f"⚠️ Full traceback: {traceback.format_exc()}")
                            break
            except Exception as e:
                self.logger.error(f"⚠️ Failed to connect to Lighter websocket: {e}")

            # Wait a bit before reconnecting
            await asyncio.sleep(2)

    def setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown."""
        signal.signal(signal.SIGINT, self.shutdown)
        signal.signal(signal.SIGTERM, self.shutdown)

    def initialize_lighter_client(self):
        """Initialize the Lighter client."""
        if self.lighter_client is None:            
            api_key_private_key = os.getenv('API_KEY_PRIVATE_KEY')
            if not api_key_private_key:
                raise Exception("API_KEY_PRIVATE_KEY environment variable not set")

            self.lighter_client = SignerClient(
                url=self.lighter_base_url,
                private_key=api_key_private_key,
                account_index=self.account_index,
                api_key_index=self.api_key_index,
            )

            # Check client
            err = self.lighter_client.check_client()
            if err is not None:
                raise Exception(f"CheckClient error: {err}")

            self.logger.info("✅ Lighter client initialized successfully")
        return self.lighter_client

    def initialize_grvt_client(self):
        """Initialize the GRVT client."""
        if not all([self.grvt_trading_account_id, self.grvt_private_key, self.grvt_api_key]):
            raise ValueError("GRVT_TRADING_ACCOUNT_ID, GRVT_PRIVATE_KEY, and GRVT_API_KEY must be set in environment variables")

        # Create config for GRVT client
        config_dict = {
            'ticker': self.ticker,
            'contract_id': '',  # Will be set when we get contract info
            'quantity': self.order_quantity,
            'tick_size': Decimal('0.01'),  # Will be updated when we get contract info
            'close_order_side': 'sell',  # Default, will be updated based on strategy
            'direction': 'buy'  # Add direction attribute for GRVT client
        }

        # Wrap in Config class for GRVT client
        config = Config(config_dict)

        # Initialize GRVT client
        self.grvt_client = GrvtClient(config)

        self.logger.info("✅ GRVT client initialized successfully")
        return self.grvt_client

    def get_lighter_market_config(self) -> Tuple[int, int, int]:
        """Get Lighter market configuration."""
        url = f"{self.lighter_base_url}/api/v1/orderBooks"
        headers = {"accept": "application/json"}

        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()

            if not response.text.strip():
                raise Exception("Empty response from Lighter API")

            data = response.json()

            if "order_books" not in data:
                raise Exception("Unexpected response format")

            for market in data["order_books"]:
                if market["symbol"] == self.ticker:
                    return (market["market_id"],
                            pow(10, market["supported_size_decimals"]),
                            pow(10, market["supported_price_decimals"]))

            raise Exception(f"Ticker {self.ticker} not found")

        except Exception as e:
            self.logger.error(f"⚠️ Error getting market config: {e}")
            raise

    async def get_grvt_contract_info(self) -> Tuple[str, Decimal]:
        """Get GRVT contract ID and tick size."""
        if not self.grvt_client:
            raise Exception("GRVT client not initialized")

        contract_id, tick_size = await self.grvt_client.get_contract_attributes()

        if self.order_quantity < self.grvt_client.config.quantity:
            raise ValueError(
                f"Order quantity is less than min quantity: {self.order_quantity} < {self.grvt_client.config.quantity}")

        return contract_id, tick_size

    async def fetch_grvt_bbo_prices(self) -> Tuple[Decimal, Decimal]:
        """Fetch best bid/ask prices from GRVT using REST API."""
        if not self.grvt_client:
            raise Exception("GRVT client not initialized")

        best_bid, best_ask = await self.grvt_client.fetch_bbo_prices(self.grvt_contract_id)
        return best_bid, best_ask

    def round_to_tick(self, price: Decimal) -> Decimal:
        """Round price to tick size."""
        if self.grvt_tick_size is None:
            return price
        return (price / self.grvt_tick_size).quantize(Decimal('1')) * self.grvt_tick_size

    async def place_bbo_order(self, side: str, quantity: Decimal):
        # Get best bid/ask prices
        best_bid, best_ask = await self.fetch_grvt_bbo_prices()

        # Place the order using GRVT client
        order_result = await self.grvt_client.place_open_order(
            contract_id=self.grvt_contract_id,
            quantity=quantity,
            direction=side.lower()
        )

        if order_result.success:
            return order_result.order_id
        else:
            raise Exception(f"Failed to place order: {order_result.error_message}")

    async def place_grvt_post_only_order(self, side: str, quantity: Decimal):
        """Place a post-only order on GRVT with improved fill strategy."""
        if not self.grvt_client:
            raise Exception("GRVT client not initialized")

        self.grvt_order_status = None
        self.logger.info(f"[OPEN] [GRVT] [{side}] Placing GRVT POST-ONLY order")
        
        # 重試機制：最多重試 3 次，每次調整價格
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries and not self.stop_flag:
            try:
                order_id = await self.place_bbo_order(side, quantity)
                start_time = time.time()
                
                # 等待成交，縮短超時時間
                timeout_duration = 8 if retry_count == 0 else 5  # 第一次給更多時間
                
                while not self.stop_flag:
                    if self.grvt_order_status == 'CANCELED':
                        self.grvt_order_status = None
                        break  # 跳出內層循環，重新下單
                    elif self.grvt_order_status in ['NEW', 'OPEN', 'PENDING', 'CANCELING', 'PARTIALLY_FILLED']:
                        await asyncio.sleep(0.1)  # 縮短檢查間隔到 100ms
                        if time.time() - start_time > timeout_duration:
                            try:
                                # 取消訂單
                                cancel_result = await self.grvt_client.cancel_order(order_id)
                                if cancel_result.success:
                                    self.grvt_order_status = 'CANCELED'
                                    self.logger.warning(f"⚠️ Order timeout after {timeout_duration}s, retrying...")
                                else:
                                    self.logger.error(f"❌ Error canceling GRVT order: {cancel_result.error_message}")
                            except Exception as e:
                                self.logger.error(f"❌ Error canceling GRVT order: {e}")
                            break
                    elif self.grvt_order_status == 'FILLED':
                        self.logger.info(f"✅ Order filled successfully on attempt {retry_count + 1}")
                        return  # 成功成交，退出函數
                    else:
                        if self.grvt_order_status is not None:
                            self.logger.error(f"❌ Unknown GRVT order status: {self.grvt_order_status}")
                            break
                        else:
                            # Wait for order status update
                            await asyncio.sleep(0.1)  # 縮短檢查間隔到 100ms
                            # Check for timeout if no status update
                            if time.time() - start_time > timeout_duration + 5:
                                self.logger.error("❌ Timeout waiting for order status update")
                                break
                
                # 如果沒有成交，增加重試計數
                if self.grvt_order_status != 'FILLED':
                    retry_count += 1
                    if retry_count < max_retries:
                        self.logger.warning(f"⚠️ Order not filled, retrying ({retry_count}/{max_retries})...")
                        await asyncio.sleep(1)  # 短暫等待後重試
                    else:
                        self.logger.error(f"❌ Failed to fill order after {max_retries} attempts")
                        break
                else:
                    break  # 成功成交，退出重試循環
                    
            except Exception as e:
                retry_count += 1
                self.logger.error(f"❌ Error placing order (attempt {retry_count}): {e}")
                if retry_count < max_retries:
                    await asyncio.sleep(1)
                else:
                    break

    def handle_grvt_order_update(self, order_data):
        """Handle GRVT order updates from WebSocket - 觸發實際持倉檢查."""
        side = order_data.get('side', '').lower()
        filled_size = Decimal(order_data.get('filled_size', '0'))
        price = Decimal(order_data.get('price', '0'))

        # 更新 GRVT 持倉
        if side == 'buy':
            self.grvt_position += filled_size
        else:
            self.grvt_position -= filled_size

        self.logger.info(f"📡 GRVT order update: {side} {filled_size} @ {price}")
        self.logger.info(f"🔄 GRVT position updated to: {self.grvt_position}")
        
        # 計算對沖方向
        if side == 'buy':
            lighter_side = 'sell'  # GRVT 買入，Lighter 賣出對沖
        else:
            lighter_side = 'buy'   # GRVT 賣出，Lighter 買入對沖
        
        # 設置對沖參數
        self.current_lighter_side = lighter_side
        self.current_lighter_quantity = filled_size  # 使用成交數量
        self.current_lighter_price = price
        
        # 設置對沖寬限期 (1秒)
        import time
        self.hedge_grace_until = time.time() + self.hedge_grace_period
        self.hedge_in_progress = True
        self.waiting_for_lighter_fill = True
        
        # 立即觸發對沖檢查，減少延遲
        self.logger.info(f"🚀 Immediate hedge trigger for {filled_size} {lighter_side} @ {price}")
        
        self.logger.info(f"🔄 Hedge calculation: GRVT position={self.grvt_position}, hedge_quantity={filled_size}")

    async def get_grvt_position(self):
        """獲取 GRVT 實際持倉 - 帶速率限制"""
        try:
            if not self.grvt_client:
                return Decimal('0')
            
            # 檢查速率限制
            current_time = time.time()
            if current_time - self.last_grvt_position_call < self.grvt_rate_limit:
                self.logger.debug(f"GRVT API rate limit, skipping call (last: {current_time - self.last_grvt_position_call:.1f}s ago)")
                return self.grvt_position  # 返回緩存的持倉而不是 0
            
            # 使用 GRVT SDK 的 fetch_positions 方法獲取實際持倉
            positions = self.grvt_client.rest_client.fetch_positions(symbols=[self.grvt_contract_id])
            self.last_grvt_position_call = current_time
            
            if positions:
                self.logger.debug(f"🔍 GRVT positions raw data: {positions}")
                for position in positions:
                    if position.get('instrument') == self.grvt_contract_id:
                        # GRVT position size: 負數=空頭, 正數=多頭
                        position_size = Decimal(str(position.get('size', '0')))
                        self.logger.info(f"📊 GRVT actual position: {position_size} (from API)")
                        self.logger.debug(f"🔍 GRVT position details: {position}")
                        return position_size
            
            self.logger.info("📊 GRVT actual position: 0 (no positions found)")
            return Decimal('0')
            
        except Exception as e:
            # 如果是速率限制錯誤，不記錄錯誤，返回緩存持倉
            if "429" in str(e) or "rate limit" in str(e).lower():
                self.logger.debug(f"GRVT API rate limit, using cached position: {self.grvt_position}")
                return self.grvt_position
            self.logger.error(f"❌ Error fetching GRVT position: {e}")
            self.logger.error(f"❌ Error details: {traceback.format_exc()}")
            return self.grvt_position  # 出錯時返回緩存持倉

    async def get_lighter_position(self):
        """獲取 Lighter 實際持倉 - 帶速率限制"""
        try:
            if not self.lighter_client:
                return Decimal('0')
            
            # 檢查速率限制
            current_time = time.time()
            if current_time - self.last_lighter_position_call < self.lighter_rate_limit:
                self.logger.debug(f"Lighter API rate limit, skipping call (last: {current_time - self.last_lighter_position_call:.1f}s ago)")
                return self.lighter_position  # 返回緩存的持倉而不是 0
            
            # 使用 Lighter API 獲取持倉信息
            from lighter.api.account_api import AccountApi
            account_api = AccountApi(self.lighter_client.api_client)
            
            # 獲取賬戶信息
            account_data = await account_api.account(by="index", value=str(self.account_index))
            self.last_lighter_position_call = current_time
            
            if account_data and account_data.accounts:
                account = account_data.accounts[0]
                self.logger.debug(f"🔍 Lighter account data: {account}")
                if hasattr(account, 'positions') and account.positions:
                    self.logger.debug(f"🔍 Lighter positions raw: {account.positions}")
                    for position in account.positions:
                        if int(position.market_id) == self.lighter_market_index:
                            # Lighter position: position 字段是絕對值，sign 字段表示方向
                            # sign: 1 = 多頭, -1 = 空頭
                            position_abs = Decimal(str(position.position))
                            position_sign = int(position.sign) if hasattr(position, 'sign') else 1
                            position_size = position_abs * position_sign
                            
                            self.logger.info(f"📊 Lighter actual position: {position_size} (from API)")
                            self.logger.debug(f"🔍 Lighter position details: market_id={position.market_id}, position={position.position}, sign={position_sign}")
                            return position_size
            
            self.logger.info(f"📊 Lighter actual position: 0 (no positions found)")
            return Decimal('0')
            
        except Exception as e:
            # 如果是速率限制錯誤，不記錄錯誤，返回緩存持倉
            if "429" in str(e) or "rate limit" in str(e).lower() or "Too Many Requests" in str(e):
                self.logger.debug(f"Lighter API rate limit, using cached position: {self.lighter_position}")
                return self.lighter_position
            self.logger.error(f"❌ Error fetching Lighter position: {e}")
            self.logger.error(f"❌ Error details: {traceback.format_exc()}")
            return self.lighter_position  # 出錯時返回緩存持倉

    async def position_monitor(self):
        """持倉監控任務 - 每 2 秒檢查一次持倉，發現不匹配立即對沖"""
        await asyncio.sleep(5)  # 等待初始化完成
        
        while not self.stop_flag:
            try:
                # 獲取實際持倉
                grvt_pos = await self.get_grvt_position()
                lighter_pos = await self.get_lighter_position()
                
                # 檢查持倉匹配 - 正確對沖時兩邊持倉應該相加為 0
                # GRVT +0.01 (多頭) + Lighter -0.01 (空頭) = 0 ✅
                position_diff = abs(grvt_pos + lighter_pos)
                if position_diff > Decimal('0.001'):
                    self.logger.warning(f"⚠️ Position mismatch detected: GRVT={grvt_pos}, Lighter={lighter_pos}, diff={position_diff}")
                    
                    # 緊急對沖：修復不匹配的持倉 - 確保持倉完全一致
                    if position_diff > Decimal('0.001'):
                        # 正確的對沖邏輯：GRVT 和 Lighter 應該方向相反，總和為 0
                        # 目標：grvt_pos + lighter_pos = 0
                        # 所以：target_lighter_pos = -grvt_pos
                        target_lighter_pos = -grvt_pos
                        hedge_quantity = abs(target_lighter_pos - lighter_pos)
                        
                        if target_lighter_pos > lighter_pos:
                            # 需要增加 Lighter 持倉（買入）
                            lighter_side = 'buy'
                        else:
                            # 需要減少 Lighter 持倉（賣出）
                            lighter_side = 'sell'
                        
                        self.logger.warning(f"🚨 Position mismatch hedge:")
                        self.logger.warning(f"   GRVT: {grvt_pos}")
                        self.logger.warning(f"   Lighter Current: {lighter_pos}")
                        self.logger.warning(f"   Lighter Target: {target_lighter_pos}")
                        self.logger.warning(f"   → Need to {lighter_side} {hedge_quantity}")
                        
                        # 設置對沖參數
                        self.current_lighter_side = lighter_side
                        self.current_lighter_quantity = hedge_quantity
                        self.current_lighter_price = Decimal('0')  # 市價單
                        self.waiting_for_lighter_fill = True
                        
                        # 立即執行市價對沖
                        await self.place_lighter_market_order(lighter_side, hedge_quantity, Decimal('0'))
                        
                else:
                    self.logger.debug(f"✅ Positions match: GRVT={grvt_pos}, Lighter={lighter_pos}")
                
                # 等待 1.0 秒，與 GRVT 速率限制對齊
                # GRVT Level 3-4 允許每秒 7.5-10 次讀取操作，我們每秒只做 1 次
                await asyncio.sleep(1.0)
                
            except Exception as e:
                self.logger.error(f"❌ Error in position monitor: {e}")
                await asyncio.sleep(1.0)

    async def cancel_all_grvt_orders(self):
        """取消所有未成交的 GRVT 訂單 - 使用 GRVT SDK 的 cancel_all_orders 方法"""
        try:
            if not self.grvt_client:
                return
            
            # 使用 GRVT REST 客戶端的 cancel_all_orders 方法，指定 PERPETUAL 類型
            cancel_response = self.grvt_client.rest_client.cancel_all_orders(params={"kind": "PERPETUAL"})
            
            if cancel_response:
                self.logger.info("✅ Successfully canceled all GRVT orders")
            else:
                self.logger.info("✅ No active GRVT orders to cancel")
                
        except Exception as e:
            self.logger.error(f"❌ Error canceling GRVT orders: {e}")

    async def place_lighter_market_order(self, lighter_side: str, quantity: Decimal, price: Decimal):
        """真正的市價單對沖 - 使用市價單而不是限價單"""
        if not self.lighter_client:
            self.initialize_lighter_client()

        # 檢查參數有效性
        if lighter_side is None:
            self.logger.error("❌ lighter_side is None, cannot place order")
            return None
            
        if quantity is None or quantity <= 0:
            self.logger.error(f"❌ Invalid quantity: {quantity}")
            return None

        best_bid, best_ask = self.get_lighter_best_levels()

        # 市價單策略：使用更激進的價格確保立即成交
        if lighter_side.lower() == 'buy':
            order_type = "CLOSE"
            is_ask = False
            # 市價買入：使用更高的價格確保立即成交
            if best_ask and len(best_ask) >= 2:
                price = best_ask[0] * Decimal('1.02')  # 比最佳賣價高 2%
            else:
                self.logger.error("❌ No best ask price available")
                return None
        else:
            order_type = "OPEN"
            is_ask = True
            # 市價賣出：使用更低的價格確保立即成交
            if best_bid and len(best_bid) >= 2:
                price = best_bid[0] * Decimal('0.98')  # 比最佳買價低 2%
            else:
                self.logger.error("❌ No best bid price available")
                return None

        # Reset order state
        self.lighter_order_filled = False
        self.lighter_order_price = price
        self.lighter_order_side = lighter_side
        self.lighter_order_size = quantity

        try:
            client_order_index = int(time.time() * 1000)
            
            # 使用 Lighter 專用的市價單方法
            tx_hash = await self.lighter_client.create_market_order(
                market_index=self.lighter_market_index,
                client_order_index=client_order_index,
                base_amount=int(quantity * self.base_amount_multiplier),
                avg_execution_price=int(price * self.price_multiplier),  # 使用 avg_execution_price 參數
                is_ask=is_ask,
            )

            self.logger.info(f"[{client_order_index}] [{order_type}] [Lighter] [MARKET]: {quantity} @ {price}")

            # 市價單通常立即成交，縮短監控時間
            await self.monitor_lighter_market_order(client_order_index)

            return tx_hash
        except Exception as e:
            self.logger.error(f"❌ Error placing Lighter market order: {e}")
            self.logger.error(f"❌ Order details: side={lighter_side}, quantity={quantity}, price={price}")
            import traceback
            self.logger.error(f"❌ Full traceback: {traceback.format_exc()}")
            return None

    async def monitor_lighter_market_order(self, client_order_index: int):
        """監控市價單 - 市價單通常立即成交，使用更短的超時時間"""
        start_time = time.time()
        max_wait_time = 5  # 市價單最多等待 5 秒
        
        while not self.lighter_order_filled and not self.stop_flag:
            elapsed_time = time.time() - start_time
            
            if elapsed_time > max_wait_time:
                self.logger.error(f"❌ Market order timeout after {elapsed_time:.1f}s")
                # 市價單超時，直接標記為成交以避免阻塞
                self.lighter_order_filled = True
                self.waiting_for_lighter_fill = False
                self.order_execution_complete = True
                break

            await asyncio.sleep(0.01)  # 市價單檢查頻率更高 - 10ms

    async def monitor_lighter_order(self, client_order_index: int):
        """Monitor Lighter order with improved timeout and retry logic."""

        start_time = time.time()
        price_adjustment_count = 0
        max_price_adjustments = 2
        
        while not self.lighter_order_filled and not self.stop_flag:
            elapsed_time = time.time() - start_time
            
            # 縮短超時時間，增加價格調整
            if elapsed_time > 15:  # 從 30 秒縮短到 15 秒
                if price_adjustment_count < max_price_adjustments:
                    # 嘗試調整價格
                    try:
                        best_bid, best_ask = self.get_lighter_best_levels()
                        if self.lighter_order_side.lower() == 'buy':
                            new_price = best_ask[0] * Decimal('1.008')  # 更積極的價格
                        else:
                            new_price = best_bid[0] * Decimal('0.992')  # 更積極的價格
                        
                        await self.modify_lighter_order(client_order_index, new_price)
                        price_adjustment_count += 1
                        start_time = time.time()  # 重置計時器
                        self.logger.warning(f"⚠️ Price adjustment {price_adjustment_count}/{max_price_adjustments}: {new_price}")
                    except Exception as e:
                        self.logger.error(f"❌ Error adjusting price: {e}")
                        break
                else:
                    # 最終超時，使用 fallback
                    self.logger.error(f"❌ Timeout waiting for Lighter order fill after {elapsed_time:.1f}s")
                    self.logger.warning("⚠️ Using fallback - marking order as filled to continue trading")
                    self.lighter_order_filled = True
                    self.waiting_for_lighter_fill = False
                    self.order_execution_complete = True
                    break

            await asyncio.sleep(0.01)  # Check every 10ms for faster response

    async def modify_lighter_order(self, client_order_index: int, new_price: Decimal):
        """Modify current Lighter order with new price using client_order_index."""
        try:
            if client_order_index is None:
                self.logger.error("❌ Cannot modify order - no order ID available")
                return

            # Calculate new Lighter price
            lighter_price = int(new_price * self.price_multiplier)

            self.logger.info(f"🔧 Attempting to modify order - Market: {self.lighter_market_index}, "
                             f"Client Order Index: {client_order_index}, New Price: {lighter_price}")

            # Use the native SignerClient's modify_order method
            tx_info, tx_hash, error = await self.lighter_client.modify_order(
                market_index=self.lighter_market_index,
                order_index=client_order_index,  # Use client_order_index directly
                base_amount=int(self.lighter_order_size * self.base_amount_multiplier),
                price=lighter_price,
                trigger_price=0
            )

            if error is not None:
                self.logger.error(f"❌ Lighter order modification error: {error}")
                return

            self.lighter_order_price = new_price
            self.logger.info(f"🔄 Lighter order modified successfully: {self.lighter_order_side} "
                             f"{self.lighter_order_size} @ {new_price}")

        except Exception as e:
            self.logger.error(f"❌ Error modifying Lighter order: {e}")
            import traceback
            self.logger.error(f"❌ Full traceback: {traceback.format_exc()}")

    async def setup_grvt_websocket(self):
        """Setup GRVT websocket for order updates."""
        if not self.grvt_client:
            raise Exception("GRVT client not initialized")

        def order_update_handler(order_data):
            """Handle order updates from GRVT WebSocket."""
            if order_data.get('contract_id') != self.grvt_contract_id:
                return
            try:
                order_id = order_data.get('order_id')
                status = order_data.get('status')
                side = order_data.get('side', '').lower()
                filled_size = Decimal(order_data.get('filled_size', '0'))
                size = Decimal(order_data.get('size', '0'))
                price = order_data.get('price', '0')

                if side == 'buy':
                    order_type = "OPEN"
                else:
                    order_type = "CLOSE"

                if status == 'CANCELED' and filled_size > 0:
                    status = 'FILLED'

                # Handle the order update - 處理 FILLED 和 PARTIALLY_FILLED
                if (status == 'FILLED' or status == 'PARTIALLY_FILLED') and filled_size > 0:
                    if side == 'buy':
                        self.grvt_position += filled_size
                    else:
                        self.grvt_position -= filled_size
                    self.logger.info(f"[{order_id}] [{order_type}] [GRVT] [{status}]: {filled_size} @ {price}")
                    self.grvt_order_status = status

                    # Log GRVT trade to CSV
                    self.log_trade_to_csv(
                        exchange='GRVT',
                        side=side,
                        price=str(price),
                        quantity=str(filled_size)
                    )

                    # 觸發對沖 - 即使只是部分成交也要對沖
                    self.logger.info(f"🔄 Triggering hedge for {filled_size} {side} @ {price}")
                    self.handle_grvt_order_update({
                        'order_id': order_id,
                        'side': side,
                        'status': status,
                        'size': size,
                        'price': price,
                        'contract_id': self.grvt_contract_id,
                        'filled_size': filled_size
                    })
                elif self.grvt_order_status != 'FILLED':
                    if status == 'OPEN':
                        self.logger.info(f"[{order_id}] [{order_type}] [GRVT] [{status}]: {size} @ {price}")
                    else:
                        self.logger.info(f"[{order_id}] [{order_type}] [GRVT] [{status}]: {filled_size} @ {price}")
                    self.grvt_order_status = status

            except Exception as e:
                self.logger.error(f"Error handling GRVT order update: {e}")

        try:
            # Setup order update handler
            self.grvt_client.setup_order_update_handler(order_update_handler)
            self.logger.info("✅ GRVT WebSocket order update handler set up")

            # Connect to GRVT WebSocket
            await self.grvt_client.connect()
            self.logger.info("✅ GRVT WebSocket connection established")

        except Exception as e:
            self.logger.error(f"Could not setup GRVT WebSocket handlers: {e}")

    async def trading_loop(self):
        """Main trading loop implementing the new strategy."""
        self.logger.info(f"🚀 Starting hedge bot for {self.ticker}")

        # Initialize clients
        try:
            self.initialize_lighter_client()
            self.initialize_grvt_client()

            # Get contract info
            self.grvt_contract_id, self.grvt_tick_size = await self.get_grvt_contract_info()
            self.lighter_market_index, self.base_amount_multiplier, self.price_multiplier = self.get_lighter_market_config()

            self.logger.info(f"Contract info loaded - GRVT: {self.grvt_contract_id}, "
                             f"Lighter: {self.lighter_market_index}")

        except Exception as e:
            self.logger.error(f"❌ Failed to initialize: {e}")
            return

        # Setup GRVT websocket
        try:
            await self.setup_grvt_websocket()
            self.logger.info("✅ GRVT WebSocket connection established")

        except Exception as e:
            self.logger.error(f"❌ Failed to setup GRVT websocket: {e}")
            return

        # Setup Lighter websocket
        try:
            self.lighter_ws_task = asyncio.create_task(self.handle_lighter_ws())
            self.logger.info("✅ Lighter WebSocket task started")

            # Wait for initial Lighter order book data with timeout
            self.logger.info("⏳ Waiting for initial Lighter order book data...")
            timeout = 10  # seconds
            start_time = time.time()
            while not self.lighter_order_book_ready and not self.stop_flag:
                if time.time() - start_time > timeout:
                    self.logger.warning(f"⚠️ Timeout waiting for Lighter WebSocket order book data after {timeout}s")
                    break
                await asyncio.sleep(0.5)

            if self.lighter_order_book_ready:
                self.logger.info("✅ Lighter WebSocket order book data received")
            else:
                self.logger.warning("⚠️ Lighter WebSocket order book not ready")

        except Exception as e:
            self.logger.error(f"❌ Failed to setup Lighter websocket: {e}")
            return
            
        # 啟動持倉監控任務
        self.position_monitor_task = asyncio.create_task(self.position_monitor())
        self.logger.info("✅ Position monitor task started")
        
        # 顯示速率限制設置
        self.logger.info(f"📊 API Rate Limits:")
        self.logger.info(f"   GRVT: {self.grvt_rate_limit}s interval (~{int(60/self.grvt_rate_limit)} calls/min)")
        self.logger.info(f"   GRVT Level 3-4: 允許 75-100 次讀取操作/10秒 (450-600 calls/min)")
        if self.lighter_account_type == 'premium':
            self.logger.info(f"   Lighter: {self.lighter_rate_limit}s interval (premium: 24000 calls/min)")
        else:
            self.logger.info(f"   Lighter: {self.lighter_rate_limit}s interval (standard: 60 calls/min)")
        self.logger.info(f"   Position Monitor: 1s interval")
        self.logger.info(f"   Trading Cycle: 5s cooldown between cycles")

        await asyncio.sleep(5)

        iterations = 0
        while iterations < self.iterations and not self.stop_flag:
            iterations += 1
            self.logger.info("-----------------------------------------------")
            self.logger.info(f"🔄 Trading loop iteration {iterations}")
            self.logger.info("-----------------------------------------------")

            self.logger.info(f"[STEP 1] GRVT position: {self.grvt_position} | Lighter position: {self.lighter_position}")

            if abs(self.grvt_position + self.lighter_position) > 0.2:
                self.logger.error(f"❌ Position diff is too large: {self.grvt_position + self.lighter_position}")
                break

            self.order_execution_complete = False
            self.waiting_for_lighter_fill = False
            try:
                # Determine side based on some logic (for now, alternate)
                side = 'buy'
                await self.place_grvt_post_only_order(side, self.order_quantity)
            except Exception as e:
                self.logger.error(f"⚠️ Error in trading loop: {e}")
                self.logger.error(f"⚠️ Full traceback: {traceback.format_exc()}")
                break

            # 等待 GRVT WebSocket 觸發對沖
            start_time = time.time()
            check_counter = 0
            
            while not self.order_execution_complete and not self.stop_flag:
                # 檢查是否已經有對沖觸發（主要機制）
                if self.waiting_for_lighter_fill:
                    await self.place_lighter_market_order(
                        self.current_lighter_side,
                        self.current_lighter_quantity,
                        self.current_lighter_price
                    )
                    break
                
                # 每 5 次循環（0.5秒）檢查一次持倉作為備用機制
                check_counter += 1
                if check_counter >= 5:
                    check_counter = 0
                    current_grvt_pos = await self.get_grvt_position()
                    
                    # 備用觸發：如果 GRVT 有持倉但還沒觸發對沖
                    if current_grvt_pos != Decimal('0') and not self.waiting_for_lighter_fill:
                        self.logger.warning(f"⚠️ Backup hedge trigger: GRVT position={current_grvt_pos}")
                        lighter_side = 'sell' if current_grvt_pos > 0 else 'buy'
                        hedge_quantity = abs(current_grvt_pos)
                        
                        self.current_lighter_side = lighter_side
                        self.current_lighter_quantity = hedge_quantity
                        self.current_lighter_price = Decimal('0')
                        self.waiting_for_lighter_fill = True
                        continue  # 下一輪循環會執行對沖

                await asyncio.sleep(0.1)
                if time.time() - start_time > 60:  # 縮短超時時間到 60 秒
                    self.logger.error("❌ Timeout waiting for trade completion")
                    break

            if self.stop_flag:
                break

            # Close position
            self.logger.info(f"[STEP 2] GRVT position: {self.grvt_position} | Lighter position: {self.lighter_position}")
            
            # 獲取並顯示實際 GRVT 和 Lighter 持倉（每 1 秒監控）
            actual_grvt_position = await self.get_grvt_position()
            actual_lighter_position = await self.get_lighter_position()
            self.logger.info(f"📊 GRVT actual position: {actual_grvt_position}")
            self.logger.info(f"📊 Lighter actual position: {actual_lighter_position}")
            
            # 檢查持倉是否匹配 - 正確對沖時兩邊持倉應該相加為 0
            position_diff = abs(actual_grvt_position + actual_lighter_position)
            if position_diff > Decimal('0.001'):  # 允許 0.001 的誤差
                self.logger.warning(f"⚠️ Position mismatch detected: GRVT={actual_grvt_position}, Lighter={actual_lighter_position}, diff={position_diff}")
            else:
                self.logger.info(f"✅ Positions match: GRVT={actual_grvt_position}, Lighter={actual_lighter_position}, diff={position_diff:.6f}")
            
            # 取消所有未成交的 GRVT 訂單
            await self.cancel_all_grvt_orders()
            
            self.order_execution_complete = False
            self.waiting_for_lighter_fill = False
            try:
                # Determine side based on some logic (for now, alternate)
                side = 'sell'
                await self.place_grvt_post_only_order(side, self.order_quantity)
            except Exception as e:
                self.logger.error(f"⚠️ Error in trading loop: {e}")
                self.logger.error(f"⚠️ Full traceback: {traceback.format_exc()}")
                break

            check_counter = 0
            while not self.order_execution_complete and not self.stop_flag:
                # Check if GRVT order filled and we need to place Lighter order
                if self.waiting_for_lighter_fill:
                    await self.place_lighter_market_order(
                        self.current_lighter_side,
                        self.current_lighter_quantity,
                        self.current_lighter_price
                    )
                    break
                
                # 每 5 次循環（0.5秒）檢查一次持倉作為備用機制
                check_counter += 1
                if check_counter >= 5:
                    check_counter = 0
                    current_grvt_pos = await self.get_grvt_position()
                    
                    # 備用觸發：如果 GRVT 有持倉但還沒觸發對沖
                    if current_grvt_pos != Decimal('0') and not self.waiting_for_lighter_fill:
                        self.logger.warning(f"⚠️ Backup hedge trigger: GRVT position={current_grvt_pos}")
                        lighter_side = 'sell' if current_grvt_pos > 0 else 'buy'
                        hedge_quantity = abs(current_grvt_pos)
                        
                        self.current_lighter_side = lighter_side
                        self.current_lighter_quantity = hedge_quantity
                        self.current_lighter_price = Decimal('0')
                        self.waiting_for_lighter_fill = True
                        continue

                await asyncio.sleep(0.1)
                if time.time() - start_time > 60:
                    self.logger.error("❌ Timeout waiting for trade completion")
                    break

            # Close remaining position
            self.logger.info(f"[STEP 3] GRVT position: {self.grvt_position} | Lighter position: {self.lighter_position}")
            self.order_execution_complete = False
            self.waiting_for_lighter_fill = False
            if self.grvt_position == 0:
                # 一個循環完成，等待 5 秒後進入下一個循環
                self.logger.info("✅ Trading cycle completed, waiting 5 seconds before next cycle...")
                await asyncio.sleep(5)
                continue
            elif self.grvt_position > 0:
                side = 'sell'
            else:
                side = 'buy'

            try:
                # Determine side based on some logic (for now, alternate)
                await self.place_grvt_post_only_order(side, abs(self.grvt_position))
            except Exception as e:
                self.logger.error(f"⚠️ Error in trading loop: {e}")
                self.logger.error(f"⚠️ Full traceback: {traceback.format_exc()}")
                break

            # Wait for order to be filled via WebSocket
            check_counter = 0
            while not self.order_execution_complete and not self.stop_flag:
                # Check if GRVT order filled and we need to place Lighter order
                if self.waiting_for_lighter_fill:
                    await self.place_lighter_market_order(
                        self.current_lighter_side,
                        self.current_lighter_quantity,
                        self.current_lighter_price
                    )
                    break
                
                # 每 5 次循環（0.5秒）檢查一次持倉作為備用機制
                check_counter += 1
                if check_counter >= 5:
                    check_counter = 0
                    current_grvt_pos = await self.get_grvt_position()
                    
                    # 備用觸發：如果 GRVT 有持倉但還沒觸發對沖
                    if current_grvt_pos != Decimal('0') and not self.waiting_for_lighter_fill:
                        self.logger.warning(f"⚠️ Backup hedge trigger: GRVT position={current_grvt_pos}")
                        lighter_side = 'sell' if current_grvt_pos > 0 else 'buy'
                        hedge_quantity = abs(current_grvt_pos)
                        
                        self.current_lighter_side = lighter_side
                        self.current_lighter_quantity = hedge_quantity
                        self.current_lighter_price = Decimal('0')
                        self.waiting_for_lighter_fill = True
                        continue

                await asyncio.sleep(0.1)
                if time.time() - start_time > 60:
                    self.logger.error("❌ Timeout waiting for trade completion")
                    break
            
            # 一個循環完成，等待 5 秒後進入下一個循環
            self.logger.info("✅ Trading cycle completed, waiting 5 seconds before next cycle...")
            await asyncio.sleep(5)

    async def run(self):
        """Run the hedge bot."""
        self.setup_signal_handlers()

        try:
            await self.trading_loop()
        except KeyboardInterrupt:
            self.logger.info("\n🛑 Received interrupt signal...")
        finally:
            self.logger.info("🔄 Cleaning up...")
            await self.async_shutdown()


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description='Trading bot for GRVT and Lighter')
    parser.add_argument('--exchange', type=str,
                        help='Exchange')
    parser.add_argument('--ticker', type=str, default='BTC',
                        help='Ticker symbol (default: BTC)')
    parser.add_argument('--size', type=str,
                        help='Number of tokens to buy/sell per order')
    parser.add_argument('--iter', type=int,
                        help='Number of iterations to run')
    parser.add_argument('--fill-timeout', type=int, default=5,
                        help='Timeout in seconds for maker order fills (default: 5)')

    return parser.parse_args()
