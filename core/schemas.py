"""The spec contract: source claims → decisions → requirements → tasks.

Every layer traces to the one above it, and the top layer traces to a verbatim
quote from the source document. That chain is the anti-hallucination mechanism:
a requirement that cannot name the sentence it came from is either an
undeclared assumption or an invention, and both are defects.

    SourceClaim   what the document actually says, quoted verbatim
    OpenDecision  what the document does not say, and must
    Requirement   EARS acceptance criteria, tracing to claims and decisions
    Task          agent-executable work, tracing to requirements
"""

from __future__ import annotations

import re
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import ears

# --------------------------------------------------------------------------- #
# Vocabularies
# --------------------------------------------------------------------------- #

ClaimKind = Literal["requirement", "constraint", "assumption", "context"]

Priority = Literal["must", "should", "could"]

TaskLayer = Literal[
    "Database/Migration",
    "API/Backend",
    "Worker/Async",
    "Frontend/UX",
    "Integration",
    "Eval/Harness",
    "Infrastructure",
]

TASK_LAYERS = (
    "Database/Migration",
    "API/Backend",
    "Worker/Async",
    "Frontend/UX",
    "Integration",
    "Eval/Harness",
    "Infrastructure",
)

CLAIM_ID = re.compile(r"^CLM-\d{3,}$")
DECISION_ID = re.compile(r"^DEC-\d{3,}$")
REQUIREMENT_ID = re.compile(r"^REQ-\d{3,}$")
TASK_ID = re.compile(r"^TASK-\d{3,}$")
SPEC_ID = re.compile(r"^SPEC-\d{4}-\d{3,}$")


def _id_validator(pattern: re.Pattern, label: str):
    def _check(value: str) -> str:
        value = (value or "").strip().upper()
        if not pattern.match(value):
            raise ValueError(f"{label} must match {pattern.pattern}, got {value!r}")
        return value

    return _check


def _clean_ids(values: List[str], pattern: re.Pattern, label: str) -> List[str]:
    cleaned: List[str] = []
    for value in values:
        value = (value or "").strip().upper()
        if not value:
            continue
        if not pattern.match(value):
            raise ValueError(f"{label} reference must match {pattern.pattern}, got {value!r}")
        if value not in cleaned:
            cleaned.append(value)
    return cleaned


# --------------------------------------------------------------------------- #
# Layer 1 - what the document says
# --------------------------------------------------------------------------- #


class SourceClaim(BaseModel):
    """One statement extracted from the source, with its verbatim evidence."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    id: str = Field(description="CLM-001")
    quote: str = Field(
        min_length=3,
        description="Verbatim span copied from the source document, unmodified",
    )
    line: int = Field(ge=0, description="1-indexed source line the quote starts on")
    kind: ClaimKind
    reading: str = Field(
        min_length=3,
        description="What this commits the build to, stated plainly",
    )

    _validate_id = field_validator("id")(_id_validator(CLAIM_ID, "claim id"))


class OpenDecision(BaseModel):
    """A choice the document leaves unmade, which the build cannot proceed without."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    id: str = Field(description="DEC-001")
    question: str = Field(min_length=8)
    why_it_blocks: str = Field(
        min_length=8, description="What an agent would be forced to invent"
    )
    options: List[str] = Field(default_factory=list)
    proposed_default: str = Field(
        min_length=3, description="The answer assumed if nobody answers"
    )
    answer: Optional[str] = Field(
        default=None, description="Human answer; None means the default stands"
    )

    _validate_id = field_validator("id")(_id_validator(DECISION_ID, "decision id"))

    @property
    def resolution(self) -> str:
        return self.answer.strip() if self.answer and self.answer.strip() else self.proposed_default

    @property
    def answered(self) -> bool:
        return bool(self.answer and self.answer.strip())


# --------------------------------------------------------------------------- #
# Layer 2 - what the system must do
# --------------------------------------------------------------------------- #


class AcceptanceCriterion(BaseModel):
    """One EARS statement. Parsed, not trusted."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    id: str = Field(description="AC-1, unique within its requirement")
    statement: str = Field(min_length=8, description="An EARS-conformant sentence")

    @field_validator("statement")
    @classmethod
    def _must_parse(cls, value: str) -> str:
        parsed = ears.parse(value)
        if not parsed.parses:
            raise ValueError(
                "acceptance criterion is not EARS-conformant. Use one of:\n"
                + ears.grammar_reference()
                + f"\nGot: {value!r}"
            )
        return ears.normalize(value)

    @property
    def parsed(self) -> "ears.EarsParse":
        return ears.parse(self.statement)


class Requirement(BaseModel):
    """A user story plus the EARS criteria that decide whether it is met."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    id: str = Field(description="REQ-001")
    title: str = Field(min_length=4)
    user_story: str = Field(
        min_length=12, description="As a <role>, I want <capability>, so that <benefit>"
    )
    acceptance_criteria: List[AcceptanceCriterion] = Field(min_length=1)
    traces_to: List[str] = Field(
        min_length=1,
        description="CLM-/DEC- ids this requirement is derived from",
    )
    priority: Priority = "must"

    _validate_id = field_validator("id")(_id_validator(REQUIREMENT_ID, "requirement id"))

    @field_validator("user_story")
    @classmethod
    def _story_shape(cls, value: str) -> str:
        lowered = value.lower()
        if not lowered.startswith("as a") or " i want" not in lowered:
            raise ValueError(
                "user_story must read 'As a <role>, I want <capability>, so that "
                f"<benefit>'. Got: {value!r}"
            )
        return value

    @field_validator("traces_to")
    @classmethod
    def _valid_traces(cls, values: List[str]) -> List[str]:
        cleaned: List[str] = []
        for value in values:
            value = (value or "").strip().upper()
            if not value:
                continue
            if not (CLAIM_ID.match(value) or DECISION_ID.match(value)):
                raise ValueError(f"traces_to must hold CLM-/DEC- ids, got {value!r}")
            if value not in cleaned:
                cleaned.append(value)
        if not cleaned:
            raise ValueError("every requirement must trace to a claim or a decision")
        return cleaned

    @model_validator(mode="after")
    def _unique_criteria_ids(self) -> "Requirement":
        ids = [c.id for c in self.acceptance_criteria]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate acceptance criterion ids in {self.id}")
        return self


# --------------------------------------------------------------------------- #
# Layer 3 - what gets built
# --------------------------------------------------------------------------- #


class Task(BaseModel):
    """One agent-executable unit of work."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    id: str = Field(description="TASK-001")
    title: str = Field(min_length=6, description="Imperative and specific")
    layer: TaskLayer
    intent: str = Field(min_length=12, description="What changes, and where")
    satisfies: List[str] = Field(
        min_length=1, description="REQ- ids this task delivers"
    )
    depends_on: List[str] = Field(default_factory=list)
    verification: str = Field(
        min_length=6, description="A runnable command or an explicit assertion"
    )
    estimate_hours: float = Field(gt=0, le=8)

    _validate_id = field_validator("id")(_id_validator(TASK_ID, "task id"))

    @field_validator("satisfies")
    @classmethod
    def _valid_requirements(cls, values: List[str]) -> List[str]:
        cleaned = _clean_ids(values, REQUIREMENT_ID, "satisfies")
        if not cleaned:
            raise ValueError("every task must satisfy at least one requirement")
        return cleaned

    @field_validator("depends_on")
    @classmethod
    def _valid_dependencies(cls, values: List[str]) -> List[str]:
        return _clean_ids(values, TASK_ID, "depends_on")

    @model_validator(mode="after")
    def _no_self_dependency(self) -> "Task":
        if self.id in self.depends_on:
            raise ValueError(f"{self.id} cannot depend on itself")
        return self


# --------------------------------------------------------------------------- #
# The document
# --------------------------------------------------------------------------- #


class SpecDocument(BaseModel):
    """The compiled specification: evidence, decisions, requirements, plan."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(min_length=3)
    spec_id: str = Field(description="SPEC-2026-001")
    summary: str = Field(min_length=12)
    architecture_notes: str = Field(min_length=12)
    claims: List[SourceClaim] = Field(default_factory=list)
    decisions: List[OpenDecision] = Field(default_factory=list)
    requirements: List[Requirement] = Field(min_length=1)
    tasks: List[Task] = Field(min_length=1)
    out_of_scope: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)

    _validate_id = field_validator("spec_id")(_id_validator(SPEC_ID, "spec id"))

    @model_validator(mode="after")
    def _referential_integrity(self) -> "SpecDocument":
        for label, items in (
            ("claim", self.claims),
            ("decision", self.decisions),
            ("requirement", self.requirements),
            ("task", self.tasks),
        ):
            ids = [item.id for item in items]
            duplicates = sorted({i for i in ids if ids.count(i) > 1})
            if duplicates:
                raise ValueError(f"duplicate {label} ids: {duplicates}")

        evidence = {c.id for c in self.claims} | {d.id for d in self.decisions}
        dangling = sorted(
            {
                ref
                for requirement in self.requirements
                for ref in requirement.traces_to
                if ref not in evidence
            }
        )
        if dangling:
            raise ValueError(f"requirements trace to unknown evidence: {dangling}")

        requirement_ids = {r.id for r in self.requirements}
        unknown = sorted(
            {
                ref
                for task in self.tasks
                for ref in task.satisfies
                if ref not in requirement_ids
            }
        )
        if unknown:
            raise ValueError(f"tasks satisfy unknown requirements: {unknown}")

        task_ids = {t.id for t in self.tasks}
        missing = sorted(
            {ref for task in self.tasks for ref in task.depends_on if ref not in task_ids}
        )
        if missing:
            raise ValueError(f"tasks depend on unknown tasks: {missing}")

        cycle = self.dependency_cycle()
        if cycle:
            raise ValueError(f"task dependency cycle: {' -> '.join(cycle)}")
        return self

    # ---------------------------------------------------------------- helpers

    def requirement_index(self) -> Dict[str, Requirement]:
        return {r.id: r for r in self.requirements}

    def claim_index(self) -> Dict[str, SourceClaim]:
        return {c.id: c for c in self.claims}

    def decision_index(self) -> Dict[str, OpenDecision]:
        return {d.id: d for d in self.decisions}

    def evidence_for(self, requirement_id: str) -> List[str]:
        requirement = self.requirement_index().get(requirement_id)
        return list(requirement.traces_to) if requirement else []

    def tasks_for(self, requirement_id: str) -> List[Task]:
        return [t for t in self.tasks if requirement_id in t.satisfies]

    def uncovered_requirements(self) -> List[str]:
        """Requirements no task delivers — the classic spec-to-plan leak."""
        covered = {ref for task in self.tasks for ref in task.satisfies}
        return sorted(r.id for r in self.requirements if r.id not in covered)

    def unused_claims(self) -> List[str]:
        """Claims that reached no requirement — either noise, or dropped scope."""
        used = {ref for r in self.requirements for ref in r.traces_to}
        return sorted(
            c.id for c in self.claims if c.id not in used and c.kind != "context"
        )

    def total_hours(self) -> float:
        return round(sum(task.estimate_hours for task in self.tasks), 1)

    def criteria_count(self) -> int:
        return sum(len(r.acceptance_criteria) for r in self.requirements)

    def dependency_cycle(self) -> Optional[List[str]]:
        graph = {task.id: list(task.depends_on) for task in self.tasks}
        WHITE, GREY, BLACK = 0, 1, 2
        color = {node: WHITE for node in graph}
        stack: List[str] = []

        def visit(node: str) -> Optional[List[str]]:
            color[node] = GREY
            stack.append(node)
            for nxt in graph.get(node, []):
                if nxt not in color:
                    continue
                if color[nxt] == GREY:
                    return stack[stack.index(nxt):] + [nxt]
                if color[nxt] == WHITE:
                    found = visit(nxt)
                    if found:
                        return found
            stack.pop()
            color[node] = BLACK
            return None

        for node in graph:
            if color[node] == WHITE:
                found = visit(node)
                if found:
                    return found
        return None

    def execution_waves(self) -> List[List[str]]:
        """Topological layers: everything in a wave can run in parallel."""
        graph = {task.id: set(task.depends_on) for task in self.tasks}
        resolved: set = set()
        waves: List[List[str]] = []
        while len(resolved) < len(graph):
            wave = sorted(
                node for node, deps in graph.items()
                if node not in resolved and deps <= resolved
            )
            if not wave:  # unreachable: cycles rejected at validation time
                break
            waves.append(wave)
            resolved.update(wave)
        return waves
