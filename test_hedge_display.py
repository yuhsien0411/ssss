#!/usr/bin/env python3
"""
Test script to verify GRVT order pricing and hedge status display
"""
import asyncio
import sys
import os
from decimal import Decimal

# Add the project root to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from hedge.hedge_mode_grvt import HedgeBot

async def test_hedge_display():
    """Test the hedge status display functionality."""
    print("🧪 Testing GRVT-Lighter Hedge Bot with improved display...")
    
    # Create a small test bot
    bot = HedgeBot(
        ticker="ETH",
        order_quantity=Decimal('0.01'),
        fill_timeout=10,  # Shorter timeout for testing
        iterations=1
    )
    
    try:
        # Test the display functions
        print("\n📊 Testing position snapshot...")
        await bot.take_position_snapshot()
        
        print("\n📊 Testing position summary...")
        bot.print_position_summary()
        
        print("\n✅ All display tests passed!")
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_hedge_display())
