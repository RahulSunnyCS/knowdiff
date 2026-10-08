"""
Per-model Claude pricing table (USD per million tokens).

Hand-maintained on purpose: fetching live prices is brittle and prices
change rarely. Update this dict when Anthropic adjusts pricing.

Cache-write price is 1.25x input (5-minute TTL, the only TTL this
pipeline uses); cache-read price is whatever Anthropic lists for the
model (0.1x input on most models, 0.05x on the 5.5 generation).

Last reviewed: 2026-10.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPrice:
    """Prices in USD per 1M tokens."""
    input_per_m: float
    output_per_m: float
    cache_read_per_m: float
    cache_write_per_m: float


# Keys match the model IDs passed to the SDK. Dated aliases are kept so an
# old scope.json keeps pricing correctly.
PRICES: dict[str, ModelPrice] = {
    # Current generation
    "claude-opus-5-5": ModelPrice(4.00, 20.00, 0.20, 5.00),
    "claude-sonnet-5-5": ModelPrice(2.00, 10.00, 0.20, 2.50),
    "claude-haiku-4-5": ModelPrice(1.00, 5.00, 0.10, 1.25),
    "claude-haiku-4-5-20251001": ModelPrice(1.00, 5.00, 0.10, 1.25),
    # Previous generations still served
    "claude-opus-5": ModelPrice(5.00, 25.00, 0.50, 6.25),
    "claude-opus-4-8": ModelPrice(5.00, 25.00, 0.50, 6.25),
    "claude-opus-4-7": ModelPrice(5.00, 25.00, 0.50, 6.25),
    "claude-opus-4-6": ModelPrice(5.00, 25.00, 0.50, 6.25),
    "claude-sonnet-5": ModelPrice(2.00, 10.00, 0.20, 2.50),
    "claude-sonnet-4-6": ModelPrice(3.00, 15.00, 0.30, 3.75),
}

# Unknown model: assume Sonnet-class pricing rather than silently under-counting.
DEFAULT_PRICE = ModelPrice(3.00, 15.00, 0.30, 3.75)


def price_for(model: str) -> ModelPrice:
    return PRICES.get(model, DEFAULT_PRICE)


def cost_usd(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    p = price_for(model)
    return (
        input_tokens * p.input_per_m
        + output_tokens * p.output_per_m
        + cache_read_tokens * p.cache_read_per_m
        + cache_write_tokens * p.cache_write_per_m
    ) / 1_000_000
