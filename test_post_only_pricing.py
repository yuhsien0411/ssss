#!/usr/bin/env python3
"""
Test script to verify post-only order pricing strategy
"""
import asyncio
import sys
import os
from decimal import Decimal

# Add the project root to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from hedge.hedge_mode_grvt import HedgeBot

async def test_post_only_pricing():
    """Test the post-only pricing strategy."""
    print("🧪 Testing post-only order pricing strategy...")
    
    # Create a test bot
    bot = HedgeBot(
        ticker="ETH",
        order_quantity=Decimal('0.01'),
        fill_timeout=5,
        iterations=1
    )
    
    try:
        # Initialize GRVT client
        bot.initialize_grvt_client()
        
        # Get contract info
        contract_id, tick_size = await bot.get_grvt_contract_info()
        bot.grvt_contract_id = contract_id
        bot.grvt_tick_size = tick_size
        
        print(f"📊 Contract ID: {contract_id}")
        print(f"📊 Tick Size: {tick_size}")
        
        # Test pricing
        best_bid, best_ask = await bot.fetch_grvt_bbo_prices()
        print(f"📊 Market: Bid={best_bid}, Ask={best_ask}")
        
        # Test buy order pricing (must be below best ask)
        tick_adjustment = tick_size if tick_size else Decimal('0.01')
        buy_price = best_ask - tick_adjustment
        print(f"💰 BUY Order Price: {buy_price} (Best Ask: {best_ask} - {tick_adjustment})")
        
        # Test sell order pricing (must be above best bid)
        sell_price = best_bid + tick_adjustment
        print(f"💰 SELL Order Price: {sell_price} (Best Bid: {best_bid} + {tick_adjustment})")
        
        print("✅ Post-only pricing strategy test completed!")
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_post_only_pricing())
