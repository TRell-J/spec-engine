"""What a compile will cost, before you spend it.

Deliberately an estimate with a stated range rather than a single confident
number: token counts depend on the document's content, and the output length
depends on how much the model finds to say. Showing "$0.34" would imply a
precision this cannot have.

Rates are USD per million tokens, from each vendor's published pricing.

Any model can be pointed at this app, and most of them are not in the table
below. Three honest outcomes, never a fourth:

    a known model      a priced range
    a local endpoint   free, because it runs on your own hardware
    anything else      token counts and no dollar figure

Inventing a price for an unknown model would be the same failure mode this
whole tool exists to prevent. Set SPEC_ENGINE_INPUT_RATE and
SPEC_ENGINE_OUTPUT_RATE (USD per million tokens) to price one yourself.
"""

from __future__ import annotations

import os
from typing import Dict, Optional, Tuple

from pydantic import BaseModel, ConfigDict

from .providers import is_local

Rate = Tuple[float, float]

# model id -> (input $/MTok, output $/MTok)
RATES: Dict[str, Rate] = {
    # Anthropic
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    # OpenAI
    "gpt-5": (1.25, 10.0),
    "gpt-5-mini": (0.25, 2.0),
    "gpt-4.1": (2.0, 8.0),
    "gpt-4.1-mini": (0.4, 1.6),
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.6),
    # Google
    "gemini-2.5-pro": (1.25, 10.0),
    "gemini-2.5-flash": (0.3, 2.5),
    # Open weights, hosted
    "deepseek-chat": (0.27, 1.1),
    "deepseek-reasoner": (0.55, 2.19),
    "deepseek/deepseek-chat": (0.27, 1.1),
    "llama-3.3-70b-versatile": (0.59, 0.79),
    "meta-llama/Llama-3.3-70B-Instruct-Turbo": (0.88, 0.88),
    "mistral-large-latest": (2.0, 6.0),
}

FREE_RATE: Rate = (0.0, 0.0)

# Anthropic bills cache reads on their own line, at 0.1x the base input
# price (docs.anthropic.com, "Prompt caching").
CACHE_READ_FACTOR = 0.1

# Roughly four characters per token for English prose.
CHARS_PER_TOKEN = 4

# Prompt overhead per pass (system prompt, grammar reference, instructions).
PASS_OVERHEAD_TOKENS = 1300

# How much of the document each pass carries, and what it tends to emit.
# extract and interrogate read the whole document; specify reads it plus the
# evidence; decompose reads the requirements rather than the source.
PASS_PROFILE: Dict[str, Tuple[float, int]] = {
    "extract": (1.0, 1800),
    "interrogate": (1.2, 1200),
    "specify": (1.6, 4500),
    "decompose": (0.8, 3500),
}


class CostEstimate(BaseModel):
    """A range, plus the numbers it was derived from.

    `usd_low` and `usd_high` are None when the model has no published rate here
    — the token counts are still real, and are all that gets shown.
    """

    model_config = ConfigDict(extra="forbid")

    model: str
    calls: int
    input_tokens: int
    output_tokens: int
    usd_low: Optional[float] = None
    usd_high: Optional[float] = None
    priced: bool = True
    free: bool = False

    @property
    def headline(self) -> str:
        if self.free:
            return "free — this model runs on your own hardware"
        if not self.priced or self.usd_high is None or self.usd_low is None:
            return "no published rate for this model"
        if self.usd_high < 0.01:
            return "under a cent"
        return f"${self.usd_low:.2f}–${self.usd_high:.2f}"

    @property
    def detail(self) -> str:
        return (
            f"{self.calls} model calls · ~{self.input_tokens:,} in / "
            f"~{self.output_tokens:,} out tokens"
        )


def rate_for(model: str, base_url: str = "") -> Optional[Rate]:
    """USD per million tokens, or None when nothing here can price it."""
    override = _rate_from_env()
    if override is not None:
        return override
    if base_url and is_local(base_url):
        return FREE_RATE
    return RATES.get((model or "").strip())


def _rate_from_env() -> Optional[Rate]:
    raw_in = os.getenv("SPEC_ENGINE_INPUT_RATE", "").strip()
    raw_out = os.getenv("SPEC_ENGINE_OUTPUT_RATE", "").strip()
    if not raw_in and not raw_out:
        return None
    try:
        return (float(raw_in or 0.0), float(raw_out or 0.0))
    except ValueError:
        return None


def count_tokens(text: str) -> int:
    """Character-based approximation. Never claims to be exact."""
    return max(1, len(text or "") // CHARS_PER_TOKEN)


def estimate_compile(
    document: str,
    model: str,
    passes: Tuple[str, ...] = tuple(PASS_PROFILE),
    base_url: str = "",
) -> CostEstimate:
    """Estimate the cost of running `passes` over `document`."""
    document_tokens = count_tokens(document)
    rate = rate_for(model, base_url)

    input_tokens = 0
    output_tokens = 0
    for name in passes:
        share, emitted = PASS_PROFILE.get(name, (1.0, 2000))
        input_tokens += int(document_tokens * share) + PASS_OVERHEAD_TOKENS
        output_tokens += emitted

    if rate is None:
        return CostEstimate(
            model=model,
            calls=len(passes),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            priced=False,
        )

    input_rate, output_rate = rate
    midpoint = (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000
    # A repair round or a verbose document can push this up; a short one pulls
    # it down. The band is deliberately wide because the estimate is rough.
    return CostEstimate(
        model=model,
        calls=len(passes),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        usd_low=round(midpoint * 0.6, 4),
        usd_high=round(midpoint * 1.6, 4),
        free=rate == FREE_RATE,
    )


def actual_cost(
    input_tokens: int,
    output_tokens: int,
    model: str,
    base_url: str = "",
    *,
    cache_read_tokens: int = 0,
) -> Optional[float]:
    """What a finished run really cost, or None if this model has no rate here.

    `input_tokens` is the uncached remainder only; cache reads are billed on
    their own line at CACHE_READ_FACTOR the base input price
    (docs.anthropic.com, "Prompt caching").
    """
    rate = rate_for(model, base_url)
    if rate is None:
        return None
    input_rate, output_rate = rate
    return round(
        (
            input_tokens * input_rate
            + cache_read_tokens * input_rate * CACHE_READ_FACTOR
            + output_tokens * output_rate
        )
        / 1_000_000,
        4,
    )
