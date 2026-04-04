"""Public pricing helpers for analysis-time cost estimation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TokenPricing:
    """Simple per-token pricing model in USD."""

    input_cost_per_token: float
    output_cost_per_token: float
    source: str


_ANTHROPIC_PUBLIC_PRICING: dict[str, TokenPricing] = {
    "claude-opus-4-6": TokenPricing(
        input_cost_per_token=5.0 / 1_000_000,
        output_cost_per_token=25.0 / 1_000_000,
        source="estimated_anthropic_public_pricing",
    ),
    "claude-sonnet-4-6": TokenPricing(
        input_cost_per_token=3.0 / 1_000_000,
        output_cost_per_token=15.0 / 1_000_000,
        source="estimated_anthropic_public_pricing",
    ),
}


def estimate_cost_from_public_pricing(
    *,
    provider: str | None,
    model: str | None,
    tokens_in: int,
    tokens_out: int,
) -> tuple[float | None, str | None]:
    """Estimate cost using public pricing when a provider did not report cost."""

    if provider != "anthropic" or model is None:
        return None, None

    pricing = _pricing_for_model(model)
    if pricing is None:
        return None, None

    estimated = (
        tokens_in * pricing.input_cost_per_token + tokens_out * pricing.output_cost_per_token
    )
    return estimated, pricing.source


def _pricing_for_model(model: str) -> TokenPricing | None:
    normalized = model.strip()
    if normalized in _ANTHROPIC_PUBLIC_PRICING:
        return _ANTHROPIC_PUBLIC_PRICING[normalized]

    for prefix, pricing in _ANTHROPIC_PUBLIC_PRICING.items():
        if normalized.startswith(prefix):
            return pricing

    return None
