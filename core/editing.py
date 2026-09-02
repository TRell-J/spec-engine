"""Applying a human's corrections to a compiled spec.

Pure functions: rows and text in, validated objects out. The UI owns the
widgets and the session; this module owns the rules, so the rules can be tested
without a browser.

A correction is held to exactly the same standard as the model's output. A quote
typed by hand is still checked against the document, and a criterion rewritten
by hand still has to parse as EARS — otherwise editing would be a hole in the
gate rather than a way through it.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Sequence

from .schemas import AcceptanceCriterion, Requirement, SourceClaim
from .verifier import find_quote_line

CLAIM_KINDS = ("requirement", "constraint", "assumption", "context")


def rebuild_claims(rows: Iterable[Dict[str, str]], document: str) -> List[SourceClaim]:
    """Turn editor rows into claims: renumbered, re-grounded, blanks dropped.

    IDs are reassigned from scratch because a deletion in the middle would
    otherwise leave a gap, and because requirements tracing to a deleted claim
    must not silently point at a different one.
    """
    rebuilt: List[SourceClaim] = []
    for index, row in enumerate(rows, start=1):
        quote = (row.get("Quote") or "").strip()
        if not quote:
            continue
        reading = (row.get("What it commits to") or "").strip()
        # The schema requires a reading of real substance; a blank or a couple
        # of characters falls back to the quote rather than failing the save.
        if len(reading) < 3:
            reading = quote
        kind = (row.get("Kind") or "requirement").strip()
        if kind not in CLAIM_KINDS:
            kind = "requirement"
        rebuilt.append(
            SourceClaim(
                id=f"CLM-{index:03d}",
                quote=quote,
                # 0 means "not found in the document" — the verifier will block
                # on it exactly as it would for a quote the model invented.
                line=find_quote_line(document, quote) or 0,
                kind=kind,
                reading=reading,
            )
        )
    return rebuilt


def apply_criteria(
    requirement: Requirement, statements: Sequence[str], title: str = ""
) -> Requirement:
    """Return the requirement with rewritten criteria, or raise.

    Raises `ValueError`/`ValidationError` if any statement is not EARS, leaving
    the original untouched — a half-applied edit is worse than a refused one.
    """
    if len(statements) != len(requirement.acceptance_criteria):
        raise ValueError(
            f"expected {len(requirement.acceptance_criteria)} statements, "
            f"got {len(statements)}"
        )
    rewritten = [
        AcceptanceCriterion(id=criterion.id, statement=text)
        for criterion, text in zip(
            requirement.acceptance_criteria, statements, strict=True
        )
    ]
    if not rewritten:
        raise ValueError("a requirement needs at least one acceptance criterion")

    updated = requirement.model_copy(deep=True)
    updated.acceptance_criteria = rewritten
    cleaned = (title or "").strip()
    if cleaned:
        updated.title = cleaned
    return updated


def replace_requirement(
    requirements: List[Requirement], updated: Requirement
) -> List[Requirement]:
    """Swap one requirement in place, preserving order."""
    return [updated if r.id == updated.id else r for r in requirements]
