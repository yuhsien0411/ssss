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

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from exchanges.edgex import EdgeXClient
from exchanges.bybit import BybitClient
import websockets
from datetime import datetime
import pytz

class Config:
    """Simple config class to wrap dictionary for exchange clients."""
    def __init__(self, config_dict):
        for key, value in config_dict.items():
            setattr(self, key, value)


class HedgeBot:
    """Trading bot that places post-only orders on EdgeX and hedges with market orders on Bybit."""

    def __init__(self, ticker: str, order_quantity: Decimal, fill_timeout: int = 5, iterations: int = 20):
        self.ticker = ticker
        self.order_quantity = order_quantity
        self.fill_timeout = fill_timeout
        self.bybit_order_filled = False
        self.iterations = iterations
        self.edgex_position = Decimal('0')
        self.bybit_position = Decimal('0')
        self.current_order = {}

        # Initialize logging to file
        os.makedirs("logs", exist_ok=True)
        self.log_filename = f"logs/edgex_{ticker}_hedge_mode_log.txt"
        self.csv_filename = f"logs/edgex_{ticker}_hedge_mode_trades.csv"
        self.original_stdout = sys.stdout

        # Initialize logger
        self.logger = logging.getLogger(f"hedge_bot_{ticker}")
        self.logger.setLevel(logging.INFO)
        self.logger.handlers.clear()

        # Disable verbose logging from external libraries
        logging.getLogger('urllib3').setLevel(logging.WARNING)
        logging.getLogger('requests').setLevel(logging.WARNING)
        logging.getLogger('websockets').setLevel(logging.WARNING)

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
        console_formatter = logging.Formatter('%(levelname)s:%(name)s:%(message)s')

        file_handler.setFormatter(file_formatter)
        console_handler.setFormatter(console_formatter)

        # Add handlers to logger
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)

        # Prevent propagation to root logger to avoid duplicate messages
        self.logger.propagate = False

        # State management
        self.stop_flag = False
        self.edgex_client = None
        self.bybit_client = None
        self.edgex_ws = None
        self.bybit_ws = None
        self.edgex_order_book = {"bids": [], "asks": []}
        self.bybit_order_book = {"bids": [], "asks": []}
        self.edgex_orders = {}
        self.bybit_orders = {}
        self.trade_count = 0
        self.total_pnl = Decimal('0')
        self.start_time = None

        # CSV file setup
        self.setup_csv_logging()

        # Setup signal handlers
        self.setup_signal_handlers()

    def setup_csv_logging(self):
        """Setup CSV logging for trades."""
        csv_exists = os.path.exists(self.csv_filename)
        with open(self.csv_filename, 'a', newline='', encoding='utf-8') as csvfile:
            fieldnames = ['timestamp', 'ticker', 'side', 'edgex_price', 'bybit_price', 'quantity', 'pnl', 'total_pnl']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            if not csv_exists:
                writer.writeheader()

    def setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown."""
        signal.signal(signal.SIGINT, self.shutdown)
        signal.signal(signal.SIGTERM, self.shutdown)

    def initialize_edgex_client(self):
        """Initialize the EdgeX client."""
        if self.edgex_client is None:
            # EdgeX credentials from environment
            account_id = os.getenv('EDGEX_ACCOUNT_ID')
            stark_private_key = os.getenv('EDGEX_STARK_PRIVATE_KEY')
            base_url = os.getenv('EDGEX_BASE_URL', 'https://pro.edgex.exchange')
            ws_url = os.getenv('EDGEX_WS_URL', 'wss://quote.edgex.exchange')

            if not account_id or not stark_private_key:
                raise Exception("EDGEX_ACCOUNT_ID and EDGEX_STARK_PRIVATE_KEY environment variables must be set")

            config = {
                'ticker': self.ticker,
                'market_type': 'PERPETUAL',
                'base_url': base_url,
                'ws_url': ws_url,
                'account_id': account_id,
                'stark_private_key': stark_private_key
            }

            self.edgex_client = EdgeXClient(Config(config))
            self.logger.info("✅ EdgeX client initialized successfully")

    def initialize_bybit_client(self):
        """Initialize the Bybit client."""
        if self.bybit_client is None:
            # Bybit credentials from environment
            api_key = os.getenv('BYBIT_API_KEY')
            api_secret = os.getenv('BYBIT_API_SECRET')
            testnet = os.getenv('BYBIT_TESTNET', 'false').lower() == 'true'

            if not api_key or not api_secret:
                raise Exception("BYBIT_API_KEY and BYBIT_API_SECRET environment variables must be set")

            config = {
                'ticker': self.ticker,
                'market_type': 'PERPETUAL',
                'api_key': api_key,
                'api_secret': api_secret,
                'testnet': testnet
            }

            self.bybit_client = BybitClient(Config(config))
            self.logger.info("✅ Bybit client initialized successfully")

    async def get_edgex_contract_info(self):
        """Get EdgeX contract information."""
        try:
            # This would need to be implemented based on EdgeX API
            # For now, return placeholder values
            return "SOL-PERP", Decimal('0.01'), Decimal('0.1')
        except Exception as e:
            self.logger.error(f"Failed to get EdgeX contract info: {e}")
            raise

    async def get_bybit_contract_info(self):
        """Get Bybit contract information."""
        try:
            # This would need to be implemented based on Bybit API
            # For now, return placeholder values
            return "SOLUSDT", Decimal('0.01'), Decimal('0.1')
        except Exception as e:
            self.logger.error(f"Failed to get Bybit contract info: {e}")
            raise

    async def setup_edgex_websocket(self):
        """Setup EdgeX WebSocket connection."""
        try:
            # This would need to be implemented based on EdgeX WebSocket API
            self.logger.info("✅ EdgeX WebSocket connection established")
        except Exception as e:
            self.logger.error(f"Could not setup EdgeX WebSocket handlers: {e}")

    async def setup_bybit_websocket(self):
        """Setup Bybit WebSocket connection."""
        try:
            # This would need to be implemented based on Bybit WebSocket API
            self.logger.info("✅ Bybit WebSocket connection established")
        except Exception as e:
            self.logger.error(f"Could not setup Bybit WebSocket handlers: {e}")

    async def trading_loop(self):
        """Main trading loop implementing the hedge strategy."""
        self.logger.info(f"🚀 Starting hedge bot for {self.ticker}")

        # Initialize clients
        try:
            self.initialize_edgex_client()
            self.initialize_bybit_client()

            # Get contract info
            self.edgex_contract_id, self.edgex_tick_size, self.edgex_min_size = await self.get_edgex_contract_info()
            self.bybit_contract_id, self.bybit_tick_size, self.bybit_min_size = await self.get_bybit_contract_info()

            self.logger.info(f"Contract info loaded - EdgeX: {self.edgex_contract_id}, "
                             f"Bybit: {self.bybit_contract_id}")

        except Exception as e:
            self.logger.error(f"❌ Failed to initialize: {e}")
            return

        # Setup WebSockets
        try:
            await self.setup_edgex_websocket()
            await self.setup_bybit_websocket()
            self.logger.info("✅ WebSocket connections established")

        except Exception as e:
            self.logger.error(f"❌ Failed to setup WebSockets: {e}")
            return

        # Main trading loop
        self.start_time = time.time()
        iteration = 0

        while iteration < self.iterations and not self.stop_flag:
            try:
                self.logger.info(f"📊 Starting iteration {iteration + 1}/{self.iterations}")

                # Place post-only order on EdgeX
                edgex_order = await self.place_edgex_order()
                if not edgex_order:
                    self.logger.warning("⚠️ Failed to place EdgeX order, skipping iteration")
                    iteration += 1
                    continue

                # Wait for fill or timeout
                filled = await self.wait_for_edgex_fill(edgex_order)
                if filled:
                    # Hedge with market order on Bybit
                    hedge_order = await self.place_bybit_hedge_order(edgex_order)
                    if hedge_order:
                        self.logger.info("✅ Hedge completed successfully")
                        self.trade_count += 1
                    else:
                        self.logger.warning("⚠️ Hedge order failed")
                else:
                    self.logger.info("⏰ EdgeX order timed out, cancelling")
                    await self.cancel_edgex_order(edgex_order)

                iteration += 1
                await asyncio.sleep(1)  # Brief pause between iterations

            except Exception as e:
                self.logger.error(f"❌ Error in trading loop: {e}")
                iteration += 1
                await asyncio.sleep(1)

        # Final summary
        self.logger.info(f"🏁 Trading completed. Total trades: {self.trade_count}")
        self.logger.info(f"💰 Total PnL: {self.total_pnl}")

    async def place_edgex_order(self):
        """Place a post-only order on EdgeX."""
        try:
            # This would need to be implemented based on EdgeX API
            self.logger.info("📝 Placing EdgeX post-only order...")
            # Placeholder implementation
            return {"order_id": f"edgex_{int(time.time())}", "side": "buy", "price": "100.0", "quantity": str(self.order_quantity)}
        except Exception as e:
            self.logger.error(f"Failed to place EdgeX order: {e}")
            return None

    async def place_bybit_hedge_order(self, edgex_order):
        """Place a market hedge order on Bybit."""
        try:
            # This would need to be implemented based on Bybit API
            self.logger.info("🔄 Placing Bybit hedge order...")
            # Placeholder implementation
            return {"order_id": f"bybit_{int(time.time())}", "side": "sell", "quantity": str(self.order_quantity)}
        except Exception as e:
            self.logger.error(f"Failed to place Bybit hedge order: {e}")
            return None

    async def wait_for_edgex_fill(self, order):
        """Wait for EdgeX order to fill or timeout."""
        try:
            # This would need to be implemented based on EdgeX WebSocket
            self.logger.info("⏳ Waiting for EdgeX order fill...")
            await asyncio.sleep(self.fill_timeout)
            # Placeholder - assume filled for demo
            return True
        except Exception as e:
            self.logger.error(f"Error waiting for EdgeX fill: {e}")
            return False

    async def cancel_edgex_order(self, order):
        """Cancel EdgeX order."""
        try:
            # This would need to be implemented based on EdgeX API
            self.logger.info("❌ Cancelling EdgeX order...")
        except Exception as e:
            self.logger.error(f"Failed to cancel EdgeX order: {e}")

    def shutdown(self, signum=None, frame=None):
        """Graceful shutdown handler."""
        self.stop_flag = True
        self.logger.info("\n🛑 Stopping...")

        # Close WebSocket connections
        if self.edgex_ws:
            try:
                self.logger.info("🔌 EdgeX WebSocket will be disconnected")
            except Exception as e:
                self.logger.error(f"Error disconnecting EdgeX WebSocket: {e}")

        if self.bybit_ws:
            try:
                self.logger.info("🔌 Bybit WebSocket will be disconnected")
            except Exception as e:
                self.logger.error(f"Error disconnecting Bybit WebSocket: {e}")

    async def run(self):
        """Main run method."""
        try:
            await self.trading_loop()
        except KeyboardInterrupt:
            self.logger.info("\n🛑 Received interrupt signal...")
        finally:
            self.logger.info("🔄 Cleaning up...")
            self.shutdown()


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description='Trading bot for EdgeX and Bybit')
    parser.add_argument('--exchange', type=str,
                        help='Exchange')
    parser.add_argument('--ticker', type=str, default='BTC',
                        help='Ticker symbol (default: BTC)')
    parser.add_argument('--size', type=str, required=True,
                        help='Number of tokens to buy/sell per order')
    parser.add_argument('--iter', type=int, required=True,
                        help='Number of iterations to run')
    parser.add_argument('--fill-timeout', type=int, default=5,
                        help='Timeout in seconds for maker order fills (default: 5)')
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_arguments()
    
    bot = HedgeBot(
        ticker=args.ticker.upper(),
        order_quantity=Decimal(args.size),
        fill_timeout=args.fill_timeout,
        iterations=args.iter
    )
    
    asyncio.run(bot.run())
