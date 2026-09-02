"""Reference example: multi-source ingestion with provenance and human review.

Hand-authored, like every example here. Each quote is copied from the document
below, so this spec passes the same grounding check a live compile faces.

Chosen for its third shape: the document is workshop notes rather than a PRD,
one of its claims is an explicit non-decision ("we have not decided"), and the
hard requirement is trust — finance will not use a number it cannot trace.
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

DOCUMENT = """# Workshop notes: RevOps ingestion

Every Monday an analyst reconciles vendor spreadsheets, CRM exports and billing
webhooks by hand. It takes most of the day.

About 40 source files arrive each week. Formats vary and some files arrive with
missing columns.

Finance needs to see where every number came from before they will trust a report.

Billing exports contain customer PII.

When a record cannot be matched automatically, a human should review it rather
than the system guessing.

We have not decided whether this writes back to Salesforce or stays read-only.
"""


def _claims() -> list:
    return [
        SourceClaim(
            id="CLM-001",
            quote=(
                "Every Monday an analyst reconciles vendor spreadsheets, CRM exports "
                "and billing webhooks by hand."
            ),
            line=3,
            kind="context",
            reading="Today's reconciliation is manual and consumes a working day.",
        ),
        SourceClaim(
            id="CLM-002",
            quote="About 40 source files arrive each week.",
            line=6,
            kind="constraint",
            reading="The weekly run must absorb roughly forty files.",
        ),
        SourceClaim(
            id="CLM-003",
            quote="Formats vary and some files arrive with missing columns.",
            line=6,
            kind="constraint",
            reading="Malformed input is normal, not exceptional.",
        ),
        SourceClaim(
            id="CLM-004",
            quote=(
                "Finance needs to see where every number came from before they will "
                "trust a report."
            ),
            line=9,
            kind="requirement",
            reading="Every reported value must be traceable to its source row.",
        ),
        SourceClaim(
            id="CLM-005",
            quote="Billing exports contain customer PII.",
            line=11,
            kind="constraint",
            reading="Billing data carries personal data that needs protecting.",
        ),
        SourceClaim(
            id="CLM-006",
            quote=(
                "When a record cannot be matched automatically, a human should "
                "review it rather than the system guessing."
            ),
            line=13,
            kind="requirement",
            reading="Unmatched records go to a person, never to a guess.",
        ),
        SourceClaim(
            id="CLM-007",
            quote=(
                "We have not decided whether this writes back to Salesforce or stays "
                "read-only."
            ),
            line=16,
            kind="assumption",
            reading=(
                "The write direction is explicitly undecided and must be settled "
                "before build."
            ),
        ),
    ]


def _decisions() -> list:
    return [
        OpenDecision(
            id="DEC-001",
            question="Does this write back to Salesforce, or stay read-only?",
            why_it_blocks=(
                "The document names this as undecided. An agent would pick one, and "
                "a wrong write path corrupts the CRM that finance already trusts."
            ),
            options=[
                "Read-only for the first release",
                "Write back matched records only",
                "Full two-way sync",
            ],
            proposed_default=(
                "Read-only for the first release; Salesforce stays the system of "
                "record and this service never writes to it."
            ),
            answer=(
                "Read-only for the first release; Salesforce stays the system of "
                "record and this service never writes to it."
            ),
        ),
        OpenDecision(
            id="DEC-002",
            question="What happens to a file that is missing a required column?",
            why_it_blocks=(
                "Failing the whole run on one bad file loses the week; ignoring it "
                "silently loses the data. An agent would choose one without asking."
            ),
            options=[
                "Fail the entire run",
                "Quarantine the file and continue",
                "Import the rows that do parse",
            ],
            proposed_default=(
                "Quarantine the file, continue the run, and alert the analyst on "
                "rota the same day."
            ),
            answer=(
                "Quarantine the file, continue the run, and alert the analyst on "
                "rota the same day."
            ),
        ),
        OpenDecision(
            id="DEC-003",
            question="Who reviews an unmatched record, and within what window?",
            why_it_blocks=(
                "A review queue with no owner and no deadline silently becomes a "
                "backlog nobody reads."
            ),
            options=[
                "The RevOps analyst on rota",
                "The account owner in Salesforce",
                "Any finance team member",
            ],
            proposed_default=(
                "The RevOps analyst on rota reviews unmatched records within 1 "
                "business day."
            ),
            answer=(
                "The RevOps analyst on rota reviews unmatched records within 1 "
                "business day."
            ),
        ),
    ]


def _requirements() -> list:
    return [
        Requirement(
            id="REQ-001",
            title="Weekly ingestion of every source",
            user_story=(
                "As a RevOps analyst, I want every source pulled in automatically, "
                "so that Monday is not spent reconciling by hand."
            ),
            traces_to=["CLM-002", "CLM-003", "DEC-002"],
            priority="must",
            acceptance_criteria=[
                AcceptanceCriterion(
                    id="AC-1",
                    statement=(
                        "WHEN a source file arrives, the ingestion service SHALL "
                        "parse it and write 1 row per source record"
                    ),
                ),
                AcceptanceCriterion(
                    id="AC-2",
                    statement=(
                        "IF a file is missing a required column, THEN the ingestion "
                        "service SHALL move it to the `quarantine` bucket and "
                        "continue the run"
                    ),
                ),
                AcceptanceCriterion(
                    id="AC-3",
                    statement=(
                        "THE ingestion service SHALL process 40 source files within "
                        "30 minutes of the weekly window opening"
                    ),
                ),
            ],
        ),
        Requirement(
            id="REQ-002",
            title="Provenance for every reported number",
            user_story=(
                "As a finance partner, I want to see where a number came from, so "
                "that I can sign off on a report."
            ),
            traces_to=["CLM-004"],
            priority="must",
            acceptance_criteria=[
                AcceptanceCriterion(
                    id="AC-1",
                    statement=(
                        "WHEN a report value is displayed, the reporting service "
                        "SHALL link it to exactly 1 source row, naming the file and "
                        "the ingest timestamp"
                    ),
                ),
                AcceptanceCriterion(
                    id="AC-2",
                    statement=(
                        "THE reporting service SHALL retain the provenance record "
                        "for 400 days"
                    ),
                ),
            ],
        ),
        Requirement(
            id="REQ-003",
            title="Unmatched records go to a person",
            user_story=(
                "As a RevOps lead, I want unmatched records queued for review, so "
                "that the system never invents a match."
            ),
            traces_to=["CLM-006", "DEC-003"],
            priority="must",
            acceptance_criteria=[
                AcceptanceCriterion(
                    id="AC-1",
                    statement=(
                        "IF a record cannot be matched to an existing account, THEN "
                        "the ingestion service SHALL place it in the `review` queue "
                        "and create 0 new accounts"
                    ),
                ),
                AcceptanceCriterion(
                    id="AC-2",
                    statement=(
                        "WHILE a record sits in the `review` queue, the reporting "
                        "service SHALL exclude it from 100% of published reports"
                    ),
                ),
                AcceptanceCriterion(
                    id="AC-3",
                    statement=(
                        "WHEN a record has waited more than 1 business day, the "
                        "ingestion service SHALL send 1 notification to the analyst "
                        "on rota"
                    ),
                ),
            ],
        ),
        Requirement(
            id="REQ-004",
            title="Personal data in billing exports is protected",
            user_story=(
                "As a data protection owner, I want PII handled deliberately, so "
                "that a billing export cannot leak through a log file."
            ),
            traces_to=["CLM-005"],
            priority="must",
            acceptance_criteria=[
                AcceptanceCriterion(
                    id="AC-1",
                    statement=(
                        "WHEN a billing export is ingested, the ingestion service "
                        "SHALL encrypt its PII columns at rest with a key rotated "
                        "every 90 days"
                    ),
                ),
                AcceptanceCriterion(
                    id="AC-2",
                    statement=(
                        "THE ingestion service SHALL exclude PII columns from 100% "
                        "of application logs"
                    ),
                ),
            ],
        ),
        Requirement(
            id="REQ-005",
            title="Salesforce stays read-only",
            user_story=(
                "As a RevOps lead, I want the CRM left untouched, so that a first "
                "release cannot corrupt the system of record."
            ),
            traces_to=["CLM-007", "DEC-001"],
            priority="must",
            acceptance_criteria=[
                AcceptanceCriterion(
                    id="AC-1",
                    statement=(
                        "THE ingestion service SHALL make 0 write requests to the "
                        "Salesforce API"
                    ),
                ),
                AcceptanceCriterion(
                    id="AC-2",
                    statement=(
                        "IF a write to Salesforce is attempted, THEN the ingestion "
                        "service SHALL reject it with HTTP 405 and write 1 audit "
                        "record"
                    ),
                ),
            ],
        ),
    ]


def _tasks() -> list:
    return [
        Task(
            id="TASK-001",
            title="Add ingestion tables for sources, records and provenance",
            layer="Database/Migration",
            intent=(
                "Versioned migration creating source files, normalized records and "
                "a provenance table linking every value to its origin row."
            ),
            satisfies=["REQ-001", "REQ-002"],
            depends_on=[],
            verification="pytest tests/test_ingestion_migrations.py -q",
            estimate_hours=4.0,
        ),
        Task(
            id="TASK-002",
            title="Build parsers for spreadsheet, CRM export and webhook sources",
            layer="Integration",
            intent=(
                "One parser per source shape, normalizing to the common record "
                "model and recording the originating file and row."
            ),
            satisfies=["REQ-001"],
            depends_on=["TASK-001"],
            verification="pytest tests/test_parsers.py -q",
            estimate_hours=6.0,
        ),
        Task(
            id="TASK-003",
            title="Quarantine malformed files without stopping the run",
            layer="Worker/Async",
            intent=(
                "Column check ahead of parsing that diverts a malformed file to the "
                "quarantine bucket and alerts the analyst on rota."
            ),
            satisfies=["REQ-001"],
            depends_on=["TASK-002"],
            verification="pytest tests/test_quarantine.py -q",
            estimate_hours=3.0,
        ),
        Task(
            id="TASK-004",
            title="Expose provenance for every reported value",
            layer="API/Backend",
            intent=(
                "Read API returning the source file, row number and ingest "
                "timestamp behind any value shown in a report."
            ),
            satisfies=["REQ-002"],
            depends_on=["TASK-001"],
            verification="pytest tests/test_provenance_api.py -q",
            estimate_hours=4.0,
        ),
        Task(
            id="TASK-005",
            title="Route unmatched records to the review queue",
            layer="Worker/Async",
            intent=(
                "Matching stage that queues anything below the confidence threshold "
                "instead of creating an account, and excludes it from reports."
            ),
            satisfies=["REQ-003"],
            depends_on=["TASK-002"],
            verification="pytest tests/test_review_queue.py -q",
            estimate_hours=5.0,
        ),
        Task(
            id="TASK-006",
            title="Build the analyst review screen",
            layer="Frontend/UX",
            intent=(
                "Queue view showing each unmatched record beside its candidate "
                "accounts, with accept and reject actions and a waiting-time column."
            ),
            satisfies=["REQ-003"],
            depends_on=["TASK-005"],
            verification="npm run test:e2e -- --spec review-queue.spec.ts",
            estimate_hours=5.0,
        ),
        Task(
            id="TASK-007",
            title="Encrypt PII columns and scrub them from logs",
            layer="Infrastructure",
            intent=(
                "Column-level encryption for billing PII with KMS rotation, plus a "
                "log filter that drops those fields before they are written."
            ),
            satisfies=["REQ-004"],
            depends_on=["TASK-001"],
            verification="pytest tests/test_pii_protection.py -q",
            estimate_hours=4.0,
        ),
        Task(
            id="TASK-008",
            title="Block writes to the Salesforce API",
            layer="API/Backend",
            intent=(
                "Read-only Salesforce client that refuses mutating verbs and audits "
                "any attempt, so the read-only decision is enforced in code."
            ),
            satisfies=["REQ-005"],
            depends_on=["TASK-002"],
            verification="pytest tests/test_salesforce_readonly.py -q",
            estimate_hours=2.0,
        ),
        Task(
            id="TASK-009",
            title="Add the weekly ingestion scenario suite",
            layer="Eval/Harness",
            intent=(
                "End-to-end suite covering a full weekly window: forty files, one "
                "quarantined, one unmatched record, provenance asserted throughout."
            ),
            satisfies=["REQ-001", "REQ-002", "REQ-003"],
            depends_on=["TASK-003", "TASK-004", "TASK-005"],
            verification="pytest tests/eval/test_weekly_window.py -q",
            estimate_hours=5.0,
        ),
    ]


def build() -> SpecDocument:
    return SpecDocument(
        name="RevOps: multi-source ingestion",
        spec_id="SPEC-2026-003",
        summary=(
            "Hand-authored reference spec. Five requirements and nine tasks derived "
            "from a seven-claim workshop note, with three decisions answered."
        ),
        architecture_notes=(
            "A scheduled ingestion service normalizing three source shapes into one "
            "record model in PostgreSQL, with a provenance row for every value. "
            "Matching runs on a worker and hands anything uncertain to a review "
            "queue rather than resolving it. Salesforce is read-only by decision, "
            "enforced in the client rather than by convention. The risky part is "
            "matching confidence: too strict floods the queue, too loose invents "
            "relationships finance will later find."
        ),
        claims=_claims(),
        decisions=_decisions(),
        requirements=_requirements(),
        tasks=_tasks(),
        out_of_scope=[
            "Writing anything back to Salesforce, deferred by DEC-001",
            "Historical backfill of prior weeks, which needs its own data audit",
            "Vendor contract parsing, which the workshop did not cover",
        ],
        risks=[
            "Match confidence thresholds are unvalidated and may flood the review "
            "queue in week one",
            "Vendors change spreadsheet formats without notice, so quarantine volume "
            "is unpredictable",
            "A 30-minute window assumes file sizes the workshop never stated",
        ],
    )
