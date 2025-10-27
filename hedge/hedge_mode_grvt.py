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
import datetime
from decimal import Decimal
from typing import Tuple

from lighter.signer_client import SignerClient
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from exchanges.grvt import GrvtClient
from exchanges.lighter import LighterClient
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

    def __init__(self, ticker: str, order_quantity: Decimal, fill_timeout: int = 30, iterations: int = 20):
        self.ticker = ticker
        self.order_quantity = order_quantity
        self.fill_timeout = fill_timeout
        self.lighter_order_filled = False
        self.iterations = iterations
        self.grvt_position = Decimal('0')
        self.lighter_position = Decimal('0')
        self.current_order = {}
        
        # Initialize logging to file
        os.makedirs("logs", exist_ok=True)
        self.log_filename = f"logs/grvt_{ticker}_hedge_mode_log.txt"
        self.csv_filename = f"logs/grvt_{ticker}_hedge_mode_trades.csv"
        self.original_stdout = sys.stdout

        # Initialize CSV file with headers if it doesn't exist
        self._initialize_csv_file()

        # Setup logger
        self.logger = logging.getLogger(f"hedge_bot_{ticker}")
        # 可以通過環境變數 LOG_LEVEL 控制: export LOG_LEVEL=INFO 或 DEBUG
        log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
        self.logger.setLevel(getattr(logging, log_level, logging.INFO))

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
        logging.getLogger('pysdk.grvt_ccxt_logging_selector').setLevel(logging.WARNING)
        logging.getLogger('pysdk.grvt_ccxt').setLevel(logging.WARNING)
        logging.getLogger('pysdk.grvt_ccxt_ws').setLevel(logging.WARNING)
        logging.getLogger('pysdk.grvt_ccxt_env').setLevel(logging.WARNING)
        # 抑制 GRVT CCXT 相關的所有日誌
        logging.getLogger('grvt').setLevel(logging.WARNING)
        logging.getLogger('ccxt').setLevel(logging.WARNING)
        # 抑制所有包含 'grvt' 或 'ccxt' 的日誌記錄器
        for logger_name in list(logging.Logger.manager.loggerDict.keys()):
            if 'grvt' in logger_name.lower() or 'ccxt' in logger_name.lower() or 'pysdk' in logger_name.lower():
                logging.getLogger(logger_name).setLevel(logging.WARNING)
        logging.getLogger('lighter').setLevel(logging.CRITICAL)
        logging.getLogger('lighter.signer_client').setLevel(logging.CRITICAL)
        
        # Disable root logger propagation to prevent external logs
        logging.getLogger().setLevel(logging.CRITICAL)

        # Create file handler
        file_handler = logging.FileHandler(self.log_filename)
        file_handler.setLevel(logging.INFO)

        # Create console handler with UTF-8 encoding
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        
        # Set UTF-8 encoding for console output
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

        # Prevent propagation to root logger to avoid duplicate messages and external logs
        self.logger.propagate = False
        
        # Ensure our logger only shows our messages
        self.logger.setLevel(logging.INFO)

        # State management
        self.stop_flag = False
        self.order_counter = 0

        # GRVT state
        self.grvt_client = None
        self.grvt_contract_id = None
        self.grvt_tick_size = None
        self.grvt_order_status = None

        # GRVT order book state (not used since we use REST API for BBO)
        # Keeping variables for potential future use but not initializing them

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

        # Order execution tracking
        self.order_execution_complete = False
        
        # 艙位快照相關
        self.position_snapshot_interval = 1  # 1秒快照一次
        self.last_snapshot_time = 0
        self.position_snapshots = []  # 存儲歷史快照

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

        # Cancel Lighter WebSocket task immediately
        if self.lighter_ws_task and not self.lighter_ws_task.done():
            try:
                self.lighter_ws_task.cancel()
                self.logger.info("🔌 Lighter WebSocket task cancelled")
            except Exception as e:
                self.logger.error(f"Error cancelling Lighter WebSocket task: {e}")

        # Cancel GRVT WebSocket if exists
        if hasattr(self, 'grvt_ws_task') and self.grvt_ws_task and not self.grvt_ws_task.done():
            try:
                self.grvt_ws_task.cancel()
                self.logger.info("🔌 GRVT WebSocket task cancelled")
            except Exception as e:
                self.logger.error(f"Error cancelling GRVT WebSocket task: {e}")

        # Close logging handlers properly
        for handler in self.logger.handlers[:]:
            try:
                handler.close()
                self.logger.removeHandler(handler)
            except Exception:
                pass

    async def async_shutdown(self):
        """Async shutdown handler for proper resource cleanup."""
        try:
            self.logger.info("🔄 Starting async cleanup...")
            
            # Cancel all WebSocket tasks with timeout
            tasks_to_cancel = []
            
            if self.lighter_ws_task and not self.lighter_ws_task.done():
                tasks_to_cancel.append(self.lighter_ws_task)
                
            if hasattr(self, 'grvt_ws_task') and self.grvt_ws_task and not self.grvt_ws_task.done():
                tasks_to_cancel.append(self.grvt_ws_task)
            
            if tasks_to_cancel:
                self.logger.info(f"🔌 Cancelling {len(tasks_to_cancel)} WebSocket tasks...")
                
                # Cancel all tasks
                for task in tasks_to_cancel:
                    task.cancel()
                
                # Wait for cancellation with timeout
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*tasks_to_cancel, return_exceptions=True),
                        timeout=2.0  # 2 second timeout
                    )
                    self.logger.info("✅ All WebSocket tasks cancelled successfully")
                except asyncio.TimeoutError:
                    self.logger.warning("⚠️ Timeout waiting for WebSocket tasks to cancel")
                except Exception as e:
                    self.logger.warning(f"⚠️ Error during task cancellation: {e}")
            else:
                self.logger.info("✅ No WebSocket tasks to cancel")

        except Exception as e:
            self.logger.error(f"❌ Error during async shutdown: {e}")
        finally:
            self.logger.info("✅ Async cleanup completed")

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
            
            filled_amount = Decimal(order_data["filled_base_amount"])
            old_position = self.lighter_position
            
            if order_data["is_ask"]:
                order_data["side"] = "SHORT"
                order_type = "OPEN"
                self.lighter_position -= filled_amount
                self.logger.info(f"📊 Lighter position updated (SHORT): -{filled_amount} → {self.lighter_position} (was {old_position})")
            else:
                order_data["side"] = "LONG"
                order_type = "CLOSE"
                self.lighter_position += filled_amount
                self.logger.info(f"📊 Lighter position updated (LONG): +{filled_amount} → {self.lighter_position} (was {old_position})")

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
            self.logger.error(f"❌ Error handling Lighter order result: {e}")
            import traceback
            self.logger.error(f"❌ Full traceback: {traceback.format_exc()}")

    async def sync_positions(self):
        """強制同步持倉 - 從 API 查詢實際持倉並更新內部記錄"""
        try:
            self.logger.info("🔄 Syncing positions from APIs...")
            
            # 查詢 GRVT 實際持倉
            try:
                grvt_pos = await self.get_grvt_actual_position()
                old_grvt_pos = self.grvt_position
                self.grvt_position = grvt_pos
                self.logger.info(f"📊 GRVT position synced: {old_grvt_pos} → {grvt_pos}")
            except Exception as e:
                self.logger.error(f"❌ Failed to sync GRVT position: {e}")
            
            # 查詢 Lighter 實際持倉
            try:
                lighter_pos = await self.get_lighter_actual_position()
                old_lighter_pos = self.lighter_position
                self.lighter_position = lighter_pos
                self.logger.info(f"📊 Lighter position synced: {old_lighter_pos} → {lighter_pos}")
            except Exception as e:
                self.logger.error(f"❌ Failed to sync Lighter position: {e}")
            
            # 檢查同步後的持倉
            position_diff = abs(self.grvt_position + self.lighter_position)
            if position_diff > Decimal('0.01'):
                self.logger.warning(f"⚠️ After sync, position diff still exists: {position_diff:.6f}")
                self.logger.warning(f"⚠️ GRVT={self.grvt_position}, Lighter={self.lighter_position}")
            else:
                self.logger.info(f"✅ Positions synced successfully: diff={position_diff:.6f}")
                
        except Exception as e:
            self.logger.error(f"❌ Error syncing positions: {e}")
            import traceback
            self.logger.error(f"❌ Full traceback: {traceback.format_exc()}")

    async def get_lighter_actual_position(self) -> Decimal:
        """Get actual Lighter position."""
        try:
            # 暫時使用內部狀態，避免 API 驗證錯誤
            # TODO: 修復 Lighter API 驗證問題後重新啟用
            self.logger.debug(f"📊 Using internal Lighter position: {self.lighter_position}")
            return self.lighter_position
            
            # 原始 API 查詢（暫時禁用）
            # if not self.lighter_client:
            #     return Decimal(0)
            # 
            # position = await self.lighter_client.get_account_positions()
            # self.lighter_position = position
            # return position
        except Exception as e:
            self.logger.error(f"❌ Error getting Lighter position: {e}")
            return Decimal(0)

    async def get_grvt_actual_position(self) -> Decimal:
        """查詢 GRVT 實際持倉"""
        try:
            # 使用 GRVT API 查詢持倉
            positions = await self.grvt_client.get_positions()
            if positions:
                for position in positions:
                    if position.get('instrument') == self.grvt_contract_id:
                        return Decimal(str(position.get('size', 0)))
            return Decimal('0')
        except Exception as e:
            self.logger.error(f"❌ Error getting GRVT position: {e}")
            return self.grvt_position  # 返回當前記錄的持倉

    async def get_lighter_actual_position(self) -> Decimal:
        """查詢 Lighter 實際持倉"""
        try:
            # 使用 Lighter API 查詢持倉
            from lighter.api.account_api import AccountApi
            account_api = AccountApi(self.lighter_client.api_client)
            
            # 查詢賬戶資訊
            account_response = await account_api.account()
            if account_response and hasattr(account_response, 'account'):
                account = account_response.account
                if hasattr(account, 'positions') and account.positions:
                    for position in account.positions:
                        if hasattr(position, 'market_index') and position.market_index == self.lighter_market_index:
                            # Lighter 持倉以 base amount 為單位
                            base_amount = Decimal(str(position.base_amount)) / self.base_amount_multiplier
                            return base_amount
            return Decimal('0')
        except Exception as e:
            self.logger.error(f"❌ Error getting Lighter position: {e}")
            return self.lighter_position  # 返回當前記錄的持倉

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

                    # Skip auth token for now - we'll use REST API for order monitoring
                    self.logger.info("ℹ️ Skipping account orders subscription (using REST API for order monitoring)")

                    while not self.stop_flag:
                        try:
                            # 更頻繁檢查停止信號
                            msg = await asyncio.wait_for(ws.recv(), timeout=0.5)  # 減少超時時間

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
                            if timeout_count % 6 == 0:  # 每3秒警告一次（0.5s * 6）
                                self.logger.warning(f"⏰ No message from Lighter websocket for {timeout_count * 0.5:.1f} seconds")
                            # 檢查停止信號
                            if self.stop_flag:
                                break
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

    async def initialize_lighter_client(self):
        """Initialize the Lighter client."""
        if self.lighter_client is None:
            # Ensure market config is loaded
            if not hasattr(self, 'lighter_market_index') or self.lighter_market_index is None:
                self.lighter_market_index, self.base_amount_multiplier, self.price_multiplier = self.get_lighter_market_config()
            
            # Create config for Lighter client
            config_dict = {
                'ticker': self.ticker,
                'contract_id': str(self.lighter_market_index),
                'quantity': self.order_quantity,
                'tick_size': Decimal('0.01'),  # Will be updated when we get contract info
                'close_order_side': 'sell'  # Default, will be updated based on strategy
            }
            
            # Wrap in Config class for Lighter client
            config = Config(config_dict)
            
            # Initialize Lighter client
            self.lighter_client = LighterClient(config)
            
            # Set the multipliers in the client
            self.lighter_client.base_amount_multiplier = self.base_amount_multiplier
            self.lighter_client.price_multiplier = self.price_multiplier
            
            # Initialize the client
            await self.lighter_client._initialize_lighter_client()
            
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
            'close_order_side': 'sell'  # Default, will be updated based on strategy
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
        # Place the order using GRVT client
        try:
            # Get best bid/ask prices
            best_bid, best_ask = await self.fetch_grvt_bbo_prices()
            
            self.logger.info(f"📊 GRVT Market: Best Bid={best_bid}, Best Ask={best_ask}")
            
            # Calculate order price for post-only orders (must be inside spread)
            if side.lower() == 'buy':
                # For buy orders, price must be BELOW best ask (inside spread)
                # Use tick size to ensure we're just inside the spread
                tick_adjustment = self.grvt_tick_size if self.grvt_tick_size else Decimal('0.01')
                order_price = best_ask - tick_adjustment
                self.logger.info(f"💰 BUY Order Price: {order_price} (Best Ask: {best_ask} - {tick_adjustment})")
            else:
                # For sell orders, price must be ABOVE best bid (inside spread)
                # Use tick size to ensure we're just inside the spread
                tick_adjustment = self.grvt_tick_size if self.grvt_tick_size else Decimal('0.01')
                order_price = best_bid + tick_adjustment
                self.logger.info(f"💰 SELL Order Price: {order_price} (Best Bid: {best_bid} + {tick_adjustment})")
            
            # Round to tick size
            order_price = self.round_to_tick(order_price)
            
            # Place post-only order with market-matching price
            self.logger.info(f"📝 Placing {side.upper()} post-only order: {quantity} @ {order_price}")
            order_result = await self.grvt_client.place_post_only_order(
                contract_id=self.grvt_contract_id,
                quantity=quantity,
                price=order_price,
                side=side.lower()
            )
            
            if order_result.success:
                self.logger.info(f"✅ Order placed successfully: {order_result.order_id}")
                return order_result.order_id, order_price
            else:
                self.logger.error(f"❌ Order rejected: {order_result.error_message}")
                raise Exception(f"Failed to place order: {order_result.error_message}")
        except Exception as e:
            self.logger.error(f"Error placing GRVT order: {e}")
            raise

    async def place_grvt_post_only_order(self, side: str, quantity: Decimal):
        """Place a post-only order on GRVT using position change detection."""
        if not self.grvt_client:
            raise Exception("GRVT client not initialized")

        self.logger.info(f"[OPEN] [GRVT] [{side}] Placing GRVT POST-ONLY order")
        
        # 重試機制：最多重試 3 次
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries and not self.stop_flag:
            try:
                # 記錄下單前持倉
                position_before = await self.get_grvt_actual_position()
                self.logger.info(f"📊 Position before order: {position_before}")
                
                # 下單
                order_id, order_price = await self.place_bbo_order(side, quantity)
                self.logger.info(f"📝 GRVT order placed: {order_id} @ {order_price}")
                
                # 等待一段時間讓訂單有機會成交
                wait_duration = self.fill_timeout
                self.logger.info(f"⏳ Waiting {wait_duration}s for order to fill...")
                self.logger.info(f"🎯 Order Details: {side.upper()} {quantity} @ {order_price}")
                
                # 分段等待並顯示進度
                for i in range(wait_duration):
                    await asyncio.sleep(1)
                    if i % 5 == 0:  # 每5秒顯示一次進度
                        self.logger.info(f"⏰ Waiting... {i+1}/{wait_duration}s elapsed")
                
                # 撤銷所有掛單（無論是否成交）
                self.logger.info(f"🗑️ Canceling all open orders...")
                try:
                    cancel_result = await self.grvt_client.cancel_all_orders()
                    if cancel_result.get("success", False):
                        self.logger.info(f"✅ All orders canceled successfully")
                    else:
                        self.logger.warning(f"⚠️ Cancel all orders returned: {cancel_result.get('error_message', 'Unknown error')}")
                except Exception as e:
                    self.logger.warning(f"⚠️ Error canceling orders: {e}")
                
                # 查詢下單後持倉
                await asyncio.sleep(1)  # 等待 1 秒讓持倉更新
                position_after = await self.get_grvt_actual_position()
                self.logger.info(f"📊 Position after order: {position_after}")
                
                # 計算持倉變化
                position_change = position_after - position_before
                self.logger.info(f"📊 Position change: {position_change}")
                self.logger.info(f"📊 Position change abs: {abs(position_change)}")
                self.logger.info(f"📊 Threshold: {Decimal('0.001')}")
                
                # 如果持倉有變化，表示訂單已成交
                if abs(position_change) >= Decimal('0.001'):  # 允許小誤差
                    filled_size = abs(position_change)
                    self.logger.info(f"✅ Order filled: {filled_size} (detected by position change)")
                    
                    # 更新內部持倉
                    self.grvt_position = position_after
                    self.logger.info(f"📊 GRVT position updated: {self.grvt_position}")
                    
                    # 立即執行 Lighter 對沖訂單
                    lighter_side = 'sell' if side.lower() == 'buy' else 'buy'
                    self.logger.info(f"🎯 GRVT order filled! Placing Lighter {lighter_side} order...")
                    self.logger.info(f"🔍 GRVT side: {side}, Lighter side: {lighter_side}, Quantity: {filled_size}")
                    self.logger.info(f"🔍 Current GRVT position: {self.grvt_position}, Lighter position: {self.lighter_position}")
                    
                    # 判斷是開倉還是平倉
                    if side.lower() == 'buy':
                        self.logger.info(f"📈 GRVT BUY order filled - Opening Long position, Lighter should SELL to hedge")
                    else:
                        self.logger.info(f"📉 GRVT SELL order filled - Closing Long position, Lighter should BUY to hedge")
                    
                    # 下 Lighter 市價訂單對沖
                    try:
                        # Ensure Lighter client is initialized
                        await self.initialize_lighter_client()
                        
                        # Use the proper Lighter client market order method
                        lighter_result = await self.lighter_client.place_market_order(
                            contract_id=str(self.lighter_market_index),
                            quantity=filled_size,
                            side=lighter_side
                        )
                        
                        if lighter_result.success:
                            self.logger.info(f"✅ Lighter hedge order placed successfully: {lighter_result.order_id}")
                            
                            # 更新內部 Lighter 持倉狀態
                            if lighter_side.lower() == 'sell':
                                self.lighter_position -= filled_size  # 賣單減少持倉
                            else:
                                self.lighter_position += filled_size  # 買單增加持倉
                            
                            self.logger.info(f"📊 Lighter position after hedge: {self.lighter_position}")
                        else:
                            self.logger.error(f"❌ Failed to place Lighter hedge order: {lighter_result.error_message}")
                    except Exception as e:
                        self.logger.error(f"❌ Error placing Lighter hedge order: {e}")
                        import traceback
                        self.logger.error(f"❌ Full traceback: {traceback.format_exc()}")
                    
                    # 設置執行完成標誌
                    self.order_execution_complete = True
                    return  # 成功成交，退出函數
                else:
                    self.logger.warning(f"⚠️ No position change detected, order likely not filled")
                    self.logger.warning(f"⚠️ Position change {position_change} is less than threshold {Decimal('0.001')}")
                    retry_count += 1
                    if retry_count < max_retries:
                        self.logger.warning(f"⚠️ Retrying ({retry_count}/{max_retries})...")
                        await asyncio.sleep(2)
                    else:
                        self.logger.error(f"❌ Failed to fill order after {max_retries} attempts")
                        return
                    
            except Exception as e:
                retry_count += 1
                self.logger.error(f"❌ Error placing order (attempt {retry_count}): {e}")
                import traceback
                self.logger.error(f"❌ Full traceback: {traceback.format_exc()}")
                if retry_count < max_retries:
                    await asyncio.sleep(2)
                else:
                    return


    def handle_grvt_order_update(self, order_data):
        """Handle GRVT order updates from WebSocket."""
        side = order_data.get('side', '').lower()
        filled_size = Decimal(order_data.get('filled_size', '0'))
        price = Decimal(order_data.get('price', '0'))

        if side == 'buy':
            lighter_side = 'sell'
        else:
            lighter_side = 'buy'

        # Store order details for immediate execution
        self.current_lighter_side = lighter_side
        self.current_lighter_quantity = filled_size
        self.current_lighter_price = price
        
        self.lighter_order_info = {
            'lighter_side': lighter_side,
            'quantity': filled_size,
            'price': price
        }

        self.waiting_for_lighter_fill = True
        
        # 立即設置執行完成標誌，避免等待持倉變化檢測
        self.order_execution_complete = True
        
        self.logger.info(f"🎯 GRVT WebSocket order filled! Triggering immediate Lighter {lighter_side} hedge...")
        
        # 創建異步任務立即執行對沖訂單
        import asyncio
        asyncio.create_task(self.execute_immediate_hedge(lighter_side, filled_size))

    async def take_position_snapshot(self):
        """拍攝艙位快照"""
        try:
            current_time = time.time()
            
            # 獲取當前持倉
            grvt_position = await self.get_grvt_actual_position()
            lighter_position = await self.get_lighter_actual_position()
            
            # 計算持倉差異
            position_diff = grvt_position + lighter_position
            
            # 創建快照
            snapshot = {
                'timestamp': current_time,
                'datetime': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'grvt_position': float(grvt_position),
                'lighter_position': float(lighter_position),
                'position_diff': float(position_diff),
                'grvt_position_abs': abs(float(grvt_position)),
                'lighter_position_abs': abs(float(lighter_position)),
                'hedge_ratio': abs(float(lighter_position) / float(grvt_position)) if grvt_position != 0 else 0,
                'is_hedged': abs(position_diff) < 0.001  # 允許小誤差
            }
            
            # 添加到歷史快照
            self.position_snapshots.append(snapshot)
            
            # 保持最近100個快照
            if len(self.position_snapshots) > 100:
                self.position_snapshots = self.position_snapshots[-100:]
            
            # 記錄快照
            self.logger.info(f"📸 Position Snapshot: GRVT={grvt_position:.4f}, Lighter={lighter_position:.4f}, Diff={position_diff:.4f}, Hedged={snapshot['is_hedged']}")
            
            return snapshot
            
        except Exception as e:
            self.logger.error(f"❌ Error taking position snapshot: {e}")
            import traceback
            self.logger.error(f"❌ Full traceback: {traceback.format_exc()}")
            return None

    async def check_and_take_snapshot(self):
        """檢查是否需要拍攝快照"""
        current_time = time.time()
        if current_time - self.last_snapshot_time >= self.position_snapshot_interval:
            self.last_snapshot_time = current_time
            await self.take_position_snapshot()

    def print_position_summary(self):
        """打印持倉摘要"""
        if not self.position_snapshots:
            self.logger.info("📊 No position snapshots available")
            return
        
        latest = self.position_snapshots[-1]
        self.logger.info("=" * 60)
        self.logger.info("📊 POSITION SUMMARY")
        self.logger.info("=" * 60)
        self.logger.info(f"🕐 Time: {latest['datetime']}")
        self.logger.info(f"📈 GRVT Position: {latest['grvt_position']:.4f}")
        self.logger.info(f"📉 Lighter Position: {latest['lighter_position']:.4f}")
        self.logger.info(f"⚖️ Position Diff: {latest['position_diff']:.4f}")
        self.logger.info(f"🎯 Hedge Ratio: {latest['hedge_ratio']:.4f}")
        self.logger.info(f"✅ Fully Hedged: {latest['is_hedged']}")
        self.logger.info("=" * 60)
        
        # 顯示最近5個快照的趨勢
        if len(self.position_snapshots) >= 5:
            self.logger.info("📈 Recent Trend (Last 5 snapshots):")
            for snapshot in self.position_snapshots[-5:]:
                self.logger.info(f"  {snapshot['datetime']}: GRVT={snapshot['grvt_position']:.4f}, Lighter={snapshot['lighter_position']:.4f}, Diff={snapshot['position_diff']:.4f}")

    def save_snapshots_to_csv(self):
        """保存快照到CSV文件"""
        if not self.position_snapshots:
            return
        
        csv_filename = f"logs/position_snapshots_{self.config.ticker}.csv"
        try:
            import csv
            with open(csv_filename, 'w', newline='', encoding='utf-8') as csvfile:
                fieldnames = ['timestamp', 'datetime', 'grvt_position', 'lighter_position', 'position_diff', 
                             'grvt_position_abs', 'lighter_position_abs', 'hedge_ratio', 'is_hedged']
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(self.position_snapshots)
            
            self.logger.info(f"💾 Position snapshots saved to {csv_filename}")
        except Exception as e:
            self.logger.error(f"❌ Error saving snapshots to CSV: {e}")

    async def execute_immediate_hedge(self, lighter_side: str, quantity: Decimal):
        """立即執行對沖訂單，不等待主循環"""
        try:
            # Lighter 客戶端應該已經預先初始化，直接使用
            if not self.lighter_client:
                self.logger.warning("⚠️ Lighter client not initialized, initializing now...")
                await self.initialize_lighter_client()
            
            # 使用正確的 Lighter 客戶端市價訂單方法
            lighter_result = await self.lighter_client.place_market_order(
                contract_id=str(self.lighter_market_index),
                quantity=quantity,
                side=lighter_side
            )
            
            if lighter_result.success:
                self.logger.info(f"✅ Lighter hedge order placed successfully: {lighter_result.order_id}")
            else:
                self.logger.error(f"❌ Failed to place Lighter hedge order: {lighter_result.error_message}")
        except Exception as e:
            self.logger.error(f"❌ Error placing Lighter hedge order: {e}")

    async def place_lighter_market_order(self, lighter_side: str, quantity: Decimal, price: Decimal):
        if not self.lighter_client:
            await self.initialize_lighter_client()

        best_bid, best_ask = self.get_lighter_best_levels()

        # Determine order parameters for market order (aggressive pricing)
        if lighter_side.lower() == 'buy':
            order_type = "CLOSE"
            is_ask = False
            # For buy market order, use price significantly above best ask to ensure immediate fill
            price = best_ask[0] * Decimal('1.01')  # 1% above best ask
        else:
            order_type = "OPEN"
            is_ask = True
            # For sell market order, use price significantly below best bid to ensure immediate fill
            price = best_bid[0] * Decimal('0.99')  # 1% below best bid


        # Reset order state
        self.lighter_order_filled = False
        self.lighter_order_price = price
        self.lighter_order_side = lighter_side
        self.lighter_order_size = quantity

        try:
            client_order_index = int(time.time() * 1000)
            # Sign the order transaction
            tx_info, error = self.lighter_client.sign_create_order(
                market_index=self.lighter_market_index,
                client_order_index=client_order_index,
                base_amount=int(quantity * self.base_amount_multiplier),
                price=int(price * self.price_multiplier),
                is_ask=is_ask,
                order_type=self.lighter_client.ORDER_TYPE_LIMIT,
                time_in_force=self.lighter_client.ORDER_TIME_IN_FORCE_GOOD_TILL_TIME,
                reduce_only=False,
                trigger_price=0,
            )
            if error is not None:
                raise Exception(f"Sign error: {error}")

            # Prepare the form data
            tx_hash = await self.lighter_client.send_tx(
                tx_type=self.lighter_client.TX_TYPE_CREATE_ORDER,
                tx_info=tx_info
            )

            self.logger.info(f"[{client_order_index}] [{order_type}] [Lighter] [OPEN]: {quantity}")

            await self.monitor_lighter_order(client_order_index)

            return tx_hash
        except Exception as e:
            self.logger.error(f"❌ Error placing Lighter order: {e}")
            return None

    async def monitor_lighter_order(self, client_order_index: int):
        """Monitor Lighter order with REST API fallback."""
        start_time = time.time()
        last_query_time = start_time
        query_interval = 1.0  # 每 1 秒主動查詢一次
        max_wait_time = 10  # 最多等待 10 秒
        
        while not self.lighter_order_filled and not self.stop_flag:
            elapsed_time = time.time() - start_time
            current_time = time.time()
            
            # 每 1 秒主動查詢訂單狀態（不依賴 WebSocket）
            if current_time - last_query_time >= query_interval:
                try:
                    from lighter.api.order_api import OrderApi
                    order_api = OrderApi(self.lighter_client.api_client)
                    
                    # 查詢特定訂單
                    order_response = await order_api.order(
                        by="client_order_id",
                        value=str(client_order_index)
                    )
                    
                    if order_response and hasattr(order_response, 'order'):
                        order = order_response.order
                        if order.status == "filled":
                            self.logger.info(f"✅ Lighter order confirmed filled via REST API")
                            # 手動觸發訂單處理
                            self.handle_lighter_order_result({
                                'client_order_id': client_order_index,
                                'status': 'filled',
                                'filled_base_amount': str(self.lighter_order_size),
                                'filled_quote_amount': str(self.lighter_order_size * self.lighter_order_price),
                                'is_ask': self.lighter_order_side.lower() == 'sell'
                            })
                            break
                    
                    last_query_time = current_time
                    self.logger.debug(f"🔍 Queried Lighter order {client_order_index} status via REST API")
                    
                except Exception as e:
                    self.logger.debug(f"⚠️ Error querying Lighter order status: {e}")
                    last_query_time = current_time
            
            # Check for timeout
            if elapsed_time > max_wait_time:
                self.logger.error(f"❌ Timeout waiting for Lighter order fill after {elapsed_time:.1f}s")
                self.logger.warning("⚠️ Assuming order filled (will be verified by position monitor)")
                
                # 假設已成交並更新持倉
                if self.lighter_order_side:
                    if self.lighter_order_side.lower() == 'buy':
                        self.lighter_position += self.lighter_order_size
                        self.logger.info(f"📊 Lighter position updated (assumed fill): +{self.lighter_order_size} → {self.lighter_position}")
                    else:
                        self.lighter_position -= self.lighter_order_size
                        self.logger.info(f"📊 Lighter position updated (assumed fill): -{self.lighter_order_size} → {self.lighter_position}")
                
                self.lighter_order_filled = True
                self.waiting_for_lighter_fill = False
                self.order_execution_complete = True
                break

            await asyncio.sleep(0.1)  # Check every 100ms

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
        """Setup GRVT websocket for order updates and order book data."""
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

                # Handle the order update
                if status == 'FILLED' and self.grvt_order_status != 'FILLED':
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
            await self.initialize_lighter_client()
            self.initialize_grvt_client()

            # Get contract info
            self.grvt_contract_id, self.grvt_tick_size = await self.get_grvt_contract_info()
            self.lighter_market_index, self.base_amount_multiplier, self.price_multiplier = self.get_lighter_market_config()

            self.logger.info(f"Contract info loaded - GRVT: {self.grvt_contract_id}, "
                             f"Lighter: {self.lighter_market_index}")

        except Exception as e:
            self.logger.error(f"❌ Failed to initialize: {e}")
            return

        # Setup GRVT WebSocket for order updates
        try:
            await self.setup_grvt_websocket()
            self.logger.info("✅ GRVT WebSocket connection established")
        except Exception as e:
            self.logger.error(f"❌ Failed to setup GRVT websocket: {e}")
            self.logger.info("⚠️ Falling back to REST API only for GRVT order status")

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

        await asyncio.sleep(5)

        # 預先初始化 Lighter 客戶端以減少對沖延遲
        try:
            await self.initialize_lighter_client()
            self.logger.info("✅ Lighter client pre-initialized for faster hedging")
        except Exception as e:
            self.logger.warning(f"⚠️ Failed to pre-initialize Lighter client: {e}")
        
        # 拍攝初始快照
        await self.take_position_snapshot()
        self.logger.info("📸 Initial position snapshot taken")
        
        iterations = 0
        while iterations < self.iterations and not self.stop_flag:
            iterations += 1
            self.logger.info("-----------------------------------------------")
            self.logger.info(f"🔄 Trading loop iteration {iterations}")
            self.logger.info("-----------------------------------------------")

            # 拍攝快照
            await self.check_and_take_snapshot()
            
            # 顯示詳細的對沖狀態
            position_diff = abs(self.grvt_position + self.lighter_position)
            hedge_status = "✅ FULLY HEDGED" if position_diff < 0.001 else f"⚠️ NOT HEDGED (diff: {position_diff:.4f})"
            
            self.logger.info("=" * 80)
            self.logger.info(f"📊 CURRENT HEDGE STATUS: {hedge_status}")
            self.logger.info(f"📈 GRVT Position: {self.grvt_position:.4f}")
            self.logger.info(f"📉 Lighter Position: {self.lighter_position:.4f}")
            self.logger.info(f"⚖️ Position Difference: {position_diff:.4f}")
            self.logger.info("=" * 80)

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

            start_time = time.time()
            self.logger.info(f"⏳ Waiting for GRVT order to fill and trigger Lighter hedge...")
            while not self.order_execution_complete and not self.stop_flag:
                # Check if GRVT order filled and we need to place Lighter order
                if self.waiting_for_lighter_fill:
                    self.logger.info(f"🎯 GRVT order filled! Placing Lighter {self.current_lighter_side} order...")
                    await self.place_lighter_market_order(
                        self.current_lighter_side,
                        self.current_lighter_quantity,
                        self.current_lighter_price
                    )
                    break
                
                # 檢查是否需要拍攝快照
                await self.check_and_take_snapshot()
                
                await asyncio.sleep(0.01)
                if time.time() - start_time > 180:
                    self.logger.error("❌ Timeout waiting for trade completion")
                    break

            if self.stop_flag:
                break

            # Close position
            self.logger.info(f"[STEP 2] GRVT position: {self.grvt_position} | Lighter position: {self.lighter_position}")
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

            self.logger.info(f"⏳ Waiting for GRVT order to fill and trigger Lighter hedge...")
            while not self.order_execution_complete and not self.stop_flag:
                # Check if GRVT order filled and we need to place Lighter order
                if self.waiting_for_lighter_fill:
                    self.logger.info(f"🎯 GRVT order filled! Placing Lighter {self.current_lighter_side} order...")
                    await self.place_lighter_market_order(
                        self.current_lighter_side,
                        self.current_lighter_quantity,
                        self.current_lighter_price
                    )
                    break
                
                await asyncio.sleep(0.01)
                if time.time() - start_time > 180:
                    self.logger.error("❌ Timeout waiting for trade completion")
                    break

            # Close remaining position
            self.logger.info(f"[STEP 3] GRVT position: {self.grvt_position} | Lighter position: {self.lighter_position}")
            
            # 檢查持倉是否完全對沖
            position_diff = abs(self.grvt_position + self.lighter_position)
            if position_diff > Decimal('0.01'):
                self.logger.warning(f"⚠️ Position mismatch detected: diff={position_diff:.6f}")
                self.logger.warning(f"⚠️ GRVT={self.grvt_position}, Lighter={self.lighter_position}")
                # 強制同步持倉
                await self.sync_positions()
            else:
                self.logger.info(f"✅ Positions balanced: diff={position_diff:.6f}")
            
            self.order_execution_complete = False
            self.waiting_for_lighter_fill = False
            if self.grvt_position == 0:
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
            while not self.order_execution_complete and not self.stop_flag:
                # Check if GRVT order filled and we need to place Lighter order
                if self.waiting_for_lighter_fill:
                    await self.place_lighter_market_order(
                        self.current_lighter_side,
                        self.current_lighter_quantity,
                        self.current_lighter_price
                    )
                    break
                
                await asyncio.sleep(0.01)
                if time.time() - start_time > 180:
                    self.logger.error("❌ Timeout waiting for trade completion")
                    break

    async def run(self):
        """Run the hedge bot."""
        self.setup_signal_handlers()

        try:
            await self.trading_loop()
        except KeyboardInterrupt:
            self.logger.info("\n🛑 Received interrupt signal...")
        except Exception as e:
            self.logger.error(f"❌ Unexpected error: {e}")
            import traceback
            self.logger.error(f"❌ Full traceback: {traceback.format_exc()}")
        finally:
            self.logger.info("🔄 Cleaning up...")
            
            # 快速清理，避免卡住
            try:
                # 拍攝最終快照（快速）
                await asyncio.wait_for(self.take_position_snapshot(), timeout=1.0)
                self.logger.info("📸 Final position snapshot taken")
            except asyncio.TimeoutError:
                self.logger.warning("⚠️ Timeout taking final snapshot")
            except Exception as e:
                self.logger.error(f"❌ Error taking final snapshot: {e}")
            
            # 打印持倉摘要（快速）
            try:
                self.print_position_summary()
            except Exception as e:
                self.logger.error(f"❌ Error printing position summary: {e}")
            
            # 保存快照到CSV（快速）
            try:
                self.save_snapshots_to_csv()
            except Exception as e:
                self.logger.error(f"❌ Error saving snapshots: {e}")
            
            # 快速關閉
            self.logger.info("🚀 Quick shutdown...")
            self.shutdown()
            
            # 異步清理（帶超時）
            try:
                await asyncio.wait_for(self.async_shutdown(), timeout=3.0)
            except asyncio.TimeoutError:
                self.logger.warning("⚠️ Timeout during async shutdown")
            except Exception as e:
                self.logger.error(f"❌ Error during async shutdown: {e}")
            
            self.logger.info("✅ Shutdown completed")


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
    parser.add_argument('--fill-timeout', type=int, default=30,
                        help='Timeout in seconds for maker order fills (default: 30)')

    return parser.parse_args()
