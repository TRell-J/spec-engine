"""EARS — Easy Approach to Requirements Syntax.

Six sentence templates from Mavin et al. (IEEE RE'09, Rolls-Royce), the
requirements-engineering standard used in aerospace, automotive and medical
software. EARS constrains free-form English into a fixed clause order and a
closed keyword set, which is what removes ambiguity — you cannot write "the
system should be fast" in EARS, because the grammar has nowhere to put it.

    Ubiquitous       THE <system> SHALL <response>
    Event-driven     WHEN <trigger>, THE <system> SHALL <response>
    State-driven     WHILE <precondition>, THE <system> SHALL <response>
    Optional         WHERE <feature>, THE <system> SHALL <response>
    Unwanted         IF <trigger>, THEN THE <system> SHALL <response>
    Complex          WHILE <precondition>, WHEN <trigger>, THE <system> SHALL <response>

This module is the real anti-slop mechanism: a parser, not a blocklist. A
sentence either parses against one of the six templates or it does not, and the
response clause is then checked for falsifiability.
"""

from __future__ import annotations

import re
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

EarsPattern = Literal[
    "ubiquitous", "event", "state", "optional", "unwanted", "complex"
]

PATTERN_TEMPLATES: dict = {
    "ubiquitous": "THE <system> SHALL <response>",
    "event": "WHEN <trigger>, THE <system> SHALL <response>",
    "state": "WHILE <precondition>, THE <system> SHALL <response>",
    "optional": "WHERE <feature>, THE <system> SHALL <response>",
    "unwanted": "IF <trigger>, THEN THE <system> SHALL <response>",
    "complex": "WHILE <precondition>, WHEN <trigger>, THE <system> SHALL <response>",
}

# Order matters: complex must be tried before state and event.
_GRAMMAR = [
    (
        "complex",
        re.compile(
            r"^\s*WHILE\s+(?P<precondition>.+?)\s*,\s*WHEN\s+(?P<trigger>.+?)\s*,\s*"
            r"THE\s+(?P<system>.+?)\s+SHALL\s+(?P<response>.+?)\s*\.?\s*$",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "unwanted",
        re.compile(
            r"^\s*IF\s+(?P<trigger>.+?)\s*,\s*THEN\s+THE\s+(?P<system>.+?)\s+SHALL\s+"
            r"(?P<response>.+?)\s*\.?\s*$",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "state",
        re.compile(
            r"^\s*WHILE\s+(?P<precondition>.+?)\s*,\s*THE\s+(?P<system>.+?)\s+SHALL\s+"
            r"(?P<response>.+?)\s*\.?\s*$",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "event",
        re.compile(
            r"^\s*WHEN\s+(?P<trigger>.+?)\s*,\s*THE\s+(?P<system>.+?)\s+SHALL\s+"
            r"(?P<response>.+?)\s*\.?\s*$",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "optional",
        re.compile(
            r"^\s*WHERE\s+(?P<feature>.+?)\s*,\s*THE\s+(?P<system>.+?)\s+SHALL\s+"
            r"(?P<response>.+?)\s*\.?\s*$",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "ubiquitous",
        re.compile(
            r"^\s*THE\s+(?P<system>.+?)\s+SHALL\s+(?P<response>.+?)\s*\.?\s*$",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
]

# Adjectives that describe a feeling rather than a behavior. In prose these are
# merely weak; inside an EARS response clause they are a defect, because the
# clause exists precisely to state what the system does.
UNFALSIFIABLE_TERMS = [
    "fast", "quick", "quickly", "slow", "responsive", "snappy",
    "scalable", "performant", "efficient", "lightweight", "optimized",
    "secure", "safe", "robust", "reliable", "stable", "resilient",
    "clean", "modern", "intuitive", "seamless", "elegant", "polished",
    "easy", "simple", "user-friendly", "friendly", "delightful",
    "accurate", "high-quality", "good", "better", "best", "appropriate",
    "reasonable", "sufficient", "adequate", "acceptable", "proper",
    "etc", "and so on", "as needed", "if possible", "where appropriate",
    "some", "several", "many", "various", "a few", "tbd", "tbc",
]

_UNFALSIFIABLE_RE = re.compile(
    r"\b(" + "|".join(re.escape(term) for term in UNFALSIFIABLE_TERMS) + r")\b",
    re.IGNORECASE,
)

# A response is measurable if it names a magnitude, a status code, an exact
# artifact, or an enumerated value.
_MEASURABLE_RE = re.compile(
    r"(\d|\b(?:HTTP|status|code|exactly|zero|no more than|at least|within)\b|`[^`]+`)",
    re.IGNORECASE,
)

_HEDGE_RE = re.compile(
    r"\b(should|may|might|could|would|can|try to|attempt to|ideally|"
    r"where possible|as appropriate)\b",
    re.IGNORECASE,
)


class EarsIssue(BaseModel):
    """One defect found in a candidate EARS statement."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    fix_hint: str


class EarsParse(BaseModel):
    """The result of parsing one candidate statement."""

    model_config = ConfigDict(extra="forbid")

    text: str
    pattern: Optional[EarsPattern] = None
    system: Optional[str] = None
    trigger: Optional[str] = None
    precondition: Optional[str] = None
    feature: Optional[str] = None
    response: Optional[str] = None
    issues: List[EarsIssue] = Field(default_factory=list)

    @property
    def conforms(self) -> bool:
        """True when the sentence parses and carries no blocking issue."""
        return self.pattern is not None and not self.issues

    @property
    def parses(self) -> bool:
        return self.pattern is not None


def parse(text: str) -> EarsParse:
    """Parse one statement against the EARS grammar and check its response clause."""
    raw = (text or "").strip()
    if not raw:
        return EarsParse(
            text=raw,
            issues=[
                EarsIssue(
                    code="EARS-EMPTY",
                    message="Empty acceptance criterion.",
                    fix_hint="Write one EARS sentence.",
                )
            ],
        )

    for pattern_name, regex in _GRAMMAR:
        match = regex.match(raw)
        if not match:
            continue
        groups = match.groupdict()
        parsed = EarsParse(
            text=raw,
            pattern=pattern_name,  # type: ignore[arg-type]
            system=groups.get("system"),
            trigger=groups.get("trigger"),
            precondition=groups.get("precondition"),
            feature=groups.get("feature"),
            response=groups.get("response"),
        )
        parsed.issues = _check_response(parsed)
        return parsed

    return EarsParse(
        text=raw,
        issues=[
            EarsIssue(
                code="EARS-GRAMMAR",
                message="Statement does not match any EARS template.",
                fix_hint=(
                    "Rewrite as one of: "
                    + "; ".join(PATTERN_TEMPLATES.values())
                ),
            )
        ],
    )


def _check_response(parsed: EarsParse) -> List[EarsIssue]:
    """A parsed sentence can still be unfalsifiable. Check the response clause."""
    issues: List[EarsIssue] = []
    response = parsed.response or ""

    vague = sorted({m.group(0).lower() for m in _UNFALSIFIABLE_RE.finditer(response)})
    if vague:
        issues.append(
            EarsIssue(
                code="EARS-UNFALSIFIABLE",
                message=(
                    "Response clause is unfalsifiable: "
                    + ", ".join(f"'{term}'" for term in vague)
                ),
                fix_hint=(
                    "State the observable behavior instead — a threshold, a status "
                    "code, an exact message, or a count."
                ),
            )
        )

    hedges = sorted({m.group(0).lower() for m in _HEDGE_RE.finditer(response)})
    if hedges:
        issues.append(
            EarsIssue(
                code="EARS-HEDGE",
                message=f"Response clause hedges: {', '.join(hedges)}",
                fix_hint="SHALL already carries the obligation. Remove the hedge.",
            )
        )

    if not _MEASURABLE_RE.search(response):
        issues.append(
            EarsIssue(
                code="EARS-UNMEASURED",
                message="Response clause names no measurable outcome.",
                fix_hint=(
                    "Add the value a test would assert: a number, a status code, "
                    "an exact string, or a named artifact."
                ),
            )
        )

    if len(response.split()) > 45:
        issues.append(
            EarsIssue(
                code="EARS-COMPOUND",
                message="Response clause is long enough to hide a second requirement.",
                fix_hint="Split into two requirements, one behavior each.",
            )
        )

    return issues


def normalize(text: str) -> str:
    """Rebuild a parsed statement with canonical keyword casing.

    Reconstructed from the parse tree rather than string-substituted, so the
    word "the" inside a response clause is left alone.
    """
    parsed = parse(text)
    if not parsed.parses:
        return text.strip()

    tail = f"the {parsed.system} SHALL {parsed.response}"
    if parsed.pattern == "ubiquitous":
        return tail
    if parsed.pattern == "event":
        return f"WHEN {parsed.trigger}, {tail}"
    if parsed.pattern == "state":
        return f"WHILE {parsed.precondition}, {tail}"
    if parsed.pattern == "optional":
        return f"WHERE {parsed.feature}, {tail}"
    if parsed.pattern == "unwanted":
        return f"IF {parsed.trigger}, THEN {tail}"
    return f"WHILE {parsed.precondition}, WHEN {parsed.trigger}, {tail}"


def grammar_reference() -> str:
    """The six templates, for prompts and for the UI."""
    return "\n".join(
        f"- {name}: {template}" for name, template in PATTERN_TEMPLATES.items()
    )
