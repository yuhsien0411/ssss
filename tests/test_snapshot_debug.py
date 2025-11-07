#!/usr/bin/env python3
"""
Test script to verify position snapshot functionality
"""
import asyncio
import sys
import os
from decimal import Decimal

# Add the project root to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from hedge.hedge_mode_grvt import HedgeBot

async def test_snapshot():
    """Test the snapshot functionality."""
    print("🧪 Testing position snapshot functionality...")
    
    # Create a test bot
    bot = HedgeBot(
        ticker="ETH",
        order_quantity=Decimal('0.01'),
        fill_timeout=5,
        iterations=1
    )
    
    try:
        print("📸 Testing snapshot...")
        
        # Test snapshot directly
        snapshot = await bot.take_position_snapshot()
        
        if snapshot:
            print(f"✅ Snapshot created: {snapshot}")
        else:
            print("❌ Snapshot failed")
        
        print("✅ Snapshot test completed!")
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_snapshot())
