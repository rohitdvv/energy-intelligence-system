"""Claude token → USD pricing table for the cost ledger.

Prices are per 1M tokens (USD). Cache reads are billed at 0.1x input and
cache writes (5-min ephemeral) at 1.25x input, per Anthropic's published
pricing. Values are best-effort defaults; override via the ANTHROPIC_PRICES
secret/env (JSON) if pricing changes.

We never fail the app over pricing: unknown models fall back to Sonnet-class
rates and cost is reported as an estimate.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelPrice:
    """Per-1M-token prices in USD."""

    input: float
    output: float
    cache_write: float  # 5-min ephemeral cache creation
    cache_read: float


# Keyed by a model-id prefix (longest match wins). Sonnet-class default.
_DEFAULT_PRICES: dict[str, ModelPrice] = {
    "claude-opus-4":     ModelPrice(input=15.0, output=75.0, cache_write=18.75, cache_read=1.50),
    "claude-sonnet-4":   ModelPrice(input=3.0,  output=15.0, cache_write=3.75,  cache_read=0.30),
    "claude-3-5-haiku":  ModelPrice(input=0.80, output=4.0,  cache_write=1.0,   cache_read=0.08),
    "claude-3-haiku":    ModelPrice(input=0.25, output=1.25, cache_write=0.30,  cache_read=0.03),
}

# Fallback when no prefix matches (Sonnet-class).
_FALLBACK = ModelPrice(input=3.0, output=15.0, cache_write=3.75, cache_read=0.30)


def _load_overrides() -> dict[str, ModelPrice]:
    """Optional JSON override from env/secret ANTHROPIC_PRICES."""
    raw = os.environ.get("ANTHROPIC_PRICES", "")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return {
            k: ModelPrice(
                input=float(v["input"]),
                output=float(v["output"]),
                cache_write=float(v.get("cache_write", v["input"] * 1.25)),
                cache_read=float(v.get("cache_read", v["input"] * 0.1)),
            )
            for k, v in data.items()
        }
    except Exception as exc:  # noqa: BLE001 — never fail over pricing
        logger.warning("Ignoring malformed ANTHROPIC_PRICES override: %s", exc)
        return {}


_PRICES = {**_DEFAULT_PRICES, **_load_overrides()}


def price_for(model: str) -> ModelPrice:
    """Return the ModelPrice for *model* via longest-prefix match."""
    best: ModelPrice | None = None
    best_len = -1
    for prefix, price in _PRICES.items():
        if model.startswith(prefix) and len(prefix) > best_len:
            best, best_len = price, len(prefix)
    return best if best is not None else _FALLBACK


def cost_usd(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float:
    """Compute the USD cost of a single Claude call from token counts."""
    p = price_for(model)
    return round(
        (input_tokens / 1_000_000) * p.input
        + (output_tokens / 1_000_000) * p.output
        + (cache_write_tokens / 1_000_000) * p.cache_write
        + (cache_read_tokens / 1_000_000) * p.cache_read,
        6,
    )
