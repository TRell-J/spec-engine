"""The compiler: four passes, each with a gate.

    1. EXTRACT     document  -> claims        (every claim quotes the source)
    2. INTERROGATE claims    -> decisions     (what the document never says)
    3. SPECIFY     evidence  -> requirements  (EARS, traced to evidence)
    4. DECOMPOSE   requirements -> tasks      (agent-executable, traced to requirements)
    5. VERIFY      deterministic checks -> repair turn -> re-verify

The passes are separated on purpose. A single call that reads prose and emits a
task list has nowhere to put the two things that matter — the evidence each
requirement rests on, and the decisions nobody made. Splitting them makes both
inspectable, and lets a human answer the decisions before any requirement is
written, which is the approval gate every spec-driven workflow converges on.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from hashlib import sha1
from typing import Any, Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

from . import ears, providers
from .providers import Provider, ProviderError, Reply, Settings
from .schemas import (
    OpenDecision,
    Requirement,
    SourceClaim,
    SpecDocument,
    TASK_LAYERS,
    Task,
)
from .verifier import Finding, VerificationReport, find_quote_line, verify

DEFAULT_MODEL = providers.BY_KEY[providers.DEFAULT_PROVIDER].default_model
DEFAULT_MAX_TOKENS = providers.DEFAULT_MAX_TOKENS
MAX_REPAIR_ATTEMPTS = 2

T = TypeVar("T", bound=BaseModel)


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
#
# Which model reads the document is a setting, not an assumption. `providers`
# owns the catalogue and the wire protocols; everything below is the compiler,
# and it only ever sees a `Provider`.


def resolve_settings(overrides: Optional[Dict[str, Any]] = None) -> Settings:
    return providers.resolve_settings(overrides)


def resolve_model() -> str:
    return resolve_settings().model


def resolve_max_tokens() -> int:
    return resolve_settings().max_tokens


def has_credentials(overrides: Optional[Dict[str, Any]] = None) -> bool:
    """Is there enough configuration to attempt a compile?

    Not the same question as "is there an API key": a model running on your own
    machine needs no key, and a hosted one is useless without one.
    """
    return resolve_settings(overrides).configured


def build_client(
    api_key: Optional[str] = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> Optional[Provider]:
    """Return a provider for the current settings, or None if unusable."""
    merged = dict(overrides or {})
    if api_key:
        merged["api_key"] = api_key
    try:
        return providers.build(resolve_settings(merged))
    except ProviderError:
        return None


class PipelineError(RuntimeError):
    """Raised when a pass cannot produce a valid result."""


# --------------------------------------------------------------------------- #
# Structured-output plumbing
# --------------------------------------------------------------------------- #


def _strictify(node: Any) -> Any:
    """Pydantic JSON Schema -> the strict shape structured outputs requires."""
    if isinstance(node, list):
        return [_strictify(item) for item in node]
    if not isinstance(node, dict):
        return node
    out: Dict[str, Any] = {}
    for key, value in node.items():
        if key in {"default", "format"}:
            continue
        out[key] = _strictify(value)
    if out.get("type") == "object" or "properties" in out:
        out["additionalProperties"] = False
        out["required"] = sorted(out.get("properties", {}).keys())
    return out


def json_schema_for(model: Type[BaseModel]) -> Dict[str, Any]:
    return _strictify(model.model_json_schema())


def _loads(text: str) -> Dict[str, Any]:
    """Parse the JSON out of a response, tolerantly.

    Open-weight reasoning models emit a `<think>` block before the answer, and
    plenty of models fence their JSON regardless of instructions. Neither is a
    schema failure, so neither should cost a repair round.
    """
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])


def _format_errors(exc: ValidationError) -> str:
    return "\n".join(
        f"- {'.'.join(str(p) for p in err.get('loc', ())) or '<root>'}: {err.get('msg')}"
        for err in exc.errors()[:15]
    )


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    calls: int = 0

    def add(self, reply: Reply) -> None:
        self.calls += 1
        self.input_tokens += reply.input_tokens
        self.output_tokens += reply.output_tokens
        self.cache_read_input_tokens += reply.cache_read_input_tokens

    def merge(self, other: "Usage") -> None:
        """Fold another tally in, so a run's cost covers every pass it made."""
        self.calls += other.calls
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_input_tokens += other.cache_read_input_tokens


def call_structured(
    client: Any,
    system: str,
    user: str,
    model_cls: Type[T],
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
    usage: Optional[Usage] = None,
    repair_attempts: int = MAX_REPAIR_ATTEMPTS,
) -> T:
    """One pass: pin the model to a schema, validate locally, repair in-conversation."""
    provider = providers.adapt(client)
    model = model or provider.settings.model or resolve_model()
    max_tokens = max_tokens or provider.settings.max_tokens
    schema = json_schema_for(model_cls)
    messages: List[Dict[str, Any]] = [{"role": "user", "content": user}]
    last_error = "no attempt made"

    for _ in range(repair_attempts + 1):
        reply = provider.complete(
            system=system,
            messages=messages,
            schema=schema,
            model=model,
            max_tokens=max_tokens,
        )
        if usage is not None:
            usage.add(reply)
        raw = reply.text

        try:
            return model_cls.model_validate(_loads(raw))
        except json.JSONDecodeError as exc:
            last_error = f"output was not JSON: {exc}"
        except ValidationError as exc:
            last_error = _format_errors(exc)

        messages.append({"role": "assistant", "content": raw})
        messages.append(
            {
                "role": "user",
                "content": (
                    f"That response failed validation:\n{last_error}\n\n"
                    "Return the corrected object. Keep every grounded detail you "
                    "already produced and fix only what is listed."
                ),
            }
        )

    raise PipelineError(f"schema adherence failed. Last error:\n{last_error}")


# --------------------------------------------------------------------------- #
# Pass envelopes
# --------------------------------------------------------------------------- #


class ExtractionResult(BaseModel):
    document_title: str
    claims: List[SourceClaim]


class InterrogationResult(BaseModel):
    decisions: List[OpenDecision]


class SpecificationResult(BaseModel):
    requirements: List[Requirement]


class PlanResult(BaseModel):
    architecture_notes: str
    tasks: List[Task]
    out_of_scope: List[str]
    risks: List[str]


class RepairResult(BaseModel):
    requirements: List[Requirement]
    tasks: List[Task]


# --------------------------------------------------------------------------- #
# Prompts
# --------------------------------------------------------------------------- #

_GROUNDING_RULE = """
GROUNDING IS THE JOB. You are reading someone else's document. You may not add
facts to it. Every claim you record carries `quote`, which must be a span copied
character-for-character from the document — it is checked against the source
after you answer, and a quote that is not found fails the build. If something is
needed but not stated, it is not a claim; it is a decision for the next pass.
""".strip()

EXTRACT_SYSTEM = f"""You are the extraction pass of a specification compiler.

Read the source document and record what it actually says — nothing more.

{_GROUNDING_RULE}

For each claim:
- `quote`: verbatim span from the document. Keep it short (one sentence or clause).
- `line`: the 1-indexed line the quote starts on.
- `kind`: requirement (the system must do this) | constraint (a limit the build
  must respect: platform, compliance, budget, deadline) | assumption (stated as
  fact but unverified) | context (background that shapes design but commits
  nothing).
- `reading`: what this commits the build to, in your own words, one sentence.

Record every distinct commitment. Do not merge two behaviors into one claim, and
do not record the same commitment twice. Prose with no commitment in it
(greetings, narrative colour) is not a claim."""

INTERROGATE_SYSTEM = f"""You are the interrogation pass of a specification compiler.

You have the source document and the claims extracted from it. Your job is to
find what the document does NOT say and cannot be built without — the decisions
someone has to make before an agent writes code.

{_GROUNDING_RULE}

A good decision is one where a competent engineer would otherwise guess, and
where guessing wrong is expensive: data ownership and system of record, identity
and permission model, failure and retry behavior, idempotency, limits and
thresholds, migration and backfill of existing data, what happens to
in-flight work, who is allowed to approve an irreversible action.

For each decision:
- `question`: one specific question with a decidable answer. Not "what about
  security" — "which role may approve a discount above the auto-approve limit?"
- `why_it_blocks`: what an agent would be forced to invent without it.
- `options`: 2-4 realistic answers, if the space is closed.
- `proposed_default`: the answer you would ship if nobody replies. Concrete,
  numeric where a number applies, and defensible from the document.

Return at most 10, ordered by how expensive a wrong guess would be. Do not ask
about anything the claims already settle."""

SPECIFY_SYSTEM = f"""You are the specification pass of a specification compiler.

Convert evidence into requirements written in EARS notation (Easy Approach to
Requirements Syntax, Mavin et al.), the requirements grammar used in
safety-critical software. Acceptance criteria MUST match one of these six
templates exactly:

{ears.grammar_reference()}

Rules that are checked mechanically after you answer:
1. Every acceptance criterion parses against one of the six templates. A
   sentence that does not start with THE / WHEN / WHILE / WHERE / IF is rejected.
2. The response clause after SHALL states an observable outcome with a value a
   test can assert: a threshold with units, an HTTP status, an exact message, a
   count, or a named artifact. Words like fast, secure, robust, clean, intuitive,
   reliable, appropriate, several, etc. are rejected — they describe a feeling,
   not a behavior.
3. No hedging inside the response clause. SHALL already carries the obligation,
   so "SHALL try to" and "SHALL ideally" are rejected.
4. `traces_to` names the CLM-/DEC- ids this requirement rests on. A requirement
   that traces to nothing is an invention and is rejected.
5. One behavior per criterion. If you need "and" between two testable outcomes,
   write two criteria.

{_GROUNDING_RULE}

Write requirements for the unwanted behavior too — timeouts, invalid input,
permission denial, partial failure. Those are the paths agents skip.

Number requirements REQ-001 upward. Acceptance criteria are AC-1, AC-2, ...
within their requirement. Cover every requirement-kind and constraint-kind claim;
if you deliberately leave one out, do not silently drop it — the verifier will
catch it."""

DECOMPOSE_SYSTEM = """You are the planning pass of a specification compiler.

Turn requirements into tasks an autonomous coding agent can execute one at a
time, in dependency order.

Rules:
1. Every task names the requirements it satisfies in `satisfies`. A task that
   satisfies nothing is scope creep and is rejected.
2. Every requirement must be delivered by at least one task. The verifier checks
   this; an uncovered requirement fails the build.
3. One task, one layer, one sitting: 1-6 hours. If it spans two layers, split it.
4. `title` is imperative and specific: "Add approval_rules table with tenant
   index", not "Database work".
5. `verification` is the command a reviewer runs, or an explicit assertion:
   `pytest tests/test_approval.py::test_over_limit_routes_to_legal -q`, or
   `POST /v1/quotes with discount=0.45 returns 403 and error code
   approval.required`.
6. `depends_on` is real ordering only — schema before the code that reads it.
   Do not serialize independent work; anything left unlinked runs in parallel.
7. Number tasks TASK-001 upward.

`architecture_notes` states the shape of the system in a short paragraph:
storage, boundaries, and where the risky part is. Name technologies only where
the requirements or constraints imply them.

`out_of_scope` records what you deliberately did not plan, so the omission is
visible. `risks` records what could still go wrong at build time."""


# --------------------------------------------------------------------------- #
# Passes
# --------------------------------------------------------------------------- #


def _numbered(document: str) -> str:
    return "\n".join(
        f"{index:>4} | {line}"
        for index, line in enumerate(document.splitlines(), start=1)
    )


def extract_claims(
    client: Any, document: str, usage: Optional[Usage] = None, **kwargs
) -> ExtractionResult:
    """Pass 1. Claims with verbatim quotes; line numbers repaired locally."""
    result = call_structured(
        client,
        EXTRACT_SYSTEM,
        "Extract the claims from this document.\n\n"
        f"<document>\n{_numbered(document)}\n</document>",
        ExtractionResult,
        usage=usage,
        **kwargs,
    )
    for claim in result.claims:
        found = find_quote_line(document, claim.quote)
        if found:
            claim.line = found
    return result


def interrogate(
    client: Any,
    document: str,
    claims: List[SourceClaim],
    usage: Optional[Usage] = None,
    **kwargs,
) -> InterrogationResult:
    """Pass 2. The decisions the document never made."""
    rendered = "\n".join(
        f"- {claim.id} [{claim.kind}] {claim.reading}" for claim in claims
    )
    result = call_structured(
        client,
        INTERROGATE_SYSTEM,
        f"<document>\n{document}\n</document>\n\n"
        f"<claims_already_extracted>\n{rendered}\n</claims_already_extracted>\n\n"
        "What must be decided before this can be built?",
        InterrogationResult,
        usage=usage,
        **kwargs,
    )
    # `answer` is a required field in the schema, so the model is free to fill
    # it in — which would silently tick questions the user never saw. Only a
    # human answers a question.
    for decision in result.decisions:
        decision.answer = None
    return result


def _evidence_block(claims: List[SourceClaim], decisions: List[OpenDecision]) -> str:
    claim_rows = "\n".join(
        f"- {c.id} [{c.kind}] quote: {c.quote!r} -> {c.reading}" for c in claims
    )
    decision_rows = "\n".join(
        f"- {d.id} {d.question} -> RESOLVED AS: {d.resolution}"
        f"{'' if d.answered else '  (assumed default, nobody answered)'}"
        for d in decisions
    )
    return (
        f"<claims>\n{claim_rows}\n</claims>\n\n"
        f"<decisions>\n{decision_rows or 'none'}\n</decisions>"
    )


def specify(
    client: Any,
    document: str,
    claims: List[SourceClaim],
    decisions: List[OpenDecision],
    usage: Optional[Usage] = None,
    **kwargs,
) -> SpecificationResult:
    """Pass 3. EARS requirements traced to evidence."""
    return call_structured(
        client,
        SPECIFY_SYSTEM,
        f"<document>\n{document}\n</document>\n\n"
        f"{_evidence_block(claims, decisions)}\n\n"
        "Write the requirements.",
        SpecificationResult,
        usage=usage,
        **kwargs,
    )


def decompose(
    client: Any,
    requirements: List[Requirement],
    architecture_hint: str = "",
    usage: Optional[Usage] = None,
    **kwargs,
) -> PlanResult:
    """Pass 4. Tasks traced to requirements."""
    rendered = "\n".join(
        f"- {r.id} [{r.priority}] {r.title}\n"
        + "\n".join(f"    {c.id}: {c.statement}" for c in r.acceptance_criteria)
        for r in requirements
    )
    hint = f"\n\nContext from the source:\n{architecture_hint}" if architecture_hint else ""
    return call_structured(
        client,
        DECOMPOSE_SYSTEM,
        f"<requirements>\n{rendered}\n</requirements>"
        f"{hint}\n\nAvailable layers: {', '.join(TASK_LAYERS)}.\n\n"
        "Produce the implementation plan.",
        PlanResult,
        usage=usage,
        **kwargs,
    )


def repair(
    client: Any,
    spec: SpecDocument,
    report: VerificationReport,
    usage: Optional[Usage] = None,
    **kwargs,
) -> RepairResult:
    """Pass 5. Hand the defect list back and take the corrected artifacts."""
    payload = {
        "requirements": [r.model_dump() for r in spec.requirements],
        "tasks": [t.model_dump() for t in spec.tasks],
    }
    return call_structured(
        client,
        SPECIFY_SYSTEM
        + "\n\nYou are now repairing a spec that failed verification. Return the "
        "complete corrected `requirements` and `tasks`. Change only what the "
        "defect list names; preserve every id and every grounded detail that was "
        "not flagged.\n\n"
        + DECOMPOSE_SYSTEM,
        f"<current_spec>\n{json.dumps(payload, indent=2)}\n</current_spec>\n\n"
        f"<defects>\n{report.blocking_summary()}\n</defects>\n\n"
        "Return the corrected requirements and tasks.",
        RepairResult,
        usage=usage,
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def repair_finding(
    client: Any,
    spec: SpecDocument,
    finding: Finding,
    document: str = "",
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
    usage: Optional[Usage] = None,
) -> "SpecDocument":
    """Fix one named defect and return the corrected spec.

    The repair machinery already existed for the internal loop; this exposes it
    for a single finding so the user can act on one defect without paying to
    recompile everything. Raises if the correction does not validate or does
    not actually clear the finding.
    """
    report = VerificationReport(findings=[finding])
    repaired = repair(
        client, spec, report, usage=usage, model=model, max_tokens=max_tokens
    )
    candidate = spec.model_copy(
        update={"requirements": repaired.requirements, "tasks": repaired.tasks}
    )
    SpecDocument.model_validate(candidate.model_dump())  # raises on a bad fix

    after = verify(candidate, document)
    still_there = [
        f
        for f in after.findings
        if f.code == finding.code and f.location == finding.location
    ]
    if still_there:
        raise PipelineError(
            f"the model returned a spec that still has {finding.code} on "
            f"{finding.location}"
        )
    return candidate


@dataclass
class CompileResult:
    """Everything the UI needs about one compile."""

    spec: Optional[SpecDocument]
    report: Optional[VerificationReport]
    model: str
    # Where the run happened, so its cost can still be reported honestly after
    # a refresh — or after the user switches provider on the next document.
    base_url: str = ""
    usage: Usage = field(default_factory=Usage)
    repair_rounds: int = 0
    error: Optional[str] = None
    stage_reached: str = "extract"

    @property
    def ok(self) -> bool:
        return self.spec is not None


def _spec_id(name: str) -> str:
    # sha1 is order-dependent, so anagram titles ("Billing Portal" vs "Portal
    # Billing") no longer land in the same bucket the way character sums do.
    digest = int(sha1(name.encode("utf-8")).hexdigest(), 16) % 900 + 100
    return f"SPEC-{date.today().year}-{digest:03d}"


def assemble(
    name: str,
    summary: str,
    plan: PlanResult,
    claims: List[SourceClaim],
    decisions: List[OpenDecision],
    requirements: List[Requirement],
) -> SpecDocument:
    return SpecDocument(
        name=name,
        spec_id=_spec_id(name),
        summary=summary,
        architecture_notes=plan.architecture_notes,
        claims=claims,
        decisions=decisions,
        requirements=requirements,
        tasks=plan.tasks,
        out_of_scope=plan.out_of_scope,
        risks=plan.risks,
    )


def compile_spec(
    client: Any,
    document: str,
    claims: List[SourceClaim],
    decisions: List[OpenDecision],
    title: str = "Untitled Initiative",
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
    requirements: Optional[List[Requirement]] = None,
) -> CompileResult:
    """Passes 3-5: specify, decompose, verify, repair, re-verify.

    Pass `requirements` to skip the specify pass and re-plan against
    requirements that are already good — a re-run of the part that was wrong
    costs one call instead of four.
    """
    # The provider's own model, not the environment's: a client pointed at
    # OpenAI must not be handed a Claude model id, and the id recorded on the
    # result is what the cost figure is priced against.
    provider = providers.adapt(client)
    model = model or provider.settings.model or resolve_model()
    base_url = provider.settings.base_url
    usage = Usage()
    call_kwargs = {"model": model, "max_tokens": max_tokens}

    try:
        if requirements is not None:
            specification = SpecificationResult(requirements=requirements)
        else:
            specification = specify(
                client, document, claims, decisions, usage=usage, **call_kwargs
            )
    except Exception as exc:
        return CompileResult(
            spec=None, report=None, model=model, base_url=base_url, usage=usage,
            error=f"specify: {type(exc).__name__}: {exc}", stage_reached="specify",
        )

    try:
        plan = decompose(
            client,
            specification.requirements,
            architecture_hint="\n".join(
                c.reading for c in claims if c.kind in ("constraint", "context")
            )[:2000],
            usage=usage,
            **call_kwargs,
        )
    except Exception as exc:
        return CompileResult(
            spec=None, report=None, model=model, base_url=base_url, usage=usage,
            error=f"decompose: {type(exc).__name__}: {exc}", stage_reached="decompose",
        )

    summary = (
        f"{len(specification.requirements)} requirements and {len(plan.tasks)} tasks "
        f"compiled from a {len(document.split())}-word source document, traced to "
        f"{len(claims)} quoted claims and {len(decisions)} resolved decisions."
    )

    try:
        spec = assemble(
            title, summary, plan, claims, decisions, specification.requirements
        )
    except ValidationError as exc:
        return CompileResult(
            spec=None, report=None, model=model, base_url=base_url, usage=usage,
            error=f"assembly: {_format_errors(exc)}", stage_reached="assemble",
        )

    report = verify(spec, document)
    rounds = 0
    while not report.passed and rounds < MAX_REPAIR_ATTEMPTS:
        rounds += 1
        try:
            repaired = repair(client, spec, report, usage=usage, **call_kwargs)
            candidate = spec.model_copy(
                update={
                    "requirements": repaired.requirements,
                    "tasks": repaired.tasks,
                }
            )
            SpecDocument.model_validate(candidate.model_dump())
            spec = candidate
            report = verify(spec, document)
        except Exception:
            break  # keep the last valid spec and report the defects honestly

    return CompileResult(
        spec=spec,
        report=report,
        model=model,
        base_url=base_url,
        usage=usage,
        repair_rounds=rounds,
        stage_reached="verify",
    )
