"""Artifact rendering tests."""

import json

from core import exporter
from core.schemas import SpecDocument
from core.verifier import verify


def test_bundle_has_the_spec_driven_artifact_set(spec, document):
    bundle = exporter.build_bundle(spec, verify(spec, document))
    assert set(bundle) == {
        "requirements.md",
        "design.md",
        "tasks.md",
        "traceability.md",
        "spec.json",
        "plan.mmd",
        "handoff.txt",
    }
    for name, content in bundle.items():
        assert content.strip(), f"{name} is empty"


def test_json_round_trips(spec):
    assert SpecDocument.model_validate(json.loads(exporter.to_json(spec))) == spec


# --------------------------------------------------------------------------- #
# requirements.md
# --------------------------------------------------------------------------- #


def test_requirements_md_renders_every_requirement(spec):
    rendered = exporter.to_requirements_md(spec)
    for requirement in spec.requirements:
        assert f"## {requirement.id} — {requirement.title}" in rendered
        assert requirement.user_story in rendered
        for criterion in requirement.acceptance_criteria:
            assert criterion.statement in rendered


def test_requirements_md_labels_the_ears_pattern(spec):
    rendered = exporter.to_requirements_md(spec)
    for pattern in ("event", "unwanted", "ubiquitous", "complex"):
        assert f"({pattern})" in rendered


def test_requirements_md_shows_evidence_and_delivery(spec):
    rendered = exporter.to_requirements_md(spec)
    assert "**Derived from:** `CLM-001`, `DEC-001`" in rendered
    assert "**Delivered by:**" in rendered


# --------------------------------------------------------------------------- #
# design.md
# --------------------------------------------------------------------------- #


def test_design_md_records_decisions_and_their_source(spec):
    rendered = exporter.to_design_md(spec)
    assert "## Decisions" in rendered
    for decision in spec.decisions:
        assert decision.question in rendered
    assert "answered" in rendered


def test_design_md_warns_when_defaults_were_assumed(spec):
    spec.decisions[0].answer = None
    rendered = exporter.to_design_md(spec)
    assert "**assumed default**" in rendered
    assert "1 of 2 decisions were not" in rendered


def test_design_md_carries_scope_risks_and_the_plan_graph(spec):
    rendered = exporter.to_design_md(spec)
    assert "## Out of scope" in rendered and spec.out_of_scope[0] in rendered
    assert "## Risks" in rendered and spec.risks[0] in rendered
    assert "```mermaid" in rendered and "flowchart LR" in rendered


# --------------------------------------------------------------------------- #
# tasks.md
# --------------------------------------------------------------------------- #


def test_tasks_md_is_a_wave_ordered_checklist(spec):
    rendered = exporter.to_tasks_md(spec)
    waves = spec.execution_waves()
    for wave_number in range(1, len(waves) + 1):
        assert f"## Wave {wave_number}" in rendered
    for task in spec.tasks:
        assert f"- [ ] **{task.id} — {task.title}**" in rendered
        assert task.verification in rendered


def test_tasks_md_states_what_each_task_satisfies(spec):
    rendered = exporter.to_tasks_md(spec)
    for task in spec.tasks:
        assert "Satisfies: " + ", ".join(f"`{r}`" for r in task.satisfies) in rendered


def test_wave_one_holds_only_dependency_free_tasks(spec):
    first_wave = spec.execution_waves()[0]
    index = {task.id: task for task in spec.tasks}
    assert all(not index[task_id].depends_on for task_id in first_wave)


# --------------------------------------------------------------------------- #
# traceability.md
# --------------------------------------------------------------------------- #


def test_traceability_links_quote_to_requirement_to_task(spec, document):
    rendered = exporter.to_traceability_md(spec, verify(spec, document))
    assert "## Forward trace" in rendered and "## Source claims" in rendered
    for requirement in spec.requirements:
        assert f"`{requirement.id}`" in rendered
    for claim in spec.claims:
        assert f"`{claim.id}`" in rendered


def test_traceability_marks_a_requirement_nothing_builds(spec, document):
    spec.tasks = [t for t in spec.tasks if "REQ-004" not in t.satisfies]
    rendered = exporter.to_traceability_md(spec, verify(spec, document))
    assert "**none**" in rendered


def test_traceability_marks_a_dropped_claim(spec, document):
    for requirement in spec.requirements:
        requirement.traces_to = [r for r in requirement.traces_to if r != "CLM-004"]
        if not requirement.traces_to:
            requirement.traces_to = ["CLM-001"]
    rendered = exporter.to_traceability_md(spec, verify(spec, document))
    assert "**dropped**" in rendered


def test_traceability_includes_the_verification_scorecard(spec, document):
    rendered = exporter.to_traceability_md(spec, verify(spec, document))
    assert "Claims grounded in the source" in rendered
    assert "Acceptance criteria EARS-conformant" in rendered


def test_traceability_renders_without_a_report(spec):
    assert "## Forward trace" in exporter.to_traceability_md(spec)


# --------------------------------------------------------------------------- #
# Graph
# --------------------------------------------------------------------------- #


def test_mermaid_declares_every_task_and_edge(spec):
    rendered = exporter.to_mermaid(spec)
    assert rendered.startswith("flowchart LR")
    for task in spec.tasks:
        node = task.id.replace("-", "")
        assert f'{node}["{task.id}' in rendered
        for dependency in task.depends_on:
            assert f"{dependency.replace('-', '')} --> {node}" in rendered


def test_mermaid_groups_tasks_into_waves(spec):
    rendered = exporter.to_mermaid(spec)
    assert rendered.count("subgraph") == len(spec.execution_waves())


def test_mermaid_ids_never_contain_hyphens(spec):
    for line in exporter.to_mermaid(spec).splitlines():
        if "-->" in line:
            left, right = line.split("-->")
            assert "-" not in left.strip() and "-" not in right.strip()


def test_markdown_tables_escape_pipes(spec, document):
    spec.claims[0].quote = "a | b | c"
    rendered = exporter.to_traceability_md(spec, verify(spec, document))
    assert "a \\| b \\| c" in rendered


# --------------------------------------------------------------------------- #
# The handoff
# --------------------------------------------------------------------------- #


def test_the_handoff_names_the_documents_it_hands_over(spec):
    prompt = exporter.to_handoff_prompt(spec)
    for filename in (
        exporter.REQUIREMENTS_FILE,
        exporter.DESIGN_FILE,
        exporter.TASKS_FILE,
        exporter.TRACEABILITY_FILE,
    ):
        assert filename in prompt


def test_the_handoff_states_the_real_wave_count(spec):
    prompt = exporter.to_handoff_prompt(spec)
    assert f"{len(spec.execution_waves())} waves" in prompt
    assert f"{len(spec.requirements)} requirements" in prompt
    assert f"{len(spec.tasks)} tasks" in prompt


def test_the_handoff_repeats_the_scope_boundary(spec):
    """An agent that never sees the out-of-scope list will build into it."""
    prompt = exporter.to_handoff_prompt(spec)
    for item in spec.out_of_scope:
        assert item in prompt


def test_the_handoff_names_decisions_no_human_answered(spec):
    """The one thing the agent must not quietly accept."""
    spec.decisions[0].answer = None
    prompt = exporter.to_handoff_prompt(spec)
    assert spec.decisions[0].id in prompt
    assert "stop and ask" in prompt


def test_the_handoff_stays_quiet_when_every_decision_was_answered(spec):
    assert all(d.answered for d in spec.decisions), "fixture assumption"
    assert "stop and ask" not in exporter.to_handoff_prompt(spec)


def test_the_handoff_ships_in_the_bundle(spec, document):
    bundle = exporter.build_bundle(spec, verify(spec, document))
    assert exporter.HANDOFF_FILE in bundle
    assert bundle[exporter.HANDOFF_FILE].strip()


def test_the_handoff_claims_nothing_the_gate_does_not_enforce(spec):
    """Every promise in the prompt is a property verified upstream."""
    prompt = exporter.to_handoff_prompt(spec)
    assert "EARS" in prompt
    assert "traces back to a quoted sentence" in prompt
    # and it must not promise the one thing this tool never checks
    assert "correct" not in prompt.lower().replace("incorrect", "")
