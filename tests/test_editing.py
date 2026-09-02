"""Editing rules: a human correction is held to the same standard as the model's.

These are pure functions, so they are tested directly. The UI wiring that calls
them is covered in `test_features.py`.
"""

import pytest
from pydantic import ValidationError

from core import editing
from core.verifier import verify

DOC = """# Deal Desk: discount approval routing

Any quote with a discount above 20% must be approved by the deal desk.

The system of record for accounts and opportunities is Salesforce.
"""


def row(quote, kind="requirement", reading=""):
    return {"Quote": quote, "Kind": kind, "What it commits to": reading}


# --------------------------------------------------------------------------- #
# Claims
# --------------------------------------------------------------------------- #


def test_rows_become_claims_numbered_from_one():
    claims = editing.rebuild_claims(
        [row("Any quote with a discount above 20%"), row("Salesforce")], DOC
    )
    assert [c.id for c in claims] == ["CLM-001", "CLM-002"]


def test_ids_are_reassigned_after_a_deletion():
    """A gap in the numbering would leave requirements pointing at the wrong claim."""
    claims = editing.rebuild_claims([row("Salesforce")], DOC)
    assert claims[0].id == "CLM-001"


def test_a_quote_from_the_document_gets_its_line():
    claims = editing.rebuild_claims([row("The system of record")], DOC)
    assert claims[0].line == 5


def test_an_invented_quote_gets_no_line_and_will_fail_the_gate():
    claims = editing.rebuild_claims([row("We will use a blockchain")], DOC)
    assert claims[0].line == 0


def test_blank_rows_are_dropped():
    claims = editing.rebuild_claims(
        [row("   "), row(""), row("Salesforce")], DOC
    )
    assert len(claims) == 1


def test_the_reading_defaults_to_the_quote():
    claims = editing.rebuild_claims([row("Salesforce", reading="")], DOC)
    assert claims[0].reading == "Salesforce"


def test_an_unknown_kind_falls_back_rather_than_crashing():
    claims = editing.rebuild_claims([row("Salesforce", kind="nonsense")], DOC)
    assert claims[0].kind == "requirement"


@pytest.mark.parametrize("kind", editing.CLAIM_KINDS)
def test_every_valid_kind_is_accepted(kind):
    claims = editing.rebuild_claims([row("Salesforce", kind=kind)], DOC)
    assert claims[0].kind == kind


def test_whitespace_is_trimmed():
    claims = editing.rebuild_claims(
        [row("  Salesforce  ", reading="  Salesforce owns it  ")], DOC
    )
    assert claims[0].quote == "Salesforce"
    assert claims[0].reading == "Salesforce owns it"


def test_a_too_short_reading_falls_back_instead_of_failing_the_save():
    """The schema needs 3+ characters; a stray keystroke must not lose the edit."""
    claims = editing.rebuild_claims([row("Salesforce", reading="x")], DOC)
    assert claims[0].reading == "Salesforce"


# --------------------------------------------------------------------------- #
# Criteria
# --------------------------------------------------------------------------- #


def test_a_criterion_can_be_rewritten(spec):
    requirement = spec.requirements[0]
    statements = [c.statement for c in requirement.acceptance_criteria]
    statements[0] = (
        "WHEN a rep submits a quote, the quote service SHALL return HTTP 418"
    )
    updated = editing.apply_criteria(requirement, statements)
    assert "418" in updated.acceptance_criteria[0].statement
    assert updated.id == requirement.id


def test_criterion_ids_are_preserved(spec):
    requirement = spec.requirements[0]
    statements = [c.statement for c in requirement.acceptance_criteria]
    updated = editing.apply_criteria(requirement, statements)
    assert [c.id for c in updated.acceptance_criteria] == [
        c.id for c in requirement.acceptance_criteria
    ]


def test_a_non_ears_rewrite_is_refused(spec):
    requirement = spec.requirements[0]
    statements = [c.statement for c in requirement.acceptance_criteria]
    statements[0] = "the system should be fast"
    with pytest.raises(ValidationError):
        editing.apply_criteria(requirement, statements)


def test_a_refused_edit_leaves_the_original_untouched(spec):
    requirement = spec.requirements[0]
    before = requirement.acceptance_criteria[0].statement
    statements = [c.statement for c in requirement.acceptance_criteria]
    statements[0] = "nonsense that is not ears"
    with pytest.raises(ValidationError):
        editing.apply_criteria(requirement, statements)
    assert requirement.acceptance_criteria[0].statement == before


def test_editing_does_not_mutate_the_input(spec):
    requirement = spec.requirements[0]
    statements = [c.statement for c in requirement.acceptance_criteria]
    statements[0] = (
        "WHEN a rep submits a quote, the quote service SHALL return HTTP 418"
    )
    editing.apply_criteria(requirement, statements)
    assert "418" not in requirement.acceptance_criteria[0].statement


def test_the_title_can_be_changed(spec):
    requirement = spec.requirements[0]
    statements = [c.statement for c in requirement.acceptance_criteria]
    updated = editing.apply_criteria(requirement, statements, "A better title")
    assert updated.title == "A better title"


def test_a_blank_title_keeps_the_old_one(spec):
    requirement = spec.requirements[0]
    statements = [c.statement for c in requirement.acceptance_criteria]
    updated = editing.apply_criteria(requirement, statements, "   ")
    assert updated.title == requirement.title


def test_an_edit_that_weakens_a_criterion_is_caught_by_the_gate(spec, document):
    """Editing must not be a hole in the verifier."""
    requirement = spec.requirements[0]
    statements = [c.statement for c in requirement.acceptance_criteria]
    statements[0] = (
        "WHEN a rep submits a quote, the quote service SHALL notify the desk"
    )
    updated = editing.apply_criteria(requirement, statements)
    spec.requirements = editing.replace_requirement(spec.requirements, updated)
    codes = {f.code for f in verify(spec, document).findings}
    assert "EARS-UNMEASURED" in codes


def test_replace_requirement_preserves_order(spec):
    updated = spec.requirements[2].model_copy(update={"title": "Renamed"})
    replaced = editing.replace_requirement(spec.requirements, updated)
    assert [r.id for r in replaced] == [r.id for r in spec.requirements]
    assert replaced[2].title == "Renamed"


def test_a_mismatched_statement_count_is_refused(spec):
    """Guards the zip: silently dropping a criterion would weaken the spec."""
    requirement = spec.requirements[0]
    with pytest.raises(ValueError, match="expected 3 statements"):
        editing.apply_criteria(requirement, ["THE service SHALL return 200"])
