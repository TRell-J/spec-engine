"""Cost estimation tests."""

import pytest

from core import pricing


def test_a_longer_document_costs_more():
    short = pricing.estimate_compile("word " * 100, "claude-opus-5")
    long = pricing.estimate_compile("word " * 5000, "claude-opus-5")
    assert long.usd_high > short.usd_high
    assert long.input_tokens > short.input_tokens


def test_output_estimate_does_not_depend_on_document_length():
    """Only the input side scales with the document."""
    short = pricing.estimate_compile("word " * 100, "claude-opus-5")
    long = pricing.estimate_compile("word " * 5000, "claude-opus-5")
    assert short.output_tokens == long.output_tokens


def test_cheaper_models_cost_less():
    document = "word " * 2000
    opus = pricing.estimate_compile(document, "claude-opus-5")
    haiku = pricing.estimate_compile(document, "claude-haiku-4-5")
    assert haiku.usd_high < opus.usd_high


def test_an_unpriced_model_reports_tokens_and_no_dollar_figure():
    """Any model can be pointed at this app; most are not in the table.

    Quoting one model's price for another would be a fabrication, which is the
    exact failure this whole tool exists to catch.
    """
    estimate = pricing.estimate_compile("word " * 100, "some-local-llama")
    assert pricing.rate_for("some-local-llama") is None
    assert estimate.priced is False
    assert estimate.usd_low is None and estimate.usd_high is None
    assert estimate.input_tokens > 0 and estimate.output_tokens > 0
    assert "no published rate" in estimate.headline
    assert pricing.actual_cost(1000, 500, "some-local-llama") is None


def test_a_local_endpoint_is_free_whatever_the_model_is_called():
    estimate = pricing.estimate_compile(
        "word " * 400, "qwen3:32b", base_url="http://localhost:11434/v1"
    )
    assert estimate.free and estimate.priced
    assert estimate.usd_low == 0 and estimate.usd_high == 0
    assert "free" in estimate.headline.lower()
    assert pricing.actual_cost(
        9_000_000, 9_000_000, "qwen3:32b", base_url="http://127.0.0.1:8000/v1"
    ) == 0


def test_a_rate_can_be_supplied_for_a_model_the_table_does_not_know(monkeypatch):
    monkeypatch.setenv("SPEC_ENGINE_INPUT_RATE", "0.5")
    monkeypatch.setenv("SPEC_ENGINE_OUTPUT_RATE", "1.5")
    assert pricing.rate_for("anything-at-all") == (0.5, 1.5)
    assert pricing.actual_cost(1_000_000, 1_000_000, "anything-at-all") == 2.0


def test_a_nonsense_rate_override_is_ignored_rather_than_crashing(monkeypatch):
    """A bad override falls through to the table, not to an exception."""
    monkeypatch.setenv("SPEC_ENGINE_INPUT_RATE", "free please")
    assert pricing.rate_for("claude-opus-5") == (5.0, 25.0)
    assert pricing.rate_for("some-local-llama") is None


def test_non_anthropic_models_are_priced_too():
    document = "word " * 2000
    gpt = pricing.estimate_compile(document, "gpt-5")
    opus = pricing.estimate_compile(document, "claude-opus-5")
    deepseek = pricing.estimate_compile(document, "deepseek-chat")
    assert deepseek.usd_high < gpt.usd_high < opus.usd_high


def test_the_estimate_is_a_band_not_a_number():
    estimate = pricing.estimate_compile("word " * 800, "claude-opus-5")
    assert estimate.usd_low < estimate.usd_high
    assert "–" in estimate.headline or estimate.headline == "under a cent"


def test_a_partial_run_is_cheaper_than_a_full_one():
    document = "word " * 1000
    full = pricing.estimate_compile(document, "claude-opus-5")
    replan = pricing.estimate_compile(document, "claude-opus-5", passes=("decompose",))
    assert replan.calls == 1 and full.calls == 4
    assert replan.usd_high < full.usd_high


def test_headline_handles_a_trivially_small_run():
    estimate = pricing.estimate_compile("hi", "claude-haiku-4-5")
    assert isinstance(estimate.headline, str) and estimate.headline


@pytest.mark.parametrize(
    "model,expected",
    [
        ("claude-opus-5", 5.0 + 25.0),  # 1M in + 1M out
        ("claude-sonnet-5", 2.0 + 10.0),
        ("claude-haiku-4-5", 1.0 + 5.0),
    ],
)
def test_actual_cost_uses_the_published_rates(model, expected):
    assert pricing.actual_cost(1_000_000, 1_000_000, model) == pytest.approx(expected)


def test_actual_cost_of_a_realistic_run_is_plausible():
    """A four-pass compile of a short PRD should land in cents, not dollars."""
    cost = pricing.actual_cost(30_000, 12_000, "claude-opus-5")
    assert 0.01 < cost < 1.0


def test_detail_names_the_calls_and_tokens():
    detail = pricing.estimate_compile("word " * 500, "claude-opus-5").detail
    assert "4 model calls" in detail and "tokens" in detail


def test_actual_cost_bills_cache_reads_at_a_tenth():
    """Cache reads are billed separately, at 0.1x the base input rate."""
    plain = pricing.actual_cost(1_000_000, 1_000_000, "claude-opus-5")
    cached = pricing.actual_cost(
        1_000_000, 1_000_000, "claude-opus-5", cache_read_tokens=500_000
    )
    assert cached == pytest.approx(plain + 0.5 * 5.0 * 0.1)


def test_actual_cost_defaults_to_no_cache_reads():
    assert pricing.actual_cost(
        1_000_000, 1_000_000, "claude-opus-5", cache_read_tokens=0
    ) == pytest.approx(pricing.actual_cost(1_000_000, 1_000_000, "claude-opus-5"))
