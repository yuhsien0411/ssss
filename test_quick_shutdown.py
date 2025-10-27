#!/usr/bin/env python3
"""
Test script to verify quick shutdown functionality
"""
import asyncio
import signal
import sys
import os
from decimal import Decimal

# Add the project root to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from hedge.hedge_mode_grvt import HedgeBot

async def test_quick_shutdown():
    """Test the quick shutdown functionality."""
    print("🧪 Testing quick shutdown functionality...")
    
    # Create a test bot
    bot = HedgeBot(
        ticker="ETH",
        order_quantity=Decimal('0.01'),
        fill_timeout=5,
        iterations=1
    )
    
    try:
        print("🚀 Starting bot...")
        
        # Simulate a quick run
        await asyncio.sleep(1)
        
        print("🛑 Triggering shutdown...")
        bot.stop_flag = True
        
        # Test shutdown
        await bot.async_shutdown()
        
        print("✅ Quick shutdown test completed successfully!")
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_quick_shutdown())
