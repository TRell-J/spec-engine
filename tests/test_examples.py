"""Every example must pass the gate it demonstrates.

A hand-authored reference spec that fails its own verification would be worse
than having no example at all, so each one is held to exactly the standard a
live compile faces.
"""

import pytest

from core.verifier import verify
from examples import registry


@pytest.fixture(params=registry.EXAMPLES, ids=lambda e: e.key)
def example(request):
    return request.param


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #


def test_the_example_passes_verification(example):
    report = verify(example.spec(), example.document)
    assert report.passed, [
        f"{f.severity} {f.location} {f.code}: {f.message}"
        for f in report.findings
        if f.blocking
    ]


def test_the_example_has_no_findings_at_all(example):
    """Not just unblocked — clean. A demo should model the target state."""
    report = verify(example.spec(), example.document)
    assert report.findings == [], [
        f"{f.severity} {f.location} {f.code}" for f in report.findings
    ]


def test_every_claim_is_grounded_in_its_own_document(example):
    report = verify(example.spec(), example.document)
    assert report.coverage.grounding_rate == 100.0


def test_every_criterion_is_ears_conformant(example):
    assert verify(example.spec(), example.document).coverage.ears_rate == 100.0


def test_every_requirement_is_delivered_by_a_task(example):
    spec = example.spec()
    assert spec.uncovered_requirements() == []


def test_no_commitment_is_silently_dropped(example):
    assert example.spec().unused_claims() == []


def test_every_decision_is_answered_and_used(example):
    spec = example.spec()
    referenced = {
        ref
        for requirement in spec.requirements
        for ref in requirement.traces_to
        if ref.startswith("DEC-")
    }
    for decision in spec.decisions:
        assert decision.answered, f"{decision.id} is unanswered"
        assert decision.id in referenced, f"{decision.id} is referenced by nothing"


# --------------------------------------------------------------------------- #
# Shape
# --------------------------------------------------------------------------- #


def test_the_plan_is_a_dag_with_parallel_waves(example):
    spec = example.spec()
    assert spec.dependency_cycle() is None
    waves = spec.execution_waves()
    assert len(waves) >= 2, "a single-wave plan demonstrates nothing about ordering"
    assert sum(len(w) for w in waves) == len(spec.tasks)


def test_the_example_is_substantial_enough_to_be_useful(example):
    spec = example.spec()
    assert len(spec.requirements) >= 4
    assert len(spec.tasks) >= 5
    assert spec.criteria_count() >= len(spec.requirements)
    assert spec.claims and spec.decisions


def test_tasks_span_more_than_one_layer(example):
    layers = {task.layer for task in example.spec().tasks}
    assert len(layers) >= 3, f"{example.key} only exercises {layers}"


def test_the_spec_exports_without_error(example):
    from core import exporter

    spec = example.spec()
    bundle = exporter.build_bundle(spec, verify(spec, example.document))
    assert len(bundle) == 7
    for name, content in bundle.items():
        assert content.strip(), f"{name} is empty for {example.key}"


# --------------------------------------------------------------------------- #
# The catalogue
# --------------------------------------------------------------------------- #


def test_every_example_is_distinct():
    keys = [e.key for e in registry.EXAMPLES]
    labels = [e.label for e in registry.EXAMPLES]
    documents = [e.document for e in registry.EXAMPLES]
    assert len(set(keys)) == len(keys)
    assert len(set(labels)) == len(labels)
    assert len(set(documents)) == len(documents)


def test_spec_ids_are_unique_across_examples():
    ids = [e.spec().spec_id for e in registry.EXAMPLES]
    assert len(set(ids)) == len(ids)


def test_a_fresh_copy_is_returned_each_time(example):
    """A walkthrough mutates decisions; the catalogue must not carry that over."""
    first = example.spec()
    first.decisions[0].answer = "mutated by a previous visitor"
    assert example.spec().decisions[0].answer != "mutated by a previous visitor"


def test_an_unknown_key_falls_back_to_the_default():
    assert registry.get("no-such-example").key == registry.DEFAULT_KEY


def test_documents_map_matches_the_catalogue():
    documents = registry.documents()
    assert set(documents) == {e.label for e in registry.EXAMPLES}
    for example in registry.EXAMPLES:
        assert documents[example.label] == example.document
