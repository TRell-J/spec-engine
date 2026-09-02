"""A hand-authored reference spec.

This is **not** generated output and the app never presents it as a compile of
the user's document. It exists for two reasons: the test suite needs a spec that
satisfies every invariant, and someone without an API key should be able to see
what the artifacts look like before deciding to spend a token.

The source document below is the real input for these claims — every quote is
copied from it, so `verifier.verify(reference_spec(), REFERENCE_DOCUMENT)` passes
its grounding check the same way a live compile has to.
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

REFERENCE_DOCUMENT = """# Deal Desk: discount approval routing

Reps currently wait on Slack for discount approval, and the desk loses the thread.

Any quote with a discount above 20% must be approved by the deal desk before it
can be sent to the customer.

Approvals must be recorded with the approver, the timestamp, and the discount
amount, because we are in a SOC 2 audit window.

If the deal desk does not respond within 4 business hours, the quote escalates to
the regional sales director.

Reps must be able to see where their quote sits in the approval queue.

The system of record for accounts and opportunities is Salesforce.
"""


def _claims() -> list:
    return [
        SourceClaim(
            id="CLM-001",
            quote=(
                "Any quote with a discount above 20% must be approved by the deal "
                "desk before it can be sent to the customer."
            ),
            line=5,
            kind="requirement",
            reading=(
                "Quotes over a 20% discount cannot reach a customer until the deal "
                "desk approves them."
            ),
        ),
        SourceClaim(
            id="CLM-002",
            quote=(
                "Approvals must be recorded with the approver, the timestamp, and "
                "the discount amount"
            ),
            line=8,
            kind="requirement",
            reading="Each approval writes an audit record with three named fields.",
        ),
        SourceClaim(
            id="CLM-003",
            quote=(
                "If the deal desk does not respond within 4 business hours, the "
                "quote escalates to the regional sales director."
            ),
            line=11,
            kind="requirement",
            reading="Unanswered approvals reassign to the regional sales director.",
        ),
        SourceClaim(
            id="CLM-004",
            quote="Reps must be able to see where their quote sits in the approval queue.",
            line=14,
            kind="requirement",
            reading="Reps get visibility into queue position for their own quotes.",
        ),
        SourceClaim(
            id="CLM-005",
            quote="The system of record for accounts and opportunities is Salesforce.",
            line=16,
            kind="constraint",
            reading="Salesforce owns account and opportunity data; this service does not.",
        ),
        SourceClaim(
            id="CLM-006",
            quote="Reps currently wait on Slack for discount approval",
            line=3,
            kind="context",
            reading="Today's process is an untracked Slack thread.",
        ),
    ]


def _decisions() -> list:
    return [
        OpenDecision(
            id="DEC-001",
            question="Who may approve a discount above 40%?",
            why_it_blocks=(
                "The document sets one threshold at 20% but names no ceiling, so an "
                "agent would let the deal desk approve any discount."
            ),
            options=[
                "Deal desk approves all discounts",
                "Deal desk up to 40%, VP of Sales above",
                "Any discount above 40% goes to the CFO",
            ],
            proposed_default=(
                "The deal desk may approve up to 40%; above 40% routes to the VP of "
                "Sales."
            ),
            answer=(
                "The deal desk may approve up to 40%; above 40% routes to the VP of "
                "Sales."
            ),
        ),
        OpenDecision(
            id="DEC-002",
            question="What happens to an in-flight quote when the threshold changes?",
            why_it_blocks=(
                "Without a rule, changing the threshold silently re-evaluates every "
                "pending quote and can revoke an approval a rep already promised."
            ),
            options=[
                "Re-evaluate all pending quotes",
                "Freeze the threshold captured at submission",
            ],
            proposed_default=(
                "A quote keeps the threshold captured at submission time until it is "
                "resolved."
            ),
            answer=(
                "A quote keeps the threshold captured at submission time until it is "
                "resolved."
            ),
        ),
    ]


def _requirements() -> list:
    return [
        Requirement(
            id="REQ-001",
            title="Discount approval gate",
            user_story=(
                "As a deal desk analyst, I want quotes over the discount threshold "
                "held for approval, so that no rep can send a discount the business "
                "has not agreed to."
            ),
            traces_to=["CLM-001", "DEC-001"],
            priority="must",
            acceptance_criteria=[
                AcceptanceCriterion(
                    id="AC-1",
                    statement=(
                        "WHEN a rep submits a quote with a discount greater than 20%, "
                        "the quote service SHALL set the quote status to "
                        "`pending_approval` and return HTTP 202"
                    ),
                ),
                AcceptanceCriterion(
                    id="AC-2",
                    statement=(
                        "IF a rep attempts to send a quote whose status is "
                        "`pending_approval`, THEN the quote service SHALL reject the "
                        "request with HTTP 403 and the error code `approval.required`"
                    ),
                ),
                AcceptanceCriterion(
                    id="AC-3",
                    statement=(
                        "WHEN a quote with a discount greater than 40% enters "
                        "approval, the quote service SHALL assign the approval to the "
                        "`vp_sales` queue"
                    ),
                ),
            ],
        ),
        Requirement(
            id="REQ-002",
            title="Approval audit record",
            user_story=(
                "As a compliance owner, I want every approval decision recorded, so "
                "that the SOC 2 auditor can reconstruct who approved what."
            ),
            traces_to=["CLM-002"],
            priority="must",
            acceptance_criteria=[
                AcceptanceCriterion(
                    id="AC-1",
                    statement=(
                        "WHEN an approver approves or rejects a quote, the quote "
                        "service SHALL append 1 audit record containing the approver "
                        "id, an ISO-8601 timestamp, and the discount amount"
                    ),
                ),
                AcceptanceCriterion(
                    id="AC-2",
                    statement=(
                        "THE quote service SHALL retain approval audit records for "
                        "400 days"
                    ),
                ),
            ],
        ),
        Requirement(
            id="REQ-003",
            title="Escalation on approval timeout",
            user_story=(
                "As a rep, I want a stalled approval to escalate on its own, so that "
                "a quote does not sit unanswered at quarter end."
            ),
            traces_to=["CLM-003", "DEC-002"],
            priority="must",
            acceptance_criteria=[
                AcceptanceCriterion(
                    id="AC-1",
                    statement=(
                        "WHILE a quote is in `pending_approval`, WHEN 4 business "
                        "hours elapse without a decision, the quote service SHALL "
                        "reassign the approval to the `regional_director` queue"
                    ),
                ),
                AcceptanceCriterion(
                    id="AC-2",
                    statement=(
                        "WHEN an approval is reassigned, the quote service SHALL "
                        "notify the submitting rep within 60 seconds"
                    ),
                ),
                AcceptanceCriterion(
                    id="AC-3",
                    statement=(
                        "WHILE a quote is unresolved, the quote service SHALL "
                        "evaluate approval against the `submitted_threshold` stored "
                        "on the quote and ignore later threshold changes"
                    ),
                ),
            ],
        ),
        Requirement(
            id="REQ-004",
            title="Approval queue visibility",
            user_story=(
                "As a rep, I want to see where my quote sits in the queue, so that I "
                "can tell the customer when to expect an answer."
            ),
            traces_to=["CLM-004"],
            priority="should",
            acceptance_criteria=[
                AcceptanceCriterion(
                    id="AC-1",
                    statement=(
                        "WHEN a rep opens the quote list, the quote service SHALL "
                        "display the queue position and the elapsed wait time for "
                        "each quote in `pending_approval`"
                    ),
                ),
                AcceptanceCriterion(
                    id="AC-2",
                    statement=(
                        "IF a rep requests a quote they did not submit, THEN the "
                        "quote service SHALL respond with HTTP 403"
                    ),
                ),
            ],
        ),
        Requirement(
            id="REQ-005",
            title="Salesforce remains the system of record",
            user_story=(
                "As a RevOps owner, I want account data to stay authoritative in "
                "Salesforce, so that the quote service never becomes a second source "
                "of truth."
            ),
            traces_to=["CLM-005"],
            priority="must",
            acceptance_criteria=[
                AcceptanceCriterion(
                    id="AC-1",
                    statement=(
                        "WHEN account or opportunity data changes, the quote service "
                        "SHALL write to Salesforce before its own store and cache the "
                        "result for no more than 300 seconds"
                    ),
                ),
                AcceptanceCriterion(
                    id="AC-2",
                    statement=(
                        "IF the Salesforce write fails, THEN the quote service SHALL "
                        "return HTTP 502 and retain 0 local changes"
                    ),
                ),
            ],
        ),
    ]


def _tasks() -> list:
    return [
        Task(
            id="TASK-001",
            title="Add approval_requests table with status and threshold columns",
            layer="Database/Migration",
            intent=(
                "Versioned migration creating approval_requests with quote id, "
                "status, captured threshold, assigned queue, and timestamps."
            ),
            satisfies=["REQ-001", "REQ-002", "REQ-003"],
            depends_on=[],
            verification="pytest tests/test_migrations.py -q",
            estimate_hours=3.0,
        ),
        Task(
            id="TASK-002",
            title="Enforce the discount approval gate on submit and send",
            layer="API/Backend",
            intent=(
                "Gate quote submission and sending on approval status, returning 202 "
                "on hold and 403 with approval.required on a blocked send."
            ),
            satisfies=["REQ-001"],
            depends_on=["TASK-001"],
            verification="pytest tests/test_approval_gate.py -q",
            estimate_hours=5.0,
        ),
        Task(
            id="TASK-003",
            title="Append immutable approval audit records",
            layer="API/Backend",
            intent=(
                "Write one append-only audit row per approval decision and enforce "
                "the 400-day retention policy in the reaper job."
            ),
            satisfies=["REQ-002"],
            depends_on=["TASK-001"],
            verification="pytest tests/test_audit_log.py::test_record_written -q",
            estimate_hours=3.0,
        ),
        Task(
            id="TASK-004",
            title="Escalate pending approvals after four business hours",
            layer="Worker/Async",
            intent=(
                "Scheduled worker reassigns stale approvals to the regional director "
                "queue and notifies the submitting rep."
            ),
            satisfies=["REQ-003"],
            depends_on=["TASK-002"],
            verification="pytest tests/test_escalation.py -q",
            estimate_hours=5.0,
        ),
        Task(
            id="TASK-005",
            title="Show queue position and wait time in the rep quote list",
            layer="Frontend/UX",
            intent=(
                "Quote list column showing position and elapsed wait, scoped to the "
                "requesting rep's own quotes."
            ),
            satisfies=["REQ-004"],
            depends_on=["TASK-002"],
            verification="npm run test:e2e -- --spec queue.spec.ts",
            estimate_hours=4.0,
        ),
        Task(
            id="TASK-006",
            title="Write account changes through to Salesforce first",
            layer="Integration",
            intent=(
                "Write-through client that commits to Salesforce before the local "
                "store, with a 300-second cache and rollback on failure."
            ),
            satisfies=["REQ-005"],
            depends_on=["TASK-001"],
            verification="pytest tests/test_salesforce_write_through.py -q",
            estimate_hours=5.0,
        ),
        Task(
            id="TASK-007",
            title="Add approval-path eval suite to CI",
            layer="Eval/Harness",
            intent=(
                "Scenario suite covering the gate, escalation and write-through "
                "paths, run on every pull request."
            ),
            satisfies=["REQ-001", "REQ-003", "REQ-005"],
            depends_on=["TASK-002", "TASK-004", "TASK-006"],
            verification="pytest tests/eval/test_approval_scenarios.py -q",
            estimate_hours=4.0,
        ),
    ]


def reference_spec() -> SpecDocument:
    """The hand-authored example. Rebuilt on each call so callers can mutate it."""
    return SpecDocument(
        name="Deal Desk: discount approval routing",
        spec_id="SPEC-2026-001",
        summary=(
            "Hand-authored reference spec. Five requirements and seven tasks derived "
            "from a six-claim source document, with two decisions answered."
        ),
        architecture_notes=(
            "A quote service owning approval state in PostgreSQL, with Salesforce as "
            "the system of record for accounts and opportunities. Escalation runs on "
            "a scheduled worker rather than in the request path, so a stalled "
            "approval cannot hold a connection. The risky part is the write-through "
            "to Salesforce: it is the only place where two stores can disagree."
        ),
        claims=_claims(),
        decisions=_decisions(),
        requirements=_requirements(),
        tasks=_tasks(),
        out_of_scope=[
            "Quote PDF rendering and delivery to the customer",
            "Commission calculation on approved discounts",
            "Backfill of approvals that happened in Slack before launch",
        ],
        risks=[
            "Business-hours arithmetic depends on a per-region calendar that the "
            "source document does not define",
            "Salesforce API rate limits may throttle write-through under bulk edits",
        ],
    )
