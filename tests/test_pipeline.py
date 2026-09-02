"""Pipeline tests. The live path runs against a scripted fake client.

No test here makes a network call or spends a token.
"""

import json
from types import SimpleNamespace

import pytest

from core import pipeline
from core.pipeline import (
    PipelineError,
    call_structured,
    compile_spec,
    decompose,
    extract_claims,
    interrogate,
    json_schema_for,
    specify,
)
from core.schemas import SourceClaim, SpecDocument
from core.verifier import verify
from examples.reference import REFERENCE_DOCUMENT


class FakeClient:
    """Stands in for `anthropic.Anthropic` with a scripted response queue."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        text = item if isinstance(item, str) else json.dumps(item, default=str)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)],
            usage=SimpleNamespace(
                input_tokens=1000, output_tokens=2000, cache_read_input_tokens=0
            ),
        )


def dump(model) -> dict:
    return json.loads(model.model_dump_json())


@pytest.fixture
def payloads(spec):
    """The four pass payloads that reproduce the reference spec."""
    return {
        "extract": {
            "document_title": spec.name,
            "claims": [dump(c) for c in spec.claims],
        },
        "interrogate": {"decisions": [dump(d) for d in spec.decisions]},
        "specify": {"requirements": [dump(r) for r in spec.requirements]},
        "decompose": {
            "architecture_notes": spec.architecture_notes,
            "tasks": [dump(t) for t in spec.tasks],
            "out_of_scope": spec.out_of_scope,
            "risks": spec.risks,
        },
    }


# --------------------------------------------------------------------------- #
# Structured-output plumbing
# --------------------------------------------------------------------------- #


def test_schema_is_strict_everywhere():
    schema = json_schema_for(pipeline.SpecificationResult)

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        if node.get("type") == "object" or "properties" in node:
            assert node.get("additionalProperties") is False
            assert sorted(node.get("properties", {}).keys()) == node.get("required")
        assert "default" not in node
        for value in node.values():
            walk(value)

    walk(schema)


def test_request_pins_the_model_to_the_schema(payloads):
    client = FakeClient([payloads["extract"]])
    extract_claims(client, REFERENCE_DOCUMENT)
    request = client.calls[0]
    assert request["output_config"]["format"]["type"] == "json_schema"
    assert request["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_invalid_output_triggers_an_in_conversation_repair(payloads):
    broken = {"document_title": "x", "claims": [{"id": "NOPE", "quote": "a"}]}
    client = FakeClient([broken, payloads["extract"]])
    result = extract_claims(client, REFERENCE_DOCUMENT)
    assert len(result.claims) == 6
    assert len(client.calls) == 2
    assert "failed validation" in client.calls[1]["messages"][-1]["content"]


def test_fenced_json_is_still_parsed(payloads):
    fenced = "```json\n" + json.dumps(payloads["extract"]) + "\n```"
    assert extract_claims(FakeClient([fenced]), REFERENCE_DOCUMENT).claims


def test_persistent_schema_failure_raises(payloads):
    bad = {"nope": True}
    with pytest.raises(PipelineError, match="schema adherence failed"):
        call_structured(
            FakeClient([bad, bad, bad]),
            "system",
            "user",
            pipeline.ExtractionResult,
        )


def test_usage_is_accumulated_across_calls(payloads):
    usage = pipeline.Usage()
    extract_claims(FakeClient([payloads["extract"]]), REFERENCE_DOCUMENT, usage=usage)
    assert usage.calls == 1
    assert usage.input_tokens == 1000 and usage.output_tokens == 2000


# --------------------------------------------------------------------------- #
# Pass behavior
# --------------------------------------------------------------------------- #


def test_extract_repairs_line_numbers_locally(payloads):
    """The model's line numbers are advisory; the source is authoritative."""
    payload = json.loads(json.dumps(payloads["extract"]))
    for claim in payload["claims"]:
        claim["line"] = 999
    result = extract_claims(FakeClient([payload]), REFERENCE_DOCUMENT)
    for claim in result.claims:
        assert claim.line != 999
        assert claim.line > 0


def test_extract_prompt_numbers_the_source_lines(payloads):
    client = FakeClient([payloads["extract"]])
    extract_claims(client, REFERENCE_DOCUMENT)
    assert "   1 | # Deal Desk" in client.calls[0]["messages"][0]["content"]


def test_extract_system_prompt_forbids_adding_facts(payloads):
    client = FakeClient([payloads["extract"]])
    extract_claims(client, REFERENCE_DOCUMENT)
    system = client.calls[0]["system"][0]["text"]
    assert "verbatim" in system.lower()
    assert "checked against the source" in system


def test_interrogate_sees_the_already_extracted_claims(payloads, spec):
    client = FakeClient([payloads["interrogate"]])
    interrogate(client, REFERENCE_DOCUMENT, spec.claims)
    prompt = client.calls[0]["messages"][0]["content"]
    assert "claims_already_extracted" in prompt
    assert "CLM-001" in prompt


def test_specify_prompt_carries_the_ears_grammar(payloads, spec):
    client = FakeClient([payloads["specify"]])
    specify(client, REFERENCE_DOCUMENT, spec.claims, spec.decisions)
    system = client.calls[0]["system"][0]["text"]
    for keyword in ("WHEN", "WHILE", "WHERE", "SHALL"):
        assert keyword in system


def test_specify_prompt_marks_assumed_decisions(payloads, spec):
    spec.decisions[0].answer = None
    client = FakeClient([payloads["specify"]])
    specify(client, REFERENCE_DOCUMENT, spec.claims, spec.decisions)
    prompt = client.calls[0]["messages"][0]["content"]
    assert "assumed default, nobody answered" in prompt


def test_decompose_receives_the_requirements_and_layers(payloads, spec):
    client = FakeClient([payloads["decompose"]])
    decompose(client, spec.requirements)
    prompt = client.calls[0]["messages"][0]["content"]
    assert "REQ-001" in prompt and "Eval/Harness" in prompt


# --------------------------------------------------------------------------- #
# End-to-end compile
# --------------------------------------------------------------------------- #


def test_compile_produces_a_verified_spec(payloads, spec):
    client = FakeClient([payloads["specify"], payloads["decompose"]])
    result = compile_spec(
        client, REFERENCE_DOCUMENT, spec.claims, spec.decisions, title=spec.name
    )
    assert result.ok
    assert isinstance(result.spec, SpecDocument)
    assert result.report.passed
    assert result.repair_rounds == 0
    assert result.usage.calls == 2


def test_compile_repairs_a_failing_spec(payloads, spec):
    """An uncovered requirement fails the gate, and the repair turn fixes it."""
    broken_plan = json.loads(json.dumps(payloads["decompose"]))
    broken_plan["tasks"] = [
        t for t in broken_plan["tasks"] if "REQ-004" not in t["satisfies"]
    ]
    repair_payload = {
        "requirements": payloads["specify"]["requirements"],
        "tasks": payloads["decompose"]["tasks"],
    }
    client = FakeClient([payloads["specify"], broken_plan, repair_payload])
    result = compile_spec(
        client, REFERENCE_DOCUMENT, spec.claims, spec.decisions, title=spec.name
    )
    assert result.ok
    assert result.repair_rounds == 1
    assert result.report.passed
    defects = client.calls[2]["messages"][0]["content"]
    assert "REQ-004" in defects


def test_compile_keeps_the_last_valid_spec_when_repair_fails(payloads, spec):
    broken_plan = json.loads(json.dumps(payloads["decompose"]))
    broken_plan["tasks"] = [
        t for t in broken_plan["tasks"] if "REQ-004" not in t["satisfies"]
    ]
    client = FakeClient(
        [payloads["specify"], broken_plan, RuntimeError("connection reset")]
    )
    result = compile_spec(
        client, REFERENCE_DOCUMENT, spec.claims, spec.decisions, title=spec.name
    )
    assert result.ok  # a spec with known defects beats no spec
    assert not result.report.passed
    assert any(f.code == "COVER-NO-TASK" for f in result.report.findings)


def test_compile_reports_a_failing_pass_without_raising(spec):
    client = FakeClient([RuntimeError("overloaded")])
    result = compile_spec(client, REFERENCE_DOCUMENT, spec.claims, spec.decisions)
    assert not result.ok
    assert result.stage_reached == "specify"
    assert "overloaded" in result.error


def test_compile_grounds_verification_in_the_real_document(payloads, spec):
    """A fabricated claim reaching compile still fails the gate."""
    claims = [c.model_copy() for c in spec.claims]
    claims[0] = SourceClaim(
        id="CLM-001",
        quote="The product must ship on a blockchain.",
        line=1,
        kind="requirement",
        reading="Invented.",
    )
    client = FakeClient(
        [payloads["specify"], payloads["decompose"], payloads["specify"]]
    )
    result = compile_spec(client, REFERENCE_DOCUMENT, claims, spec.decisions)
    assert result.ok
    assert not result.report.passed
    assert any(f.code == "GROUND-MISSING" for f in result.report.findings)


# --------------------------------------------------------------------------- #
# Client construction
# --------------------------------------------------------------------------- #


def test_no_client_without_credentials():
    """No key means no compile — never a fabricated spec."""
    assert pipeline.build_client() is None
    assert not pipeline.has_credentials()


def test_model_is_configurable(monkeypatch):
    monkeypatch.setenv("SPEC_ENGINE_MODEL", "claude-sonnet-5")
    assert pipeline.resolve_model() == "claude-sonnet-5"
    monkeypatch.delenv("SPEC_ENGINE_MODEL")
    assert pipeline.resolve_model() == pipeline.DEFAULT_MODEL


def test_interrogation_never_pre_answers_its_own_questions(payloads):
    """`answer` is a required schema field; a model answer must be discarded."""
    pre_answered = {
        "decisions": [
            {
                "id": "DEC-001",
                "question": "Who approves above 40%?",
                "why_it_blocks": "An agent would let anyone approve.",
                "options": ["The VP", "The CFO"],
                "proposed_default": "The VP of Sales approves above 40%.",
                "answer": "The CFO decides, obviously.",
            }
        ]
    }
    result = interrogate(FakeClient([pre_answered]), REFERENCE_DOCUMENT, [])
    decision = result.decisions[0]
    assert decision.answer is None
    assert not decision.answered
    assert decision.resolution == "The VP of Sales approves above 40%."


# --------------------------------------------------------------------------- #
# Targeted re-runs
# --------------------------------------------------------------------------- #


def test_supplying_requirements_skips_the_specify_pass(payloads, spec):
    """Re-planning must cost one call, not two."""
    client = FakeClient([payloads["decompose"]])
    result = compile_spec(
        client,
        REFERENCE_DOCUMENT,
        spec.claims,
        spec.decisions,
        title=spec.name,
        requirements=spec.requirements,
    )
    assert result.ok
    assert result.usage.calls == 1
    assert len(client.calls) == 1
    system = client.calls[0]["system"][0]["text"]
    assert "planning pass" in system  # decompose, not specify


def test_a_replan_keeps_the_requirements_untouched(payloads, spec):
    client = FakeClient([payloads["decompose"]])
    result = compile_spec(
        client, REFERENCE_DOCUMENT, spec.claims, spec.decisions,
        title=spec.name, requirements=spec.requirements,
    )
    assert [r.id for r in result.spec.requirements] == [
        r.id for r in spec.requirements
    ]
    assert result.report.passed


# --------------------------------------------------------------------------- #
# Fixing one finding
# --------------------------------------------------------------------------- #


def _spec_missing_a_task(spec):
    """A spec whose REQ-004 has nothing building it."""
    return spec.model_copy(
        update={"tasks": [t for t in spec.tasks if "REQ-004" not in t.satisfies]}
    )


def test_repair_finding_fixes_one_defect(spec, document):
    from core.pipeline import repair_finding

    broken = _spec_missing_a_task(spec)
    report = verify(broken, document)
    finding = next(f for f in report.findings if f.code == "COVER-NO-TASK")

    corrected = {
        "requirements": [dump(r) for r in spec.requirements],
        "tasks": [dump(t) for t in spec.tasks],  # the full set puts TASK-005 back
    }
    fixed = repair_finding(
        FakeClient([corrected]), broken, finding, document=document
    )
    assert verify(fixed, document).passed
    assert fixed.tasks_for("REQ-004")


def test_repair_finding_sends_only_that_defect(spec, document):
    from core.pipeline import repair_finding

    broken = _spec_missing_a_task(spec)
    report = verify(broken, document)
    finding = next(f for f in report.findings if f.code == "COVER-NO-TASK")
    client = FakeClient(
        [{"requirements": [dump(r) for r in spec.requirements],
          "tasks": [dump(t) for t in spec.tasks]}]
    )
    repair_finding(client, broken, finding, document=document)
    sent = client.calls[0]["messages"][0]["content"]
    assert "REQ-004" in sent
    assert sent.count("[BLOCKER]") == 1


def test_repair_finding_rejects_a_fix_that_does_not_fix_it(spec, document):
    from core.pipeline import repair_finding

    broken = _spec_missing_a_task(spec)
    report = verify(broken, document)
    finding = next(f for f in report.findings if f.code == "COVER-NO-TASK")
    unchanged = {
        "requirements": [dump(r) for r in broken.requirements],
        "tasks": [dump(t) for t in broken.tasks],
    }
    with pytest.raises(PipelineError, match="still has COVER-NO-TASK"):
        repair_finding(
            FakeClient([unchanged]), broken, finding, document=document
        )


def test_repair_finding_counts_its_own_usage(spec, document):
    from core.pipeline import repair_finding

    broken = _spec_missing_a_task(spec)
    finding = next(
        f for f in verify(broken, document).findings if f.code == "COVER-NO-TASK"
    )
    usage = pipeline.Usage()
    repair_finding(
        FakeClient([{"requirements": [dump(r) for r in spec.requirements],
                     "tasks": [dump(t) for t in spec.tasks]}]),
        broken, finding, document=document, usage=usage,
    )
    assert usage.calls == 1
