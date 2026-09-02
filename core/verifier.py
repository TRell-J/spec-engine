"""Deterministic verification of a compiled spec.

No model runs here. Every check is a mechanical property of the spec plus the
source document it claims to come from — which is the point: the compiler is
allowed to be probabilistic, the gate is not.

The headline check is **grounding**. Each `SourceClaim` carries a quote that is
supposed to be copied verbatim from the source; this module goes and looks. A
claim whose quote is not in the document is a fabrication, and it fails the
build regardless of how plausible it reads.
"""

from __future__ import annotations

import re
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .schemas import SpecDocument

Severity = Literal["blocker", "major", "minor"]

SEVERITY_ORDER: Dict[str, int] = {"blocker": 0, "major": 1, "minor": 2}

# A verification step that names no command and asserts nothing is decoration.
# Two signals are required, because either alone gives false positives — "make
# sure it works" names a build tool without being a command.
_TOOL = re.compile(
    r"\b(pytest|npm|yarn|pnpm|go test|cargo|make|curl|psql|python -m|docker|"
    r"k6|locust|playwright|jest|vitest|GET|POST|PUT|PATCH|DELETE)\b",
    re.IGNORECASE,
)
_ASSERTION = re.compile(
    r"\b(assert|asserts|expect|returns?|responds? with|yields?|status)\b",
    re.IGNORECASE,
)
_COMMAND_SHAPE = re.compile(r"[/\\]|--|::|==|->|=>|\.\w{2,4}\b|\bHTTP\b|\b\d{3}\b")


def _is_runnable(verification: str) -> bool:
    """True when the step names something a reviewer could actually execute."""
    has_shape = bool(_COMMAND_SHAPE.search(verification))
    return has_shape and bool(
        _TOOL.search(verification) or _ASSERTION.search(verification)
    )


class Finding(BaseModel):
    """One defect, addressed to whoever has to fix it."""

    model_config = ConfigDict(extra="forbid")

    code: str
    severity: Severity
    location: str = Field(description="The id the defect belongs to")
    message: str
    fix_hint: str = ""

    @property
    def blocking(self) -> bool:
        return self.severity == "blocker"


class Coverage(BaseModel):
    """The three ratios that say whether the spec hangs together."""

    model_config = ConfigDict(extra="forbid")

    claims_total: int = 0
    claims_grounded: int = 0
    criteria_total: int = 0
    criteria_conformant: int = 0
    requirements_total: int = 0
    requirements_covered: int = 0
    decisions_total: int = 0
    decisions_answered: int = 0

    def _ratio(self, part: int, whole: int) -> float:
        return round(part / whole * 100, 1) if whole else 100.0

    @property
    def grounding_rate(self) -> float:
        return self._ratio(self.claims_grounded, self.claims_total)

    @property
    def ears_rate(self) -> float:
        return self._ratio(self.criteria_conformant, self.criteria_total)

    @property
    def coverage_rate(self) -> float:
        return self._ratio(self.requirements_covered, self.requirements_total)

    @property
    def decision_rate(self) -> float:
        return self._ratio(self.decisions_answered, self.decisions_total)


class VerificationReport(BaseModel):
    """The gate. `passed` is the only number that matters."""

    model_config = ConfigDict(extra="forbid")

    findings: List[Finding] = Field(default_factory=list)
    coverage: Coverage = Field(default_factory=Coverage)

    @property
    def passed(self) -> bool:
        return not any(finding.blocking for finding in self.findings)

    def by_severity(self, severity: Severity) -> List[Finding]:
        return [f for f in self.findings if f.severity == severity]

    def counts(self) -> Dict[str, int]:
        return {s: len(self.by_severity(s)) for s in ("blocker", "major", "minor")}  # type: ignore[arg-type]

    def blocking_summary(self) -> str:
        """The defect list handed back to the model for repair."""
        return "\n".join(
            f"- [{f.severity.upper()}] {f.location} ({f.code}): {f.message} "
            f"FIX: {f.fix_hint}"
            for f in sorted(
                self.findings, key=lambda f: SEVERITY_ORDER[f.severity]
            )
            if f.severity in ("blocker", "major")
        )


def _normalize(text: str) -> str:
    """Collapse whitespace so a quote survives re-wrapping, but nothing else."""
    return re.sub(r"\s+", " ", text or "").strip().lower()


def verify(spec: SpecDocument, source_document: str = "") -> VerificationReport:
    """Check a spec against its own invariants and against its source."""
    findings: List[Finding] = []
    coverage = Coverage()
    haystack = _normalize(source_document)

    # ---- grounding: every quote must exist in the source --------------------
    coverage.claims_total = len(spec.claims)
    for claim in spec.claims:
        needle = _normalize(claim.quote)
        if not haystack:
            coverage.claims_grounded += 1  # nothing to check against
            continue
        if needle and needle in haystack:
            coverage.claims_grounded += 1
        else:
            findings.append(
                Finding(
                    code="GROUND-MISSING",
                    severity="blocker",
                    location=claim.id,
                    message=(
                        f"Quote does not appear in the source document: "
                        f"{claim.quote[:90]!r}"
                    ),
                    fix_hint=(
                        "Copy the span verbatim from the document, or drop the "
                        "claim and raise it as an open decision instead."
                    ),
                )
            )

    # ---- EARS conformance ---------------------------------------------------
    for requirement in spec.requirements:
        for criterion in requirement.acceptance_criteria:
            coverage.criteria_total += 1
            parsed = criterion.parsed
            if parsed.conforms:
                coverage.criteria_conformant += 1
                continue
            for issue in parsed.issues:
                findings.append(
                    Finding(
                        code=issue.code,
                        severity=(
                            "blocker" if issue.code == "EARS-GRAMMAR" else "major"
                        ),
                        location=f"{requirement.id}/{criterion.id}",
                        message=issue.message,
                        fix_hint=issue.fix_hint,
                    )
                )

    # ---- requirement coverage by tasks --------------------------------------
    coverage.requirements_total = len(spec.requirements)
    uncovered = spec.uncovered_requirements()
    coverage.requirements_covered = coverage.requirements_total - len(uncovered)
    for requirement_id in uncovered:
        findings.append(
            Finding(
                code="COVER-NO-TASK",
                severity="blocker",
                location=requirement_id,
                message="No task delivers this requirement.",
                fix_hint="Add a task whose `satisfies` names this requirement.",
            )
        )

    # ---- evidence that never became a requirement ---------------------------
    for claim_id in spec.unused_claims():
        claim = spec.claim_index()[claim_id]
        findings.append(
            Finding(
                code="TRACE-DROPPED-CLAIM",
                severity="major",
                location=claim_id,
                message=(
                    f"The document states this, but no requirement covers it: "
                    f"{claim.reading[:90]}"
                ),
                fix_hint=(
                    "Cover it with a requirement, or record it in `out_of_scope` "
                    "so the omission is deliberate."
                ),
            )
        )

    # ---- decisions ----------------------------------------------------------
    coverage.decisions_total = len(spec.decisions)
    used_decisions = {
        ref for r in spec.requirements for ref in r.traces_to if ref.startswith("DEC-")
    }
    for decision in spec.decisions:
        if decision.answered:
            coverage.decisions_answered += 1
        else:
            findings.append(
                Finding(
                    code="DEC-ASSUMED",
                    severity="minor",
                    location=decision.id,
                    message=(
                        f"Unanswered — building on the assumed default: "
                        f"{decision.proposed_default[:90]}"
                    ),
                    fix_hint="Answer it, or accept that the default ships.",
                )
            )
        if decision.id not in used_decisions:
            findings.append(
                Finding(
                    code="DEC-UNUSED",
                    severity="minor",
                    location=decision.id,
                    message="Decision resolved but no requirement references it.",
                    fix_hint=(
                        "Trace the requirement it settles, or remove the decision."
                    ),
                )
            )

    # ---- tasks --------------------------------------------------------------
    for task in spec.tasks:
        if not _is_runnable(task.verification):
            findings.append(
                Finding(
                    code="TASK-UNVERIFIABLE",
                    severity="major",
                    location=task.id,
                    message=f"Verification names no command or assertion: "
                    f"{task.verification[:80]!r}",
                    fix_hint=(
                        "Give the command a reviewer would run, e.g. "
                        "`pytest tests/test_quota.py::test_burst -q`."
                    ),
                )
            )
        if task.estimate_hours > 6:
            findings.append(
                Finding(
                    code="TASK-OVERSIZED",
                    severity="minor",
                    location=task.id,
                    message=f"{task.estimate_hours}h is larger than one agent sitting.",
                    fix_hint="Split into two tasks of 6h or less.",
                )
            )

    titles: Dict[str, List[str]] = {}
    for requirement in spec.requirements:
        titles.setdefault(requirement.title.strip().lower(), []).append(requirement.id)
    for title, ids in titles.items():
        if len(ids) > 1:
            findings.append(
                Finding(
                    code="REQ-DUPLICATE",
                    severity="major",
                    location=", ".join(ids),
                    message=f"Two requirements share the title {title!r}.",
                    fix_hint="Merge them, or differentiate the behavior each covers.",
                )
            )

    findings.sort(key=lambda f: (SEVERITY_ORDER[f.severity], f.location))
    return VerificationReport(findings=findings, coverage=coverage)


# --------------------------------------------------------------------------- #
# Pre-compile readiness of the source document
# --------------------------------------------------------------------------- #


class SourceReadiness(BaseModel):
    """A cheap, honest read on the source before any model call is made."""

    model_config = ConfigDict(extra="forbid")

    word_count: int = 0
    line_count: int = 0
    notes: List[str] = Field(default_factory=list)

    @property
    def compilable(self) -> bool:
        return self.word_count >= 25


def assess_source(text: str) -> SourceReadiness:
    """Say what is thin about the document, without scoring it out of 100.

    Deliberately not a grade. Earlier versions of this tool scored every real
    PRD near zero, which told the user nothing they could act on.
    """
    words = text.split()
    lines = [line for line in text.splitlines() if line.strip()]
    notes: List[str] = []

    if len(words) < 25:
        notes.append("Too short to compile — paste the full document.")
    if not re.search(r"\b(as a|user|customer|operator|admin|rep|analyst)\b", text, re.I):
        notes.append("No actor named — requirements will need an assumed role.")
    if not re.search(r"\d", text):
        notes.append("No numbers anywhere — every threshold will be an assumption.")
    if not re.search(
        r"\b(if|when|error|fail|timeout|retry|invalid|reject)\b", text, re.I
    ):
        notes.append("No failure paths described — expect happy-path-only tasks.")
    if not re.search(
        r"\b(test|verify|accept|criteria|done|success)\b", text, re.I
    ):
        notes.append("No acceptance language — verification will be inferred.")

    return SourceReadiness(
        word_count=len(words), line_count=len(lines), notes=notes
    )


def find_quote_line(document: str, quote: str) -> Optional[int]:
    """1-indexed line where a quote starts, or None. Used to repair bad line numbers."""
    needle = _normalize(quote)
    if not needle:
        return None
    for index, line in enumerate(document.splitlines(), start=1):
        if needle in _normalize(line):
            return index
    # The quote may span a wrapped line break; fall back to a prefix probe.
    head = " ".join(needle.split()[:6])
    for index, line in enumerate(document.splitlines(), start=1):
        if head and head in _normalize(line):
            return index
    return None
