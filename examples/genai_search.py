"""Reference example: an evaluation harness for a GenAI search assistant.

Hand-authored, like every example here — not generated output. Each quote is
copied from the document below, so this spec passes the same grounding check a
live compile faces.

Chosen because it has a different shape to the deal-desk example: the hard part
is measurement rather than workflow, and two of its constraints (PII, VPC) come
from the customer's environment rather than from the feature itself.
"""

from __future__ import annotations

from core.schemas import (
    AcceptanceCriterion,
    OpenDecision,
    Requirement,
    SourceClaim,
    SpecDocument,
    Task,
)

DOCUMENT = """# Discovery notes: GenAI search quality

Customer is a 900-person B2B software company. They shipped an LLM search
assistant over their internal knowledge base four months ago and adoption is flat.

Their words: "It works great in demos but users say the answers are wrong. We have
no idea if a change makes it better or worse."

Today an engineer spot-checks about twenty queries in a notebook before shipping.
There is no regression suite and no labelled set.

They want to compare retrieval strategies and prompt versions against a fixed set
of questions, and be told when a release makes answers worse.

Their support transcripts contain customer names and email addresses, and nothing
may leave their VPC. They run Postgres with pgvector today.

A product manager, not an ML engineer, should be able to read the results.
"""


def _claims() -> list:
    return [
        SourceClaim(
            id="CLM-001",
            quote=(
                "Today an engineer spot-checks about twenty queries in a notebook "
                "before shipping."
            ),
            line=9,
            kind="context",
            reading="Release confidence today rests on an ad-hoc manual spot check.",
        ),
        SourceClaim(
            id="CLM-002",
            quote="There is no regression suite and no labelled set.",
            line=10,
            kind="context",
            reading="Nothing exists to compare one release against another.",
        ),
        SourceClaim(
            id="CLM-003",
            quote=(
                "They want to compare retrieval strategies and prompt versions "
                "against a fixed set of questions"
            ),
            line=12,
            kind="requirement",
            reading=(
                "Two configurations must be scorable against identical questions."
            ),
        ),
        SourceClaim(
            id="CLM-004",
            quote="be told when a release makes answers worse",
            line=13,
            kind="requirement",
            reading="A regression must raise an alarm rather than ship quietly.",
        ),
        SourceClaim(
            id="CLM-005",
            quote=(
                "Their support transcripts contain customer names and email addresses"
            ),
            line=15,
            kind="constraint",
            reading="The corpus carries PII that must not reach an embedding model.",
        ),
        SourceClaim(
            id="CLM-006",
            quote="nothing may leave their VPC",
            line=15,
            kind="constraint",
            reading="No component may call a third-party endpoint.",
        ),
        SourceClaim(
            id="CLM-007",
            quote="They run Postgres with pgvector today.",
            line=16,
            kind="constraint",
            reading="Storage must reuse the existing PostgreSQL and pgvector stack.",
        ),
        SourceClaim(
            id="CLM-008",
            quote=(
                "A product manager, not an ML engineer, should be able to read the "
                "results."
            ),
            line=18,
            kind="requirement",
            reading="The results view must be legible without ML vocabulary.",
        ),
    ]


def _decisions() -> list:
    return [
        OpenDecision(
            id="DEC-001",
            question="What makes an answer correct?",
            why_it_blocks=(
                "The document says answers are wrong but never defines right, so an "
                "agent would invent a scoring rule and every number after it would "
                "be meaningless."
            ),
            options=[
                "Exact string match against a reference answer",
                "The answer cites the labelled source document",
                "An LLM judge scores against a rubric",
            ],
            proposed_default=(
                "An answer is correct when it cites the source document a human "
                "labelled as authoritative; each question scores 1 or 0."
            ),
            answer=(
                "An answer is correct when it cites the source document a human "
                "labelled as authoritative; each question scores 1 or 0."
            ),
        ),
        OpenDecision(
            id="DEC-002",
            question="How many labelled questions gate a release?",
            why_it_blocks=(
                "Too few and the score is noise; too many and nobody maintains the "
                "set. An agent would pick a number arbitrarily."
            ),
            options=["50 questions", "150 questions", "500 questions"],
            proposed_default=(
                "150 labelled questions, reviewed quarterly by the support lead."
            ),
            answer="150 labelled questions, reviewed quarterly by the support lead.",
        ),
        OpenDecision(
            id="DEC-003",
            question="Where does the scoring judge run, given nothing leaves the VPC?",
            why_it_blocks=(
                "The obvious implementation calls a hosted model, which breaks the "
                "stated constraint on the first run."
            ),
            options=[
                "Self-hosted open-weights judge inside the VPC",
                "Hosted API with a signed data agreement",
                "Deterministic citation matching, no judge model",
            ],
            proposed_default=(
                "A self-hosted open-weights judge runs inside the VPC; no scoring "
                "traffic leaves the network."
            ),
            answer=(
                "A self-hosted open-weights judge runs inside the VPC; no scoring "
                "traffic leaves the network."
            ),
        ),
    ]


def _requirements() -> list:
    return [
        Requirement(
            id="REQ-001",
            title="Fixed labelled question set",
            user_story=(
                "As an ML engineer, I want a fixed set of labelled questions, so "
                "that two releases can be compared on the same ground."
            ),
            traces_to=["CLM-003", "DEC-002"],
            priority="must",
            acceptance_criteria=[
                AcceptanceCriterion(
                    id="AC-1",
                    statement=(
                        "THE evaluation service SHALL store 150 labelled questions, "
                        "each naming the source document a correct answer must cite"
                    ),
                ),
                AcceptanceCriterion(
                    id="AC-2",
                    statement=(
                        "IF a question is submitted without a labelled source "
                        "document, THEN the evaluation service SHALL reject it with "
                        "HTTP 422 and the error code `question.unlabelled`"
                    ),
                ),
            ],
        ),
        Requirement(
            id="REQ-002",
            title="Scored comparison run",
            user_story=(
                "As an ML engineer, I want every configuration scored against the "
                "same questions, so that I can tell whether a change helped."
            ),
            traces_to=["CLM-003", "DEC-001"],
            priority="must",
            acceptance_criteria=[
                AcceptanceCriterion(
                    id="AC-1",
                    statement=(
                        "WHEN an engineer starts a run, the evaluation service SHALL "
                        "score all 150 questions and write 1 result row per question"
                    ),
                ),
                AcceptanceCriterion(
                    id="AC-2",
                    statement=(
                        "WHEN an answer cites the labelled source document, the "
                        "evaluation service SHALL mark that question `correct`"
                    ),
                ),
                AcceptanceCriterion(
                    id="AC-3",
                    statement=(
                        "WHEN a run finishes, the evaluation service SHALL publish "
                        "the composite score as a percentage to 1 decimal place"
                    ),
                ),
            ],
        ),
        Requirement(
            id="REQ-003",
            title="Regression blocks the release",
            user_story=(
                "As a release manager, I want a worse release stopped "
                "automatically, so that quality cannot slip out unnoticed."
            ),
            traces_to=["CLM-004"],
            priority="must",
            acceptance_criteria=[
                AcceptanceCriterion(
                    id="AC-1",
                    statement=(
                        "IF a run scores more than 2 percentage points below the "
                        "stored baseline, THEN the evaluation service SHALL fail the "
                        "release check with exit code 1"
                    ),
                ),
                AcceptanceCriterion(
                    id="AC-2",
                    statement=(
                        "WHEN a run scores below the baseline, the evaluation "
                        "service SHALL list every question that moved from `correct` "
                        "to `incorrect`"
                    ),
                ),
            ],
        ),
        Requirement(
            id="REQ-004",
            title="Results legible without ML vocabulary",
            user_story=(
                "As a product manager, I want to read a run without ML training, so "
                "that I can judge a release myself."
            ),
            traces_to=["CLM-008"],
            priority="should",
            acceptance_criteria=[
                AcceptanceCriterion(
                    id="AC-1",
                    statement=(
                        "WHEN a product manager opens a run, the evaluation service "
                        "SHALL show the composite score and the 10 largest "
                        "regressions with the question text for each"
                    ),
                ),
                AcceptanceCriterion(
                    id="AC-2",
                    statement=(
                        "THE evaluation service SHALL present every score as a "
                        "percentage between 0 and 100 in the summary view"
                    ),
                ),
            ],
        ),
        Requirement(
            id="REQ-005",
            title="No data and no scoring traffic leaves the VPC",
            user_story=(
                "As a security owner, I want every component inside the VPC, so "
                "that customer transcripts never reach a third party."
            ),
            traces_to=["CLM-005", "CLM-006", "DEC-003"],
            priority="must",
            acceptance_criteria=[
                AcceptanceCriterion(
                    id="AC-1",
                    statement=(
                        "THE evaluation service SHALL run the judge model inside the "
                        "customer VPC and make 0 outbound requests to third-party "
                        "endpoints"
                    ),
                ),
                AcceptanceCriterion(
                    id="AC-2",
                    statement=(
                        "IF an outbound request to a non-VPC host is attempted, THEN "
                        "the evaluation service SHALL block it and write 1 audit "
                        "record naming the destination"
                    ),
                ),
                AcceptanceCriterion(
                    id="AC-3",
                    statement=(
                        "WHEN a transcript is indexed, the evaluation service SHALL "
                        "redact customer names and email addresses from 100% of the "
                        "text before it reaches the embedding model"
                    ),
                ),
            ],
        ),
        Requirement(
            id="REQ-006",
            title="Reuse the existing storage stack",
            user_story=(
                "As a platform engineer, I want this on our current database, so "
                "that we do not operate a second datastore."
            ),
            traces_to=["CLM-007"],
            priority="must",
            acceptance_criteria=[
                AcceptanceCriterion(
                    id="AC-1",
                    statement=(
                        "THE evaluation service SHALL store questions, runs and "
                        "results in the existing PostgreSQL database using the "
                        "`pgvector` extension"
                    ),
                ),
            ],
        ),
    ]


def _tasks() -> list:
    return [
        Task(
            id="TASK-001",
            title="Add evaluation schema for questions, runs and results",
            layer="Database/Migration",
            intent=(
                "Versioned migration creating questions, runs and results tables "
                "with a pgvector column for cached embeddings."
            ),
            satisfies=["REQ-001", "REQ-006"],
            depends_on=[],
            verification="pytest tests/test_eval_migrations.py -q",
            estimate_hours=4.0,
        ),
        Task(
            id="TASK-002",
            title="Implement question intake with source-label validation",
            layer="API/Backend",
            intent=(
                "POST /v1/questions accepting a question and its labelled source "
                "document, rejecting unlabelled submissions."
            ),
            satisfies=["REQ-001"],
            depends_on=["TASK-001"],
            verification="pytest tests/test_question_intake.py -q",
            estimate_hours=4.0,
        ),
        Task(
            id="TASK-003",
            title="Build the evaluation run worker",
            layer="Worker/Async",
            intent=(
                "Queue-driven worker that executes a configuration against every "
                "labelled question and records one result row per question."
            ),
            satisfies=["REQ-002"],
            depends_on=["TASK-001"],
            verification="pytest tests/test_eval_run.py -q",
            estimate_hours=6.0,
        ),
        Task(
            id="TASK-004",
            title="Score answers by citation match",
            layer="Eval/Harness",
            intent=(
                "Scoring function marking a question correct when the answer cites "
                "the labelled source, plus the composite score calculation."
            ),
            satisfies=["REQ-002"],
            depends_on=["TASK-003"],
            verification="pytest tests/test_scoring.py -q",
            estimate_hours=5.0,
        ),
        Task(
            id="TASK-005",
            title="Fail the release check on a regression",
            layer="Integration",
            intent=(
                "CI entry point comparing a run against the stored baseline and "
                "exiting non-zero when the score drops past the threshold."
            ),
            satisfies=["REQ-003"],
            depends_on=["TASK-004"],
            verification="pytest tests/test_release_gate.py::test_regression_fails -q",
            estimate_hours=3.0,
        ),
        Task(
            id="TASK-006",
            title="Build the run summary view",
            layer="Frontend/UX",
            intent=(
                "Summary screen showing the composite score and the largest "
                "regressions in plain language, with no model terminology."
            ),
            satisfies=["REQ-004"],
            depends_on=["TASK-004"],
            verification="npm run test:e2e -- --spec run-summary.spec.ts",
            estimate_hours=5.0,
        ),
        Task(
            id="TASK-007",
            title="Pin the judge model and egress policy inside the VPC",
            layer="Infrastructure",
            intent=(
                "Self-hosted judge deployment plus a deny-by-default egress policy "
                "that blocks and audits non-VPC destinations."
            ),
            satisfies=["REQ-005"],
            depends_on=["TASK-001"],
            verification="pytest tests/test_egress_policy.py -q",
            estimate_hours=4.0,
        ),
        Task(
            id="TASK-008",
            title="Redact PII before text reaches the embedding model",
            layer="Worker/Async",
            intent=(
                "Redaction stage removing customer names and email addresses from "
                "transcript text ahead of embedding, with a fixture-based test set."
            ),
            satisfies=["REQ-005"],
            depends_on=["TASK-007"],
            verification="pytest tests/test_redaction.py -q",
            estimate_hours=4.0,
        ),
    ]


def build() -> SpecDocument:
    return SpecDocument(
        name="GenAI search: evaluation harness",
        spec_id="SPEC-2026-002",
        summary=(
            "Hand-authored reference spec. Six requirements and eight tasks derived "
            "from an eight-claim discovery note, with three decisions answered."
        ),
        architecture_notes=(
            "An evaluation service beside the existing search assistant, storing "
            "questions, runs and results in the current PostgreSQL database with "
            "pgvector. Scoring runs on a queue worker rather than in the request "
            "path, because a full run touches 150 questions. The risky part is "
            "egress: the judge model and the embedding step both sit where a "
            "careless default would call a hosted API and break the VPC constraint."
        ),
        claims=_claims(),
        decisions=_decisions(),
        requirements=_requirements(),
        tasks=_tasks(),
        out_of_scope=[
            "Improving retrieval quality itself — this measures, it does not tune",
            "Labelling the initial question set, which is a support-team exercise",
            "Live production monitoring, as opposed to pre-release evaluation",
        ],
        risks=[
            "A 150-question set can drift out of date faster than the quarterly "
            "review catches",
            "Citation-match scoring rewards an answer that cites correctly while "
            "summarising badly",
            "Self-hosted judge quality is unmeasured and may itself regress",
        ],
    )
