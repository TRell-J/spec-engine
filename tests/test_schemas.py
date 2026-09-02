"""Schema tests: the traceability contract."""

import json

import pytest
from pydantic import ValidationError

from core.schemas import (
    AcceptanceCriterion,
    OpenDecision,
    Requirement,
    SourceClaim,
    SpecDocument,
    Task,
)


def a_criterion(**overrides) -> AcceptanceCriterion:
    payload = {
        "id": "AC-1",
        "statement": "WHEN a quote is sent, the service SHALL return HTTP 202",
    }
    payload.update(overrides)
    return AcceptanceCriterion(**payload)


def a_requirement(**overrides) -> Requirement:
    payload = {
        "id": "REQ-001",
        "title": "Approval gate",
        "user_story": "As a rep, I want held quotes, so that discounts are approved.",
        "acceptance_criteria": [a_criterion()],
        "traces_to": ["CLM-001"],
    }
    payload.update(overrides)
    return Requirement(**payload)


def a_task(**overrides) -> Task:
    payload = {
        "id": "TASK-001",
        "title": "Add the approvals table",
        "layer": "Database/Migration",
        "intent": "Versioned migration with a rollback path.",
        "satisfies": ["REQ-001"],
        "verification": "pytest tests/test_migrations.py -q",
        "estimate_hours": 3.0,
    }
    payload.update(overrides)
    return Task(**payload)


def a_claim(**overrides) -> SourceClaim:
    payload = {
        "id": "CLM-001",
        "quote": "quotes over 20% need approval",
        "line": 1,
        "kind": "requirement",
        "reading": "Discounted quotes need approval.",
    }
    payload.update(overrides)
    return SourceClaim(**payload)


def a_spec(**overrides) -> SpecDocument:
    payload = {
        "name": "Deal desk",
        "spec_id": "SPEC-2026-001",
        "summary": "A compiled specification.",
        "architecture_notes": "A service over PostgreSQL.",
        "claims": [a_claim()],
        "decisions": [],
        "requirements": [a_requirement()],
        "tasks": [a_task()],
    }
    payload.update(overrides)
    return SpecDocument(**payload)


# --------------------------------------------------------------------------- #
# Identity and shape
# --------------------------------------------------------------------------- #


def test_reference_spec_is_valid(spec):
    assert spec.spec_id == "SPEC-2026-001"
    assert spec.requirements and spec.tasks


def test_spec_round_trips_through_json(spec):
    restored = SpecDocument.model_validate(json.loads(spec.model_dump_json()))
    assert restored == spec


@pytest.mark.parametrize("bad", ["CLM1", "CL-001", "REQ-001", ""])
def test_claim_ids_are_enforced(bad):
    with pytest.raises(ValidationError):
        a_claim(id=bad)


def test_ids_are_upper_cased():
    assert a_claim(id="clm-009").id == "CLM-009"
    assert a_requirement(traces_to=["clm-001"]).traces_to == ["CLM-001"]


def test_unknown_task_layer_is_rejected():
    with pytest.raises(ValidationError):
        a_task(layer="Middleware")


# --------------------------------------------------------------------------- #
# Acceptance criteria are parsed, not trusted
# --------------------------------------------------------------------------- #


def test_non_ears_acceptance_criterion_is_rejected():
    with pytest.raises(ValidationError, match="EARS"):
        a_criterion(statement="The system should handle errors properly")


def test_acceptance_criterion_is_normalized_on_construction():
    criterion = a_criterion(
        statement="when a quote is sent, the service shall return 202"
    )
    assert criterion.statement.startswith("WHEN ")
    assert " SHALL " in criterion.statement


def test_requirement_rejects_duplicate_criterion_ids():
    with pytest.raises(ValidationError, match="duplicate"):
        a_requirement(acceptance_criteria=[a_criterion(), a_criterion()])


def test_requirement_needs_at_least_one_criterion():
    with pytest.raises(ValidationError):
        a_requirement(acceptance_criteria=[])


# --------------------------------------------------------------------------- #
# Traceability is structural, not advisory
# --------------------------------------------------------------------------- #


def test_requirement_must_trace_to_evidence():
    with pytest.raises(ValidationError):
        a_requirement(traces_to=[])


def test_requirement_traces_reject_foreign_id_types():
    with pytest.raises(ValidationError, match="CLM-/DEC-"):
        a_requirement(traces_to=["TASK-001"])


def test_task_must_satisfy_a_requirement():
    with pytest.raises(ValidationError):
        a_task(satisfies=[])


def test_requirement_tracing_to_unknown_evidence_is_rejected():
    with pytest.raises(ValidationError, match="unknown evidence"):
        a_spec(requirements=[a_requirement(traces_to=["CLM-404"])])


def test_task_satisfying_an_unknown_requirement_is_rejected():
    with pytest.raises(ValidationError, match="unknown requirements"):
        a_spec(tasks=[a_task(satisfies=["REQ-404"])])


def test_dangling_task_dependency_is_rejected():
    with pytest.raises(ValidationError, match="unknown tasks"):
        a_spec(tasks=[a_task(depends_on=["TASK-404"])])


def test_user_story_shape_is_enforced():
    with pytest.raises(ValidationError, match="user_story"):
        a_requirement(user_story="Reps need approvals to work")


# --------------------------------------------------------------------------- #
# Graph
# --------------------------------------------------------------------------- #


def test_self_dependency_is_rejected():
    with pytest.raises(ValidationError):
        a_task(depends_on=["TASK-001"])


def test_dependency_cycle_is_rejected():
    tasks = [
        a_task(id="TASK-001", depends_on=["TASK-003"]),
        a_task(id="TASK-002", depends_on=["TASK-001"]),
        a_task(id="TASK-003", depends_on=["TASK-002"]),
    ]
    with pytest.raises(ValidationError, match="cycle"):
        a_spec(tasks=tasks)


def test_duplicate_ids_are_rejected():
    with pytest.raises(ValidationError, match="duplicate task ids"):
        a_spec(tasks=[a_task(), a_task()])


def test_execution_waves_respect_dependencies(spec):
    waves = spec.execution_waves()
    position = {task_id: index for index, wave in enumerate(waves) for task_id in wave}
    for task in spec.tasks:
        for dependency in task.depends_on:
            assert position[dependency] < position[task.id]


def test_every_task_is_scheduled_exactly_once(spec):
    scheduled = [t for wave in spec.execution_waves() for t in wave]
    assert sorted(scheduled) == sorted(task.id for task in spec.tasks)


# --------------------------------------------------------------------------- #
# Derived views
# --------------------------------------------------------------------------- #


def test_uncovered_requirements_are_reported():
    spec = a_spec(
        requirements=[a_requirement(), a_requirement(id="REQ-002")],
    )
    assert spec.uncovered_requirements() == ["REQ-002"]


def test_unused_claims_ignore_context_claims():
    spec = a_spec(
        claims=[
            a_claim(),
            a_claim(id="CLM-002", kind="requirement"),
            a_claim(id="CLM-003", kind="context"),
        ]
    )
    assert spec.unused_claims() == ["CLM-002"]


def test_reference_spec_has_no_structural_leaks(spec):
    assert spec.uncovered_requirements() == []
    assert spec.unused_claims() == []
    assert spec.dependency_cycle() is None


def test_tasks_for_and_totals(spec):
    assert spec.tasks_for("REQ-001")
    assert spec.total_hours() > 0
    assert spec.criteria_count() >= len(spec.requirements)


def test_decision_resolution_prefers_the_human_answer():
    decision = OpenDecision(
        id="DEC-001",
        question="Who approves above 40%?",
        why_it_blocks="An agent would let anyone approve.",
        proposed_default="The VP of Sales approves above 40%.",
    )
    assert not decision.answered
    assert decision.resolution == "The VP of Sales approves above 40%."
    decision.answer = "The CFO approves above 40%."
    assert decision.answered
    assert decision.resolution == "The CFO approves above 40%."
