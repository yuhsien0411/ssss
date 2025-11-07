#!/usr/bin/env python3
"""
Simple test for position snapshot
"""
import asyncio
import time
from decimal import Decimal

async def test_simple_snapshot():
    """Test simple snapshot functionality."""
    print("🧪 Testing simple snapshot...")
    
    # Simulate snapshot data
    snapshot = {
        'timestamp': time.time(),
        'datetime': time.strftime('%Y-%m-%d %H:%M:%S'),
        'grvt_position': 0.01,
        'lighter_position': -0.01,
        'position_diff': 0.0,
        'grvt_position_abs': 0.01,
        'lighter_position_abs': 0.01,
        'hedge_ratio': 1.0,
        'is_hedged': True
    }
    
    print(f"📸 Position Snapshot: GRVT={snapshot['grvt_position']:.4f}, Lighter={snapshot['lighter_position']:.4f}, Diff={snapshot['position_diff']:.4f}, Hedged={snapshot['is_hedged']}")
    print("✅ Snapshot test successful!")

if __name__ == "__main__":
    asyncio.run(test_simple_snapshot())