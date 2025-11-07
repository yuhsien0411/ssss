# Simple Market-Maker

This document explains how the Backpack-MM-Simple strategy was integrated into
`perp-dex-tools`, generalised via adapters, and how to run it.

## Overview

- Module: `strategies/mm/simple_mm.py`
- Strategy: `SimpleMarketMaker`
- Config dataclass: `SimpleMMConfig`
- Adapter factory: `strategies/mm/adapters/__init__.py`
- Entry point: `runbot.py --strategy simple-mm`

The strategy keeps one maker quote on each side of the book,
adjusts pricing with an inventory-skew factor, and enforces position limits. It
was adapted from the `PerpetualMarketMaker` logic in the Backpack-MM-Simple
project but reworked to fit the asynchronous `perp-dex-tools` stack. Adapters
currently support Backpack and Lighter, and more exchanges can be added by
implementing the adapter interface.

## Prerequisites

1. Set the API credentials for the desired exchange in your environment (same
   keys required by the existing exchange clients). For example, Backpack:

   ```bash
   export BACKPACK_PUBLIC_KEY=...
   export BACKPACK_SECRET_KEY=...
   ```

2. Ensure your `.env` file (default `./.env`) also contains the keys. The
   strategy will reuse the existing exchange client for connectivity and
   WebSocket subscriptions. When targeting Lighter, also populate the
   `LIGHTER_*` variables from `env_example.txt`.

## Running the strategy

```bash
python runbot.py \
  --strategy simple-mm \
  --exchange backpack \
  --ticker SOL \
  --quantity 0.5 \
  --spread 0.35 \
  --refresh-interval 1.5 \
  --target-position 0 \
  --max-position 3 \
  --position-threshold 0.2 \
  --inventory-skew 0.4
```

### Important flags

- `--strategy simple-mm`: selects the adapter-based maker.
- `--exchange backpack|lighter`: choose the exchange mapping; errors if the
  adapter is not implemented yet.
- `--ticker`: spot symbol base (e.g. `SOL`, `BTC`, `ETH`).
- `--quantity`: maker order size per side (Decimal).
- `--spread`: total percentage spread between bid/ask quotes (default 0.30).
- `--spread-ticks`: alternatively express spread as an integer number of ticks,
  overriding `--spread`.
- `--refresh-interval`: seconds between re-quoting cycles.
- `--target-position`: desired net exposure (absolute).
- `--max-position`: hard stop; breaches trigger a market flatten.
- `--position-threshold`: buffer beyond target before pausing accumulation in
  that direction.
- `--inventory-skew`: between `0` and `1`, shifts quotes away from your current
  inventory to speed rebalancing.

All other arguments from the original grid bot remain available but are ignored
when `--strategy simple-mm` is chosen.

### Example (Lighter tick-mode)

```bash
python runbot.py \
  --strategy simple-mm \
  --exchange lighter \
  --ticker ETH \
  --quantity 0.5 \
  --spread-ticks 12 \
  --refresh-interval 1.0 \
  --target-position 0 \
  --max-position 1.5 \
  --position-threshold 0.25 \
  --inventory-skew 0.3
```

## Behaviour summary

- Loads contract metadata through the adapter (which calls the appropriate
  exchange client).
- Maintains one bid and one ask sized at `quantity` and spaced by the requested
  spread.
- Cancels and re-quotes when price shifts by at least one tick (or
  `min_price_move`).
- Removes the bid (or ask) when inventory exceeds `target_position +
  position_threshold` in that direction.
- Issues a market `flatten` order via `place_market_order` if net position
  breaches `max_position`.
- Logs all quote updates through the shared `TradingLogger`.

## Key files

- `strategies/mm/simple_mm.py`
  - Strategy event loop
- `strategies/mm/config.py`
  - Shared configuration dataclass
- `strategies/mm/adapters/*.py`
  - Exchange adapters (`BackpackAdapter`, `LighterAdapter`, add more here)
- `runbot.py`
  - CLI flag wiring and strategy selection

## Notes

- This integration focuses on quoting behaviour. Advanced features from the
  original project (dashboard, database logging, hedge routines) are not yet
  ported.
- Adapters are responsible for enforcing maker/POST_ONLY or REDUCE_ONLY flags as
  needed by the underlying venue.
- Each adapter should ensure position reporting returns signed quantities; the
  provided implementations rely on Backpack's open positions endpoint and
  Lighter's `get_account_positions`.
