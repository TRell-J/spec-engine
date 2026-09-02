"""Verification tests — the gate that makes the compiler trustworthy."""

import pytest

from core.verifier import assess_source, find_quote_line, verify
from tests.test_schemas import a_claim, a_criterion, a_requirement, a_spec, a_task


# --------------------------------------------------------------------------- #
# Grounding: the anti-hallucination check
# --------------------------------------------------------------------------- #


def test_reference_spec_passes_against_its_source(spec, document):
    report = verify(spec, document)
    assert report.passed, [f.message for f in report.findings if f.blocking]
    assert report.coverage.grounding_rate == 100.0
    assert report.coverage.ears_rate == 100.0
    assert report.coverage.coverage_rate == 100.0


def test_a_fabricated_quote_blocks_the_build(spec, document):
    spec.claims[0].quote = "The system must support blockchain settlement."
    report = verify(spec, document)
    assert not report.passed
    codes = {f.code for f in report.findings if f.blocking}
    assert "GROUND-MISSING" in codes


def test_grounding_survives_rewrapped_whitespace(spec, document):
    """A quote spanning a line break is still grounded."""
    spec.claims[0].quote = " ".join(spec.claims[0].quote.split())
    assert verify(spec, document).coverage.grounding_rate == 100.0


def test_grounding_is_case_insensitive(spec, document):
    spec.claims[0].quote = spec.claims[0].quote.upper()
    assert verify(spec, document).coverage.grounding_rate == 100.0


def test_grounding_is_skipped_when_no_source_is_supplied(spec):
    report = verify(spec, "")
    assert report.coverage.grounding_rate == 100.0
    assert not any(f.code == "GROUND-MISSING" for f in report.findings)


def test_find_quote_line_locates_the_source_line(document):
    line = find_quote_line(document, "The system of record for accounts")
    assert line and document.splitlines()[line - 1].startswith("The system of record")


def test_find_quote_line_returns_none_for_absent_text(document):
    assert find_quote_line(document, "blockchain settlement rails") is None


# --------------------------------------------------------------------------- #
# Coverage
# --------------------------------------------------------------------------- #


def test_uncovered_requirement_blocks_the_build():
    spec = a_spec(requirements=[a_requirement(), a_requirement(id="REQ-002")])
    report = verify(spec, "")
    assert not report.passed
    assert any(
        f.code == "COVER-NO-TASK" and f.location == "REQ-002" for f in report.findings
    )


def test_dropped_claim_is_reported_as_major():
    spec = a_spec(claims=[a_claim(), a_claim(id="CLM-002")])
    findings = [f for f in verify(spec, "").findings if f.code == "TRACE-DROPPED-CLAIM"]
    assert findings and findings[0].severity == "major"
    assert findings[0].location == "CLM-002"


def test_context_claims_are_allowed_to_go_unused():
    spec = a_spec(claims=[a_claim(), a_claim(id="CLM-002", kind="context")])
    assert not any(f.code == "TRACE-DROPPED-CLAIM" for f in verify(spec, "").findings)


# --------------------------------------------------------------------------- #
# EARS conformance is enforced at the gate too
# --------------------------------------------------------------------------- #


def test_unmeasurable_criterion_is_a_major_finding():
    requirement = a_requirement(
        acceptance_criteria=[
            a_criterion(
                statement="WHEN a quote is sent, the service SHALL notify the rep"
            )
        ]
    )
    report = verify(a_spec(requirements=[requirement]), "")
    assert any(f.code == "EARS-UNMEASURED" for f in report.findings)
    assert report.passed  # major, not blocking
    assert report.coverage.ears_rate == 0.0


def test_ears_rate_counts_only_fully_conformant_criteria(spec):
    assert verify(spec, "").coverage.ears_rate == 100.0


# --------------------------------------------------------------------------- #
# Decisions and tasks
# --------------------------------------------------------------------------- #


def test_unanswered_decision_is_recorded_as_an_assumption(spec, document):
    spec.decisions[0].answer = None
    report = verify(spec, document)
    assert any(
        f.code == "DEC-ASSUMED" and f.location == "DEC-001" for f in report.findings
    )
    assert report.passed  # assuming a default is visible, not fatal
    assert report.coverage.decision_rate == 50.0


@pytest.mark.parametrize(
    "verification",
    [
        "make sure it works fine",  # names a build tool, is not a command
        "the feature is done and reviewed",
        "verify it behaves correctly",
    ],
)
def test_task_with_a_hand_wavy_verification_is_flagged(verification):
    spec = a_spec(tasks=[a_task(verification=verification)])
    assert any(f.code == "TASK-UNVERIFIABLE" for f in verify(spec, "").findings)


@pytest.mark.parametrize(
    "verification",
    [
        "pytest tests/test_approval.py::test_over_limit -q",
        "npm run test:e2e -- --spec queue.spec.ts",
        "POST /v1/quotes with discount=0.45 returns HTTP 403",
        "assert queue depth == 0 after the worker drains",
    ],
)
def test_real_verification_commands_pass(verification):
    spec = a_spec(tasks=[a_task(verification=verification)])
    assert not any(f.code == "TASK-UNVERIFIABLE" for f in verify(spec, "").findings)


def test_oversized_task_is_flagged():
    spec = a_spec(tasks=[a_task(estimate_hours=7.5)])
    assert any(f.code == "TASK-OVERSIZED" for f in verify(spec, "").findings)


def test_duplicate_requirement_titles_are_flagged():
    spec = a_spec(
        requirements=[a_requirement(), a_requirement(id="REQ-002")],
        tasks=[a_task(satisfies=["REQ-001", "REQ-002"])],
    )
    assert any(f.code == "REQ-DUPLICATE" for f in verify(spec, "").findings)


# --------------------------------------------------------------------------- #
# Report shape
# --------------------------------------------------------------------------- #


def test_findings_are_ordered_worst_first():
    spec = a_spec(
        claims=[a_claim(), a_claim(id="CLM-002")],
        requirements=[a_requirement(), a_requirement(id="REQ-002")],
    )
    severities = [f.severity for f in verify(spec, "").findings]
    assert severities == sorted(
        severities, key=lambda s: {"blocker": 0, "major": 1, "minor": 2}[s]
    )


def test_blocking_summary_feeds_the_repair_turn():
    spec = a_spec(requirements=[a_requirement(), a_requirement(id="REQ-002")])
    summary = verify(spec, "").blocking_summary()
    assert "REQ-002" in summary and "FIX:" in summary


def test_counts_add_up(spec, document):
    report = verify(spec, document)
    assert sum(report.counts().values()) == len(report.findings)


# --------------------------------------------------------------------------- #
# Source readiness
# --------------------------------------------------------------------------- #


def test_source_readiness_flags_a_thin_document():
    readiness = assess_source("Build a tool.")
    assert not readiness.compilable
    assert any("Too short" in note for note in readiness.notes)


def test_source_readiness_notes_are_specific(document):
    readiness = assess_source(document)
    assert readiness.compilable
    assert readiness.word_count > 25
    # The reference document names actors, numbers, and failure paths.
    assert not any("No actor" in note for note in readiness.notes)
    assert not any("No numbers" in note for note in readiness.notes)


def test_source_readiness_does_not_grade():
    """Deliberately has no score — earlier versions scored every real PRD zero."""
    readiness = assess_source("Some prose about a product.")
    assert not hasattr(readiness, "score")
