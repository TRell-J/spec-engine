"""The example catalogue.

One entry per scenario, each pairing a source document with the hand-authored
spec compiled from it. The app derives both its preset documents and its
walkthroughs from this list, so a preset can never point at a different
scenario's example — which is exactly what happened when the two were declared
separately.

Every example is verified against its own document by the test suite, using the
same gate a live compile faces.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List

from core.schemas import SpecDocument

from . import genai_search, reference, revops_ingestion


@dataclass(frozen=True)
class Example:
    """A scenario: the document, the spec, and what to call it on screen."""

    key: str
    label: str          # the preset button
    short: str          # the walkthrough button
    document: str
    build: Callable[[], SpecDocument]

    def spec(self) -> SpecDocument:
        """A fresh copy each call, so a walkthrough cannot mutate the catalogue."""
        return self.build()


EXAMPLES: List[Example] = [
    Example(
        key="deal_desk",
        label="Deal desk approval routing",
        short="Deal desk",
        document=reference.REFERENCE_DOCUMENT,
        build=reference.reference_spec,
    ),
    Example(
        key="genai_search",
        label="GenAI search evaluation",
        short="GenAI search",
        document=genai_search.DOCUMENT,
        build=genai_search.build,
    ),
    Example(
        key="revops",
        label="RevOps ingestion",
        short="RevOps",
        document=revops_ingestion.DOCUMENT,
        build=revops_ingestion.build,
    ),
]

BY_KEY: Dict[str, Example] = {example.key: example for example in EXAMPLES}
BY_LABEL: Dict[str, Example] = {example.label: example for example in EXAMPLES}

DEFAULT_KEY = EXAMPLES[0].key


def get(key: str) -> Example:
    """Look up an example, falling back to the default rather than raising."""
    return BY_KEY.get(key, BY_KEY[DEFAULT_KEY])


def documents() -> Dict[str, str]:
    """Label -> document, for the preset buttons."""
    return {example.label: example.document for example in EXAMPLES}
