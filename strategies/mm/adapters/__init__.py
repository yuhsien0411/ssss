"""
Adapter factory utilities for the simple market-making strategy.
"""

from __future__ import annotations

from typing import Dict, Type

from exchanges.base import BaseExchangeClient

from .base import SimpleMMAdapter
from .backpack import BackpackAdapter
from .lighter import LighterAdapter
from ..config import SimpleMMConfig

ADAPTER_MAP: Dict[str, Type[SimpleMMAdapter]] = {
    "backpack": BackpackAdapter,
    "lighter": LighterAdapter,
}


def build_adapter(exchange_name: str, client: BaseExchangeClient, config: SimpleMMConfig) -> SimpleMMAdapter:
    """Instantiate an adapter for the given exchange."""
    normalized = exchange_name.lower()
    if normalized not in ADAPTER_MAP:
        raise ValueError(f"simple-mm does not support exchange '{exchange_name}' yet")
    adapter_cls = ADAPTER_MAP[normalized]
    return adapter_cls(client, config)
