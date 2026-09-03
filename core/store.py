"""Saving a run to disk, so a browser refresh cannot destroy paid-for work.

A compile costs real money. Streamlit session state lives only as long as the
websocket, which means a reload, a dropped connection or a server restart
silently throws the result away. This module writes the run to a local JSON
file after every step that changes it.

Persistence is opt-in. On a shared or public deployment one process serves
every visitor, and a shared file would hand one stranger's document to the
next — so an unset SPEC_ENGINE_STORE means off. Set a directory path — or
'on' — on a machine where one person owns the data, and the app offers the
run back through an explicit, metadata-first restore instead of putting it
on screen unasked.

Local, single-user, plain JSON on purpose: the file is readable, deletable, and
carries nothing but what the user already had.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, ValidationError

from .schemas import OpenDecision, SourceClaim, SpecDocument
from .verifier import VerificationReport

DEFAULT_STORE_DIR = ".spec_engine"
FORMAT_VERSION = 1

#: Values of SPEC_ENGINE_STORE that turn persistence off entirely.
DISABLED = {"off", "none", "no", "0", "disabled", ""}

#: Values of SPEC_ENGINE_STORE that opt in at the default directory.
ON_WORDS = {"on", "true", "1", "yes", "enabled"}


def enabled() -> bool:
    """Persistence is opt-in. Set a directory path — or 'on' — on a machine
    where one person owns the data. Unset means off.

    It is exactly right on one person's laptop — a compile costs money and a
    browser refresh must not destroy it. It is exactly wrong on a shared
    deployment, where one process serves every visitor: the next stranger to
    open the app would be handed the last stranger's document.
    """
    val = os.getenv("SPEC_ENGINE_STORE", "").strip().lower()
    return bool(val) and val not in DISABLED


def store_dir() -> Path:
    """Resolved per call, not at import, so tests and users can redirect it."""
    val = os.getenv("SPEC_ENGINE_STORE", "").strip()
    return (
        Path(DEFAULT_STORE_DIR)
        if val.lower() in ON_WORDS
        else Path(val or DEFAULT_STORE_DIR)
    )


def run_file() -> Path:
    return store_dir() / "last_run.json"


class SavedRun(BaseModel):
    """Everything needed to put the user back where they were."""

    model_config = ConfigDict(extra="forbid")

    version: int = FORMAT_VERSION
    saved_at: str = ""
    title: str = "Untitled Initiative"
    document: str = ""
    step: int = 0
    question: int = 0
    claims: Optional[List[SourceClaim]] = None
    decisions: Optional[List[OpenDecision]] = None
    spec: Optional[SpecDocument] = None
    report: Optional[VerificationReport] = None
    model: str = ""
    base_url: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    repair_rounds: int = 0

    @property
    def has_spec(self) -> bool:
        return self.spec is not None

    def describe(self) -> str:
        """A one-line summary for the restore prompt."""
        when = self.saved_at.replace("T", " ")[:16] if self.saved_at else "earlier"
        if self.spec is not None:
            what = (
                f"{len(self.spec.requirements)} requirements, "
                f"{len(self.spec.tasks)} tasks"
            )
        elif self.decisions is not None:
            what = f"{len(self.decisions)} open questions"
        elif self.claims is not None:
            what = f"{len(self.claims)} claims"
        else:
            what = "a document"
        return f"{self.title} — {what}, saved {when}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save(run: SavedRun, path: Optional[Path] = None) -> Optional[Path]:
    """Write atomically: a half-written file must never replace a good one.

    Returns None without writing when persistence is disabled.
    """
    if path is None and not enabled():
        return None
    path = path or run_file()
    run.saved_at = _now()
    run.version = FORMAT_VERSION
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write(run.model_dump_json(indent=2))
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise
    return path


def load(path: Optional[Path] = None) -> Optional[SavedRun]:
    """Return the saved run, or None if there is nothing usable.

    A corrupt or outdated file is treated as absent rather than fatal — losing
    a restore is an inconvenience, refusing to start is not acceptable.
    """
    if path is None and not enabled():
        return None
    path = path or run_file()
    if not path.exists():
        return None
    try:
        payload: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("version") != FORMAT_VERSION:
        return None
    try:
        return SavedRun.model_validate(payload)
    except ValidationError:
        return None


def clear(path: Optional[Path] = None) -> None:
    path = path or run_file()
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def exists(path: Optional[Path] = None) -> bool:
    if path is None and not enabled():
        return False
    return (path or run_file()).exists()
