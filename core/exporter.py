"""Artifact rendering.

The file names and their split follow the convention that spec-driven tooling
has settled on (GitHub Spec Kit, Amazon Kiro): requirements, design and tasks as
three separate documents, so the output drops into an existing agent workflow
instead of asking one to be built around it.

    requirements.md   user stories + EARS acceptance criteria
    design.md         architecture, decisions, risks, scope boundary
    tasks.md          the agent-executable checklist, in dependency order
    traceability.md   source quote -> claim -> requirement -> task
    spec.json         the whole document, machine-readable
    handoff.txt       the instruction that hands the above to an agent

Everything here is a pure function of the spec. No I/O, no globals.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from .schemas import SpecDocument
from .verifier import VerificationReport

REQUIREMENTS_FILE = "requirements.md"
DESIGN_FILE = "design.md"
TASKS_FILE = "tasks.md"
TRACEABILITY_FILE = "traceability.md"
JSON_FILE = "spec.json"
MERMAID_FILE = "plan.mmd"
HANDOFF_FILE = "handoff.txt"


def _cell(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").replace("|", "\\|").strip()


def _node(identifier: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "", identifier)


# --------------------------------------------------------------------------- #
# requirements.md
# --------------------------------------------------------------------------- #


def to_requirements_md(spec: SpecDocument) -> str:
    lines = [
        f"# Requirements — {spec.name}",
        "",
        f"`{spec.spec_id}`  ·  {len(spec.requirements)} requirements  ·  "
        f"{spec.criteria_count()} acceptance criteria",
        "",
        "Acceptance criteria are written in EARS notation (Easy Approach to "
        "Requirements Syntax). Each one parses against a fixed template and names "
        "an outcome a test can assert.",
        "",
        "---",
        "",
    ]

    for requirement in spec.requirements:
        evidence = ", ".join(f"`{ref}`" for ref in requirement.traces_to)
        lines += [
            f"## {requirement.id} — {requirement.title}",
            "",
            f"**Priority:** {requirement.priority}  ·  **Derived from:** {evidence}",
            "",
            f"> {requirement.user_story}",
            "",
            "**Acceptance criteria**",
            "",
        ]
        for criterion in requirement.acceptance_criteria:
            pattern = criterion.parsed.pattern or "unparsed"
            lines.append(
                f"{criterion.id}. {criterion.statement}  <sub>({pattern})</sub>"
            )
        lines += [""]

        tasks = spec.tasks_for(requirement.id)
        if tasks:
            lines += [
                "**Delivered by:** "
                + ", ".join(f"`{task.id}`" for task in tasks),
                "",
            ]
        lines += ["---", ""]

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# design.md
# --------------------------------------------------------------------------- #


def to_design_md(spec: SpecDocument) -> str:
    lines = [
        f"# Design — {spec.name}",
        "",
        f"`{spec.spec_id}`",
        "",
        "## Summary",
        "",
        spec.summary,
        "",
        "## Architecture",
        "",
        spec.architecture_notes,
        "",
        "## Decisions",
        "",
    ]

    if spec.decisions:
        lines += [
            "| ID | Question | Resolution | Source |",
            "| --- | --- | --- | --- |",
        ]
        for decision in spec.decisions:
            source = "answered" if decision.answered else "**assumed default**"
            lines.append(
                f"| `{decision.id}` | {_cell(decision.question)} "
                f"| {_cell(decision.resolution)} | {source} |"
            )
        lines.append("")
        unanswered = [d for d in spec.decisions if not d.answered]
        if unanswered:
            lines += [
                f"> {len(unanswered)} of {len(spec.decisions)} decisions were not "
                "answered by a human. The build proceeds on the assumed default; "
                "each one is a place the spec could be wrong.",
                "",
            ]
    else:
        lines += ["_No open decisions were identified._", ""]

    lines += ["## Out of scope", ""]
    lines += [f"- {item}" for item in spec.out_of_scope] or [
        "_Nothing recorded as deliberately excluded._"
    ]
    lines += ["", "## Risks", ""]
    lines += [f"- {item}" for item in spec.risks] or ["_None recorded._"]
    lines += ["", "## Execution plan", "", "```mermaid", to_mermaid(spec), "```", ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# tasks.md
# --------------------------------------------------------------------------- #


def to_tasks_md(spec: SpecDocument) -> str:
    waves = spec.execution_waves()
    index = {task.id: task for task in spec.tasks}

    lines = [
        f"# Tasks — {spec.name}",
        "",
        f"`{spec.spec_id}`  ·  {len(spec.tasks)} tasks  ·  {spec.total_hours()}h "
        f"estimated  ·  {len(waves)} waves",
        "",
        "Work inside a wave has no ordering constraint and can run in parallel. "
        "Each task names the requirements it satisfies and the command that "
        "verifies it.",
        "",
    ]

    for wave_number, wave in enumerate(waves, start=1):
        lines += [f"## Wave {wave_number}", ""]
        for task_id in wave:
            task = index[task_id]
            depends = (
                ", ".join(f"`{dep}`" for dep in task.depends_on)
                if task.depends_on
                else "none"
            )
            satisfies = ", ".join(f"`{ref}`" for ref in task.satisfies)
            lines += [
                f"- [ ] **{task.id} — {task.title}**",
                f"  - Layer: {task.layer}  ·  Estimate: {task.estimate_hours}h  "
                f"·  Depends on: {depends}",
                f"  - Satisfies: {satisfies}",
                f"  - {task.intent}",
                f"  - Verify: `{task.verification}`",
            ]
        lines.append("")

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# traceability.md
# --------------------------------------------------------------------------- #


def to_traceability_md(
    spec: SpecDocument, report: Optional[VerificationReport] = None
) -> str:
    lines = [
        f"# Traceability — {spec.name}",
        "",
        "Every requirement traces back to a quoted span of the source document or "
        "to a recorded decision, and forward to the tasks that deliver it. A row "
        "with no forward link is scope that will not get built; a requirement with "
        "no backward link is an invention.",
        "",
    ]

    if report is not None:
        coverage = report.coverage
        lines += [
            "| Check | Result |",
            "| --- | --- |",
            f"| Claims grounded in the source | {coverage.claims_grounded}/"
            f"{coverage.claims_total} ({coverage.grounding_rate}%) |",
            f"| Acceptance criteria EARS-conformant | {coverage.criteria_conformant}/"
            f"{coverage.criteria_total} ({coverage.ears_rate}%) |",
            f"| Requirements delivered by a task | {coverage.requirements_covered}/"
            f"{coverage.requirements_total} ({coverage.coverage_rate}%) |",
            f"| Decisions answered by a human | {coverage.decisions_answered}/"
            f"{coverage.decisions_total} ({coverage.decision_rate}%) |",
            "",
        ]

    lines += [
        "## Forward trace",
        "",
        "| Requirement | Derived from | Evidence | Delivered by |",
        "| --- | --- | --- | --- |",
    ]
    claims = spec.claim_index()
    decisions = spec.decision_index()
    for requirement in spec.requirements:
        evidence_parts: List[str] = []
        for ref in requirement.traces_to:
            if ref in claims:
                evidence_parts.append(f'"{_cell(claims[ref].quote)[:70]}"')
            elif ref in decisions:
                evidence_parts.append(
                    f"decision: {_cell(decisions[ref].resolution)[:70]}"
                )
        tasks = ", ".join(f"`{t.id}`" for t in spec.tasks_for(requirement.id))
        lines.append(
            f"| `{requirement.id}` {_cell(requirement.title)} "
            f"| {', '.join(f'`{r}`' for r in requirement.traces_to)} "
            f"| {' · '.join(evidence_parts) or '—'} "
            f"| {tasks or '**none**'} |"
        )

    lines += ["", "## Source claims", "", "| Claim | Line | Kind | Quote | Used by |", "| --- | --- | --- | --- | --- |"]
    for claim in spec.claims:
        used = [r.id for r in spec.requirements if claim.id in r.traces_to]
        lines.append(
            f"| `{claim.id}` | {claim.line} | {claim.kind} "
            f"| {_cell(claim.quote)[:90]} "
            f"| {', '.join(f'`{r}`' for r in used) or '**dropped**'} |"
        )

    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Graph
# --------------------------------------------------------------------------- #


def to_mermaid(spec: SpecDocument, direction: str = "LR") -> str:
    lines = [f"flowchart {direction}"]
    for wave_number, wave in enumerate(spec.execution_waves(), start=1):
        lines.append(f'    subgraph WAVE{wave_number}["Wave {wave_number}"]')
        lines.append("        direction TB")
        index = {task.id: task for task in spec.tasks}
        for task_id in wave:
            task = index[task_id]
            label = re.sub(r"\s+", " ", task.title)[:44].replace('"', "'")
            lines.append(f'        {_node(task_id)}["{task_id}<br/>{label}"]')
        lines.append("    end")
    for task in spec.tasks:
        for dependency in task.depends_on:
            lines.append(f"    {_node(dependency)} --> {_node(task.id)}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# JSON + bundle
# --------------------------------------------------------------------------- #


def to_json(spec: SpecDocument, indent: int = 2) -> str:
    return spec.model_dump_json(indent=indent)


def to_handoff_prompt(spec: SpecDocument) -> str:
    """The instruction that hands a verified spec to whoever builds from it.

    This tool ends where the agent begins, and the seam is a real artifact
    rather than an implication. Everything the prompt asserts is enforced
    upstream: the waves come from the dependency graph, the criteria parse as
    EARS, and every requirement traces to a quote the verifier found in the
    source. So the agent is not being asked to trust the document — it is being
    told which properties already hold and where to look when one seems wrong.
    """
    waves = spec.execution_waves()
    assumed = [d for d in spec.decisions if not d.answered]

    lines = [
        f"Implement the specification for: {spec.name} ({spec.spec_id}).",
        "",
        f"Read {REQUIREMENTS_FILE}, {DESIGN_FILE} and {TASKS_FILE} before writing "
        "anything.",
        "",
        "How to work:",
        "",
        f"1. {TASKS_FILE} is ordered into {len(waves)} waves. Complete a wave "
        "before starting the next. Tasks inside one wave have no ordering "
        "constraint between them.",
        "2. Every task names the requirements it satisfies. Do not build "
        "anything that no requirement asks for — if you think something is "
        "missing, say so instead of adding it.",
        f"3. The acceptance criteria in {REQUIREMENTS_FILE} are written in EARS "
        "notation and each one names an outcome a test can assert. Satisfy them "
        "literally; do not soften a threshold, status code, or message.",
        "4. Each task carries the command that verifies it. Run it before "
        "marking the task done.",
    ]

    if assumed:
        lines += [
            f"5. {DESIGN_FILE} records which decisions a human answered and which "
            f"are assumed defaults. {len(assumed)} of "
            f"{len(spec.decisions)} were not answered by a human "
            f"({', '.join(d.id for d in assumed)}). If one of those blocks you or "
            "looks wrong, stop and ask rather than deciding for yourself.",
        ]

    lines += [
        f"{6 if assumed else 5}. Every requirement traces back to a quoted "
        f"sentence from the source document — see {TRACEABILITY_FILE}. If a "
        "requirement looks wrong, read the quote it came from before changing it.",
        "",
        f"Scope: {len(spec.requirements)} requirements, {spec.criteria_count()} "
        f"acceptance criteria, {len(spec.tasks)} tasks.",
    ]

    if spec.out_of_scope:
        lines += ["", "Explicitly out of scope — do not build these:"]
        lines += [f"- {item}" for item in spec.out_of_scope]

    if spec.risks:
        lines += ["", "Known risks going in:"]
        lines += [f"- {item}" for item in spec.risks]

    return "\n".join(lines) + "\n"


def build_bundle(
    spec: SpecDocument, report: Optional[VerificationReport] = None
) -> Dict[str, str]:
    """Filename -> content, ready for download buttons or a file write."""
    return {
        REQUIREMENTS_FILE: to_requirements_md(spec),
        DESIGN_FILE: to_design_md(spec),
        TASKS_FILE: to_tasks_md(spec),
        TRACEABILITY_FILE: to_traceability_md(spec, report),
        JSON_FILE: to_json(spec),
        MERMAID_FILE: to_mermaid(spec),
        HANDOFF_FILE: to_handoff_prompt(spec),
    }
