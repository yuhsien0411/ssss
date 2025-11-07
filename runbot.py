#!/usr/bin/env python3
"""
Modular Trading Bot - Supports multiple exchanges
"""

import argparse
import asyncio
import logging
from pathlib import Path
import sys
import dotenv
from decimal import Decimal
from trading_bot import TradingBot, TradingConfig
from exchanges import ExchangeFactory
from strategies import SimpleMMConfig, SimpleMarketMaker, build_adapter


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description='Modular Trading Bot - Supports multiple exchanges')

    # Exchange selection
    parser.add_argument('--exchange', type=str, default='edgex',
                        choices=ExchangeFactory.get_supported_exchanges(),
                        help='Exchange to use (default: edgex). '
                             f'Available: {", ".join(ExchangeFactory.get_supported_exchanges())}')

    # Trading parameters
    parser.add_argument('--ticker', type=str, default='ETH',
                        help='Ticker (default: ETH)')
    parser.add_argument('--quantity', type=Decimal, default=Decimal(0.1),
                        help='Order quantity (default: 0.1)')
    parser.add_argument('--take-profit', type=Decimal, default=Decimal(0.02),
                        help='Take profit in USDT (default: 0.02)')
    parser.add_argument('--direction', type=str, default='buy', choices=['buy', 'sell'],
                        help='Direction of the bot (default: buy)')
    parser.add_argument('--max-orders', type=int, default=40,
                        help='Maximum number of active orders (default: 40)')
    parser.add_argument('--wait-time', type=int, default=450,
                        help='Wait time between orders in seconds (default: 450)')
    parser.add_argument('--env-file', type=str, default=".env",
                        help=".env file path (default: .env)")
    parser.add_argument('--grid-step', type=str, default='-100',
                        help='The minimum distance in percentage to the next close order price (default: -100)')
    parser.add_argument('--stop-price', type=Decimal, default=-1,
                        help='Price to stop trading and exit. Buy: exits if price >= stop-price.'
                        'Sell: exits if price <= stop-price. (default: -1, no stop)')
    parser.add_argument('--pause-price', type=Decimal, default=-1,
                        help='Pause trading and wait. Buy: pause if price >= pause-price.'
                        'Sell: pause if price <= pause-price. (default: -1, no pause)')
    parser.add_argument('--boost', action='store_true',
                        help='Use the Boost mode for volume boosting')
    parser.add_argument('--strategy', type=str, default='grid',
                        choices=['grid', 'simple-mm'],
                        help='Strategy to execute (default: grid).')
    parser.add_argument('--spread', type=Decimal, default=Decimal('0.30'),
                        help='Base spread percentage for simple-mm strategy (default: 0.30)')
    parser.add_argument('--spread-ticks', type=int, default=None,
                        help='Spread in ticks for simple-mm strategy (overrides --spread when set)')
    parser.add_argument('--refresh-interval', type=float, default=2.0,
                        help='Refresh interval in seconds for simple-mm strategy (default: 2.0)')
    parser.add_argument('--target-position', type=Decimal, default=Decimal('0'),
                        help='Target position size for simple-mm strategy (default: 0)')
    parser.add_argument('--max-position', type=Decimal, default=Decimal('2'),
                        help='Maximum net position before flattening (default: 2)')
    parser.add_argument('--position-threshold', type=Decimal, default=Decimal('0.1'),
                        help='Threshold before pausing accumulation (default: 0.1)')
    parser.add_argument('--inventory-skew', type=Decimal, default=Decimal('0'),
                        help='Inventory skew factor between 0 and 1 (default: 0)')

    return parser.parse_args()


def setup_logging(log_level: str):
    """Setup global logging configuration."""
    # Convert string level to logging constant
    level = getattr(logging, log_level.upper(), logging.INFO)

    # Clear any existing handlers to prevent duplicates
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Configure root logger WITHOUT adding a console handler
    # This prevents duplicate logs when TradingLogger adds its own console handler
    root_logger.setLevel(level)

    # Suppress websockets debug logs unless DEBUG level is explicitly requested
    if log_level.upper() != 'DEBUG':
        logging.getLogger('websockets').setLevel(logging.WARNING)

    # Suppress other noisy loggers
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)

    # Suppress Lighter SDK debug logs
    logging.getLogger('lighter').setLevel(logging.WARNING)
    # Also suppress any root logger DEBUG messages that might be coming from Lighter
    if log_level.upper() != 'DEBUG':
        # Set root logger to WARNING to suppress DEBUG messages from Lighter SDK
        root_logger.setLevel(logging.WARNING)


async def main():
    """Main entry point."""
    args = parse_arguments()

    # Setup logging first
    setup_logging("WARNING")

    # Validate boost-mode can only be used with aster and backpack exchange
    if args.boost and args.exchange.lower() != 'aster' and args.exchange.lower() != 'backpack':
        print(f"Error: --boost can only be used when --exchange is 'aster' or 'backpack'. "
              f"Current exchange: {args.exchange}")
        sys.exit(1)

    env_path = Path(args.env_file)
    if not env_path.exists():
        print(f"Env file not find: {env_path.resolve()}")
        sys.exit(1)
    dotenv.load_dotenv(args.env_file)

    strategy_name = args.strategy.lower()
    exchange_name = args.exchange.lower()

    if strategy_name == 'simple-mm':
        spread_pct = args.spread
        if args.spread_ticks is not None:
            spread_pct = None

        mm_config = SimpleMMConfig(
            ticker=args.ticker.upper(),
            quantity=args.quantity,
            base_spread_pct=spread_pct,
            spread_ticks=args.spread_ticks,
            refresh_interval=args.refresh_interval,
            target_position=args.target_position,
            max_position=args.max_position,
            position_threshold=args.position_threshold,
            inventory_skew=args.inventory_skew,
            exchange=exchange_name,
        )

        try:
            exchange_client = ExchangeFactory.create_exchange(exchange_name, mm_config)
            adapter = build_adapter(exchange_name, exchange_client, mm_config)
            strategy = SimpleMarketMaker(adapter, mm_config)
            await strategy.run()
        except Exception as e:
            print(f"simple-mm strategy failed: {e}")
        return

    # Default grid strategy
    config = TradingConfig(
        ticker=args.ticker.upper(),
        contract_id='',  # will be set in the bot's run method
        tick_size=Decimal(0),
        quantity=args.quantity,
        take_profit=args.take_profit,
        direction=args.direction.lower(),
        max_orders=args.max_orders,
        wait_time=args.wait_time,
        exchange=exchange_name,
        grid_step=Decimal(args.grid_step),
        stop_price=Decimal(args.stop_price),
        pause_price=Decimal(args.pause_price),
        boost_mode=args.boost
    )

    bot = TradingBot(config)
    try:
        await bot.run()
    except Exception as e:
        print(f"Bot execution failed: {e}")
        return


if __name__ == "__main__":
    asyncio.run(main())
