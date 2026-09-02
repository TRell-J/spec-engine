"""EARS grammar tests — the anti-slop mechanism."""

import pytest

from core import ears

# One example per pattern, taken from the shape Mavin et al. define.
CONFORMANT = {
    "ubiquitous": "THE quote service SHALL retain audit records for 400 days",
    "event": (
        "WHEN a rep submits a discount above 20%, the quote service SHALL return "
        "HTTP 202"
    ),
    "state": (
        "WHILE a quote is unresolved, the quote service SHALL show the "
        "`pending_approval` badge"
    ),
    "optional": (
        "WHERE the tenant has SSO enabled, the quote service SHALL expire sessions "
        "after 15 minutes"
    ),
    "unwanted": (
        "IF the Salesforce write fails, THEN the quote service SHALL return HTTP 502"
    ),
    "complex": (
        "WHILE a quote is pending, WHEN 4 business hours elapse, the quote service "
        "SHALL reassign the approval to the `director` queue"
    ),
}


@pytest.mark.parametrize("expected,statement", CONFORMANT.items())
def test_each_pattern_parses_to_itself(expected, statement):
    parsed = ears.parse(statement)
    assert parsed.pattern == expected, parsed.issues
    assert parsed.conforms, [i.code for i in parsed.issues]


def test_complex_is_matched_before_state_and_event():
    """Ordering matters: a complex sentence must not be read as state-driven."""
    parsed = ears.parse(CONFORMANT["complex"])
    assert parsed.pattern == "complex"
    assert parsed.precondition and parsed.trigger


def test_clauses_are_captured():
    parsed = ears.parse(CONFORMANT["event"])
    assert parsed.trigger == "a rep submits a discount above 20%"
    assert parsed.system == "quote service"
    assert parsed.response == "return HTTP 202"


@pytest.mark.parametrize(
    "statement",
    [
        "The system should be fast.",
        "Users can approve quotes.",
        "Make the approval flow work.",
        "",
        "SHALL return 200",
    ],
)
def test_non_ears_prose_is_rejected(statement):
    parsed = ears.parse(statement)
    assert not parsed.parses
    assert not parsed.conforms
    assert parsed.issues


def test_grammar_failure_names_the_templates():
    issue = ears.parse("the thing works well").issues[0]
    assert issue.code in {"EARS-GRAMMAR", "EARS-UNFALSIFIABLE", "EARS-UNMEASURED"}


# --------------------------------------------------------------------------- #
# Response-clause quality: parsing is necessary, not sufficient
# --------------------------------------------------------------------------- #


def test_parsing_sentence_can_still_be_unfalsifiable():
    parsed = ears.parse("THE system SHALL be fast and scalable")
    assert parsed.parses
    assert not parsed.conforms
    assert "EARS-UNFALSIFIABLE" in {issue.code for issue in parsed.issues}


@pytest.mark.parametrize(
    "term", ["fast", "secure", "robust", "intuitive", "several", "appropriate"]
)
def test_vague_terms_in_the_response_are_caught(term):
    parsed = ears.parse(f"THE service SHALL be {term} within 200 ms")
    assert "EARS-UNFALSIFIABLE" in {issue.code for issue in parsed.issues}


def test_hedging_after_shall_is_caught():
    parsed = ears.parse("THE service SHALL try to return 200 within 100 ms")
    assert "EARS-HEDGE" in {issue.code for issue in parsed.issues}


def test_response_without_a_measurable_outcome_is_caught():
    parsed = ears.parse("WHEN a quote is sent, the service SHALL notify the rep")
    assert "EARS-UNMEASURED" in {issue.code for issue in parsed.issues}


@pytest.mark.parametrize(
    "response",
    [
        "return HTTP 403",
        "write exactly 1 audit row",
        "set the status to `pending`",
        "respond within 250 ms",
    ],
)
def test_measurable_responses_pass(response):
    parsed = ears.parse(f"WHEN a quote is sent, the service SHALL {response}")
    assert parsed.conforms, [i.code for i in parsed.issues]


def test_compound_response_is_flagged():
    long_response = " ".join(["and do the next thing"] * 12)
    parsed = ears.parse(f"THE service SHALL return 200 {long_response}")
    assert "EARS-COMPOUND" in {issue.code for issue in parsed.issues}


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #


def test_normalize_fixes_keyword_casing():
    assert ears.normalize(
        "when a quote is sent, the service shall return 202"
    ) == "WHEN a quote is sent, the service SHALL return 202"


def test_normalize_leaves_the_response_clause_alone():
    """'the' inside a response must not be shouted into a keyword."""
    normalized = ears.normalize(
        "when a rep submits, the service SHALL update the ledger and return 200"
    )
    assert "update the ledger" in normalized
    assert "THE ledger" not in normalized


def test_normalize_is_idempotent():
    once = ears.normalize(CONFORMANT["unwanted"])
    assert ears.normalize(once) == once


def test_normalize_passes_through_unparseable_text():
    assert ears.normalize("not ears at all") == "not ears at all"


def test_grammar_reference_lists_all_six_patterns():
    reference = ears.grammar_reference()
    for name in ears.PATTERN_TEMPLATES:
        assert name in reference
