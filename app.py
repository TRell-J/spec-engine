"""Obvious Spec-Engine — Streamlit front end.

Four screens, one job each, following the "one thing per page" pattern:

    1  Document        paste it
    2  What I read     check the machine understood you
    3  Open questions  one question at a time
    4  Your spec       verdict first, detail on demand

Nothing is nested more than one level, and every screen has exactly one primary
action and a way back.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import os
from typing import List, Optional

import streamlit as st
from dotenv import load_dotenv

import theme
from core import editing, exporter, pricing, providers, store
from core.pipeline import (
    CompileResult,
    Usage,
    build_client,
    compile_spec,
    extract_claims,
    interrogate,
    repair_finding,
    resolve_settings,
)
from core.providers import ProviderError, Settings
from core.schemas import OpenDecision, SourceClaim, SpecDocument
from core.verifier import VerificationReport, assess_source, verify
from examples import registry

load_dotenv()


def _adopt_secrets() -> None:
    """A hosted deployment is configured through secrets, not a .env file.

    Copied in with setdefault so a real environment variable always wins, and
    wrapped because having no secrets at all is the normal local case.
    """
    try:
        for key, value in st.secrets.items():
            if isinstance(value, str):
                os.environ.setdefault(key, value)
    except Exception:
        pass


_adopt_secrets()

st.set_page_config(
    page_title="Spec-Engine",
    page_icon="◆",
    layout="centered",
    initial_sidebar_state="collapsed",
)
st.markdown(theme.CSS, unsafe_allow_html=True)

HTML = dict(unsafe_allow_html=True)
TOTAL_STEPS = 4

PRESETS = registry.documents()

# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #


def init_state() -> None:
    defaults = {
        "document_text": PRESETS["Deal desk approval routing"],
        "document_input": PRESETS["Deal desk approval routing"],
        "claims": None,
        "decisions": None,
        "result": None,
        "step": 0,
        "question": 0,
        "pending": None,
        "title": "Untitled Initiative",
        "view": "Requirements",
        "example": False,
        "saved_document": None,
        "restored": False,
        "editing_claims": False,
        "example_key": registry.DEFAULT_KEY,
        "usage": Usage(),
    }
    # Provider configuration, seeded from the environment on first load.
    started = resolve_settings()
    defaults.update(
        provider_key=started.provider,
        model_name=started.model,
        base_url=started.base_url,
        api_key="",
    )
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)
    for canonical, widget in _MIRRORED.items():
        st.session_state.setdefault(widget, st.session_state[canonical])
    st.session_state.setdefault(
        "provider_widget", providers.spec_for(st.session_state["provider_key"]).label
    )

# Every one of these is read on steps 2-4, where its widget is not on screen.
# Streamlit garbage-collects widget state for widgets it did not render, so the
# value has to live under a key no widget owns, with the widget mirroring it.
_MIRRORED = {
    "model_name": "model_widget",
    "base_url": "base_url_widget",
    "api_key": "api_key_widget",
}


def _mirror(canonical: str) -> None:
    """Copy a widget's value back to the key that survives the screen."""
    st.session_state[canonical] = st.session_state.get(_MIRRORED[canonical], "")


def provider_overrides() -> dict:
    return {
        "provider": st.session_state.get("provider_key"),
        "model": st.session_state.get("model_name"),
        "base_url": st.session_state.get("base_url"),
        "api_key": st.session_state.get("api_key"),
    }


def settings() -> Settings:
    return resolve_settings(provider_overrides())


def provider_changed() -> None:
    """Switching provider reloads that preset's defaults and drops the key.

    Carrying a key across providers would send an Anthropic secret to whoever
    owns the next base URL.
    """
    label = st.session_state.get("provider_widget", "")
    spec = providers.spec_for(label)
    st.session_state.update(
        provider_key=spec.key,
        model_name=spec.default_model,
        model_widget=spec.default_model,
        base_url=spec.base_url,
        base_url_widget=spec.base_url,
        api_key="",
        api_key_widget="",
    )
    st.session_state.pop("connection", None)
    st.session_state.pop("error", None)


def check_connection() -> None:
    """Ask the endpoint whether it is there, before a compile finds out."""
    provider = build_client(overrides=provider_overrides())
    if provider is None:
        st.session_state["connection"] = ("error", _unconfigured_reason(settings()))
        return
    try:
        st.session_state["connection"] = ("ok", provider.check())
    except Exception as exc:
        message = str(exc) if isinstance(exc, ProviderError) else f"{type(exc).__name__}: {exc}"
        st.session_state["connection"] = ("error", message)


def _unconfigured_reason(current: Settings) -> str:
    if not current.model.strip():
        return "Name a model before compiling."
    return f"Add an API key for {current.label} before compiling."


def document_text() -> str:
    """The canonical document.

    Deliberately NOT the text area's widget key: Streamlit discards widget
    state for widgets that are not rendered, so on steps 2-4 a widget-keyed
    document silently disappears — taking the grounding check and the saved
    run with it.
    """
    return st.session_state.get("document_text", "")


def set_document(text: str) -> None:
    """Write both the canonical value and the widget that displays it."""
    st.session_state["document_text"] = text
    st.session_state["document_input"] = text


def _persist() -> None:
    """Write the run to disk so a refresh cannot destroy paid-for work."""
    if st.session_state.get("example"):
        return  # an example is not the user's work
    result = st.session_state.get("result")
    usage = st.session_state.get("usage")
    try:
        store.save(
            store.SavedRun(
                title=st.session_state.get("title", "Untitled Initiative"),
                document=document_text(),
                step=st.session_state.get("step", 0),
                question=st.session_state.get("question", 0),
                claims=st.session_state.get("claims"),
                decisions=st.session_state.get("decisions"),
                spec=result.spec if result else None,
                report=result.report if result else None,
                model=result.model if result else "",
                base_url=result.base_url if result else "",
                input_tokens=usage.input_tokens if usage else 0,
                output_tokens=usage.output_tokens if usage else 0,
                calls=usage.calls if usage else 0,
                repair_rounds=result.repair_rounds if result else 0,
            )
        )
    except OSError:
        pass  # a read-only disk must not break the app


def _restore(saved: store.SavedRun) -> None:
    usage = Usage(
        input_tokens=saved.input_tokens,
        output_tokens=saved.output_tokens,
        calls=saved.calls,
    )
    result = None
    if saved.spec is not None:
        result = CompileResult(
            spec=saved.spec,
            report=saved.report or verify(saved.spec, saved.document),
            model=saved.model or settings().model,
            base_url=saved.base_url,
            usage=usage,
            repair_rounds=saved.repair_rounds,
            stage_reached="verify",
        )
    set_document(saved.document)
    st.session_state.update(
        title=saved.title,
        claims=saved.claims,
        decisions=saved.decisions,
        result=result,
        step=saved.step,
        question=saved.question,
        usage=usage,
        example=False,
        saved_document=None,
        restored=False,
    )


def discard_saved_run() -> None:
    store.clear()
    st.session_state["restored"] = False
    _clear_run(restore_draft=False)
    st.session_state["step"] = 0


def restore_last_run() -> None:
    """Load the stored run, but only because someone asked for it.

    The file can vanish between the offer and the click; absent is rendered
    as not-found everywhere, so a miss is a no-op rather than an error.
    """
    saved = store.load()
    if saved is None:
        return
    _restore(saved)
    st.session_state["restored_from_saved_at"] = saved.saved_at
    st.session_state["restored"] = True


def _saved_run_offer() -> None:
    """Offer the stored run back — explicitly, metadata first.

    Replaces the boot auto-restore: a stranger's document is never silently
    on screen. Renders nothing at all when the store is off, and nothing
    when there is no usable run — absent is absent everywhere.
    """
    if not store.enabled() or st.session_state.get("restored_from_saved_at"):
        return
    if (
        st.session_state["step"]
        or st.session_state["claims"] is not None
        or st.session_state["result"] is not None
        or st.session_state["example"]
    ):
        return  # the offer belongs to a fresh session, not one already at work
    saved = store.load()
    if saved is None or not saved.document.strip():
        return
    when = saved.saved_at.replace("T", " ")[:16] if saved.saved_at else "earlier"
    model = saved.model or "model not recorded"
    st.markdown(
        theme.notice(f"A saved run is on disk: {saved.title} — {model}, saved {when}."),
        **HTML,
    )
    left, right = st.columns(2)
    left.button(
        "Restore last run",
        type="primary",
        width="stretch",
        on_click=restore_last_run,
        key="restore-last-run",
    )
    right.button(
        "Discard it and start fresh",
        width="stretch",
        on_click=discard_saved_run,
        key="discard-saved-run",
    )


def go(step: int) -> None:
    st.session_state["step"] = step
    st.session_state.pop("error", None)
    _persist()


def request(action: str) -> None:
    st.session_state["pending"] = action
    st.session_state.pop("error", None)


def load_preset(name: str) -> None:
    _clear_run(restore_draft=False)
    example = registry.BY_LABEL[name]
    set_document(example.document)
    st.session_state.update(step=0, example_key=example.key)


def _clear_run(restore_draft: bool = True) -> None:
    """Drop the run. Optionally put the user's own draft back on screen.

    `restore_draft` is False when the user is replacing the document anyway —
    otherwise a stashed draft would overwrite what they just chose or typed.
    """
    if (
        restore_draft
        and st.session_state.get("example")
        and st.session_state.get("saved_document")
    ):
        set_document(st.session_state["saved_document"])
    st.session_state.update(
        claims=None,
        decisions=None,
        result=None,
        question=0,
        example=False,
        saved_document=None,
        usage=Usage(),
    )
    st.session_state.pop("error", None)


def start_over() -> None:
    _clear_run()
    st.session_state["step"] = 0


def document_changed() -> None:
    """Editing the document invalidates everything downstream of it."""
    st.session_state["document_text"] = st.session_state.get("document_input", "")
    _clear_run(restore_draft=False)
    st.session_state["step"] = 0


def next_question() -> None:
    st.session_state["question"] += 1


def previous_question() -> None:
    st.session_state["question"] = max(0, st.session_state["question"] - 1)


def show_reference(key: str = "") -> None:
    """Load an example and start it at step 2, so the flow is walked, not skipped.

    The user's own draft is stashed and restored when they leave the example.
    """
    example = registry.get(key or st.session_state.get("example_key", ""))
    spec = example.spec()
    stashed = document_text()
    set_document(example.document)
    st.session_state.update(
        saved_document=stashed,
        claims=spec.claims,
        decisions=spec.decisions,
        title=spec.name,
        result=CompileResult(
            spec=spec,
            report=verify(spec, example.document),
            model="hand-authored reference",
            stage_reached="verify",
        ),
        example=True,
        example_key=example.key,
        question=0,
        step=1,
    )
    st.session_state.pop("error", None)


# --------------------------------------------------------------------------- #
# Pipeline execution
# --------------------------------------------------------------------------- #


def run_pending() -> None:
    action = st.session_state.pop("pending", None)
    if not action:
        return

    client = build_client(overrides=provider_overrides())
    if client is None:
        st.session_state["error"] = _unconfigured_reason(settings())
        return
    st.session_state.pop("error", None)
    document = document_text()

    # One tally for the whole run. Reporting only the compile would tell the
    # user their run cost half what it actually did.
    spend: Usage = st.session_state["usage"]

    try:
        if action == "extract":
            with st.spinner("Reading your document…"):
                extraction = extract_claims(client, document, usage=spend)
            st.session_state.update(
                claims=extraction.claims,
                title=extraction.document_title,
                decisions=None,
                result=None,
                step=1,
            )

        elif action == "interrogate":
            with st.spinner("Looking for what the document leaves open…"):
                found = interrogate(
                    client, document, st.session_state["claims"], usage=spend
                )
            st.session_state.update(
                decisions=found.decisions, step=2, question=0
            )

        elif action in ("compile", "replan"):
            # "replan" keeps the requirements and re-runs only the planning
            # pass, so fixing a bad plan costs one call instead of four.
            existing = st.session_state.get("result")
            requirements = (
                existing.spec.requirements
                if action == "replan" and existing and existing.ok
                else None
            )
            label = (
                "Re-planning the tasks…"
                if requirements
                else "Writing requirements, planning tasks, verifying…"
            )
            with st.spinner(label):
                compiled = compile_spec(
                    client,
                    document,
                    st.session_state["claims"],
                    st.session_state["decisions"] or [],
                    title=st.session_state["title"],
                    requirements=requirements,
                )
            spend.merge(compiled.usage)
            st.session_state["result"] = compiled
            st.session_state["step"] = 3

        elif action.startswith("fix:"):
            index = int(action.split(":", 1)[1])
            result = st.session_state["result"]
            finding = result.report.findings[index]
            with st.spinner(f"Fixing {finding.location}…"):
                fixed = repair_finding(
                    client, result.spec, finding, document=document, usage=spend,
                )
            result.spec = fixed
            result.report = verify(fixed, document)
            result.repair_rounds += 1
    except ProviderError as exc:
        # The provider's own words are more useful than the class name.
        st.session_state["error"] = str(exc)
    except Exception as exc:
        st.session_state["error"] = f"{type(exc).__name__}: {exc}"

    _persist()


# --------------------------------------------------------------------------- #
# Screen 1 — Document
# --------------------------------------------------------------------------- #


def screen_document() -> None:
    st.markdown(
        theme.head(
            0,
            TOTAL_STEPS,
            "Paste your product document",
            "A PRD, discovery notes, a transcript — whatever you would "
            "otherwise hand straight to an agent.",
        ),
        **HTML,
    )

    st.text_area(
        "Document",
        key="document_input",
        height=300,
        label_visibility="collapsed",
        placeholder="Paste here…",
        on_change=document_changed,
    )

    columns = st.columns([1.2] + [1] * len(PRESETS))
    columns[0].markdown(
        '<div class="ob-quiet" style="padding-top:0.5rem">Or try an example:</div>',
        **HTML,
    )
    for column, name in zip(columns[1:], PRESETS, strict=True):
        column.button(
            name.split()[0], width="stretch", key=f"preset-{name}",
            on_click=load_preset, args=(name,), help=name,
        )

    readiness = assess_source(document_text())
    current = settings()
    _provider_panel(current)

    if readiness.compilable:
        st.markdown(theme.quiet(_estimate_line(current)), **HTML)

    st.markdown('<div style="height:1.2rem"></div>', **HTML)
    left, right = st.columns([2, 3])
    left.button(
        "Read the document →",
        type="primary",
        width="stretch",
        disabled=not readiness.compilable,
        on_click=request,
        args=("extract",),
    )
    example = registry.get(st.session_state.get("example_key", ""))
    right.button(
        f"Walk through the {example.short} example",
        width="stretch",
        on_click=show_reference,
        args=(example.key,),
        help=(
            "Steps through a finished example of this scenario, with no API calls."
        ),
    )


def _provider_panel(current: Settings) -> None:
    """Which model reads the document — one collapsed panel, not a screen.

    Closed it states the current choice; open it is the whole configuration.
    Nobody choosing the default should have to look at any of it.
    """
    spec = providers.spec_for(current.provider)
    st.markdown(theme.rule(), **HTML)

    with st.expander(f"Model · {current.describe}", expanded=not current.configured):
        st.selectbox(
            "Provider",
            [option.label for option in providers.PROVIDERS],
            key="provider_widget",
            on_change=provider_changed,
        )

        left, right = st.columns([1, 1])
        left.text_input(
            "Model",
            key="model_widget",
            on_change=_mirror,
            args=("model_name",),
            placeholder=spec.default_model or "model id",
        )
        if spec.kind == "openai":
            right.text_input(
                "Base URL",
                key="base_url_widget",
                on_change=_mirror,
                args=("base_url",),
                placeholder="https://…/v1",
            )

        if spec.needs_key or st.session_state.get("api_key"):
            st.text_input(
                "API key",
                key="api_key_widget",
                type="password",
                placeholder=spec.key_hint or "…",
                on_change=_mirror,
                args=("api_key",),
                help=(
                    f"Read from {spec.key_env} if it is set. Whatever you type "
                    "here is used for this session only and never written to disk."
                    if spec.key_env
                    else "Used for this session only, never written to disk."
                ),
            )

        if spec.note:
            st.markdown(theme.quiet(spec.note), **HTML)

        st.button("Check connection", key="check-connection", on_click=check_connection)
        status = st.session_state.get("connection")
        if status:
            (st.success if status[0] == "ok" else st.error)(status[1])


def _estimate_line(current: Settings) -> str:
    """What a compile will cost — or an honest refusal to guess."""
    if not current.model.strip():
        return "Choose a model above before compiling."
    estimate = pricing.estimate_compile(
        document_text(), current.model, base_url=current.base_url
    )
    if estimate.free:
        return (
            f"Free — {current.model} runs on your own hardware. "
            f"{estimate.detail}."
        )
    if not estimate.priced:
        return (
            f"{estimate.detail}. No published rate for {current.model} here, "
            "so no dollar figure — set SPEC_ENGINE_INPUT_RATE and "
            "SPEC_ENGINE_OUTPUT_RATE to price it."
        )
    return (
        f"A full compile is roughly {estimate.headline} — {estimate.detail}. "
        "An estimate, not a quote."
    )


# --------------------------------------------------------------------------- #
# Screen 2 — What I read
# --------------------------------------------------------------------------- #


def screen_claims(claims: List[SourceClaim]) -> None:
    document = document_text()
    grounded = [c for c in claims if _in_source(c.quote, document)]
    commitments = [c for c in claims if c.kind != "context"]

    st.markdown(
        theme.head(
            1,
            TOTAL_STEPS,
            f"I found {len(commitments)} commitments"
            if commitments
            else "I could not find anything to build",
            "Check nothing important is missing."
            if commitments
            else "Nothing in the document states what the system must do.",
        ),
        **HTML,
    )
    _example_banner()

    if not commitments:
        st.markdown(
            theme.notice(
                "Nothing here commits the build to anything — the document has "
                "context but no requirements or constraints. Add what the system "
                "must do, then read it again."
            ),
            **HTML,
        )
        for claim in claims:  # show what was read, so the gap is visible
            st.markdown(
                theme.claim(claim.quote, [claim.kind, f"line {claim.line}"], True),
                **HTML,
            )
        st.markdown(theme.rule(), **HTML)
        st.button("← Back to document", width="stretch", on_click=go, args=(0,))
        return

    if len(grounded) < len(claims):
        st.markdown(
            theme.notice(
                f"{len(claims) - len(grounded)} quote(s) could not be found in your "
                "document. Those are marked in red and will fail verification."
            ),
            **HTML,
        )

    if st.session_state.get("editing_claims") and not st.session_state["example"]:
        _edit_claims(claims, document)
    else:
        for claim in claims:
            ok = _in_source(claim.quote, document)
            meta = [claim.kind, f"line {claim.line}"]
            if not ok:
                meta.append("not found in your document")
            st.markdown(theme.claim(claim.quote, meta, grounded=ok), **HTML)
        if not st.session_state["example"]:
            st.button(
                "Something is wrong or missing — edit these",
                width="stretch",
                on_click=lambda: st.session_state.update(editing_claims=True),
            )

    _footer(
        back_label="← Back to document",
        back_step=0,
        primary_label="Find the open questions →",
        primary_action="interrogate",
        forward_step=2,
    )


def _edit_claims(claims: List[SourceClaim], document: str) -> None:
    """Correct, delete or add a claim.

    The screen asks the user to check nothing is missing; without this it had
    no way to act on the answer. Added rows are grounded-checked exactly like
    extracted ones — a quote typed by hand still has to exist in the document.
    """
    st.markdown(
        theme.quiet(
            "Edit a quote, change its kind, delete a row, or add one the reader "
            "missed. A quote must appear in your document word for word."
        ),
        **HTML,
    )
    edited = st.data_editor(
        [
            {"Quote": c.quote, "Kind": c.kind, "What it commits to": c.reading}
            for c in claims
        ],
        column_config={
            "Quote": st.column_config.TextColumn(width="large", required=True),
            "Kind": st.column_config.SelectboxColumn(
                options=["requirement", "constraint", "assumption", "context"],
                required=True,
            ),
            "What it commits to": st.column_config.TextColumn(width="medium"),
        },
        num_rows="dynamic",
        width="stretch",
        hide_index=True,
        key="claims_editor",
    )

    left, right = st.columns(2)
    left.button(
        "Save changes",
        type="primary",
        width="stretch",
        on_click=_save_claims,
        args=(edited, document),
    )
    right.button(
        "Cancel",
        width="stretch",
        on_click=lambda: st.session_state.update(editing_claims=False),
    )


def _save_claims(rows: List[dict], document: str) -> None:
    """Apply the edited rows. The rules live in `core.editing`, tested on their own."""
    st.session_state["claims"] = editing.rebuild_claims(rows, document)
    # Requirements traced to the old ids are no longer meaningful.
    st.session_state.update(editing_claims=False, decisions=None, result=None)
    _persist()


# --------------------------------------------------------------------------- #
# Screen 3 — Open questions, one at a time
# --------------------------------------------------------------------------- #


def screen_questions(decisions: List[OpenDecision]) -> None:
    if not decisions:
        st.markdown(
            theme.head(
                2, TOTAL_STEPS, "No open questions",
                "Your document settles everything the compiler needs.",
            ),
            **HTML,
        )
        _footer("← Back", 1, "Compile the specification →", "compile", forward_step=3)
        return

    index = min(st.session_state["question"], len(decisions))

    if index >= len(decisions):
        answered = sum(1 for d in decisions if d.answered)
        st.markdown(
            theme.head(
                2,
                TOTAL_STEPS,
                "That's everything",
                f"You answered {answered} of {len(decisions)}. "
                "The rest use the default shown.",
            ),
            **HTML,
        )
        _example_banner()
        for decision in decisions:
            with st.expander(
                f"{'✓' if decision.answered else '○'}  {decision.question}"
            ):
                st.markdown(
                    theme.answer_box(
                        "Answer" if decision.answered else "Assumed default",
                        decision.resolution,
                    ),
                    **HTML,
                )
        st.button(
            "← Review the questions again",
            on_click=lambda: st.session_state.update(question=0),
        )
        _footer(
            "← Back to claims", 1, "Compile the specification →", "compile",
            forward_step=3,
        )
        return

    decision = decisions[index]
    st.markdown(
        theme.head(
            2,
            TOTAL_STEPS,
            decision.question,
            decision.why_it_blocks,
            sub_step=f"Question {index + 1} of {len(decisions)}",
        ),
        **HTML,
    )
    _example_banner()

    options = list(decision.options) + ["Something else…"]
    # No pre-selection: an unanswered question must not answer itself.
    default_index: Optional[int] = None
    if decision.answer and decision.answer in decision.options:
        default_index = decision.options.index(decision.answer)
    elif decision.answer:
        default_index = len(options) - 1

    if decision.options:
        choice = st.radio(
            "Your answer",
            options,
            index=default_index,
            key=f"choice-{decision.id}",
            label_visibility="collapsed",
        )
        if choice is None:
            decision.answer = None
        elif choice == "Something else…":
            free_text = st.text_input(
                "Your answer",
                value=decision.answer if decision.answer not in decision.options else "",
                key=f"free-{decision.id}",
                placeholder="Type your answer",
            )
            decision.answer = free_text.strip() or None
        else:
            decision.answer = choice
    else:
        free_text = st.text_input(
            "Your answer",
            value=decision.answer or "",
            key=f"free-{decision.id}",
            placeholder="Type your answer, or skip to accept the default",
        )
        decision.answer = free_text.strip() or None

    st.markdown(
        theme.answer_box("If you skip this", decision.proposed_default), **HTML
    )

    st.markdown('<div style="height:0.8rem"></div>', **HTML)
    back, skip, forward = st.columns([1, 1, 1.4])
    if index == 0:
        back.button("← Back", width="stretch", on_click=go, args=(1,))
    else:
        back.button("← Previous", width="stretch", on_click=previous_question)
    skip.button(
        "Skip",
        width="stretch",
        on_click=lambda d=decision: (
            setattr(d, "answer", None),
            next_question(),
        ),
    )
    forward.button(
        "Next →", type="primary", width="stretch", on_click=next_question
    )


# --------------------------------------------------------------------------- #
# Screen 4 — The spec
# --------------------------------------------------------------------------- #


def screen_spec(result: CompileResult) -> None:
    if not result.ok:
        st.markdown(
            theme.head(
                3, TOTAL_STEPS, "The compile did not finish",
                f"It stopped at the {result.stage_reached} pass.",
            ),
            **HTML,
        )
        st.code(result.error or "unknown error", language="text")
        _footer("← Back to questions", 2, "Try again →", "compile")
        return

    spec, report = result.spec, result.report
    st.markdown(
        theme.head(
            3,
            TOTAL_STEPS,
            spec.name,
            "Verified against the example document."
            if st.session_state.get("example")
            else "Verified against your document.",
        ),
        **HTML,
    )

    _example_banner()

    # Overview first.
    counts = report.counts()
    st.markdown(
        theme.verdict(
            report.passed,
            "Ready for an agent to build" if report.passed
            else "Not ready — blocking defects remain",
            f"{counts['blocker']} blocking, {counts['major']} major, "
            f"{counts['minor']} minor findings."
            + (
                f" Repaired over {result.repair_rounds} round(s)."
                if result.repair_rounds
                else ""
            ),
        ),
        **HTML,
    )
    st.markdown(
        theme.stats(
            [
                (str(len(spec.requirements)), "requirements"),
                (str(spec.criteria_count()), "acceptance criteria"),
                (str(len(spec.tasks)), "tasks"),
                (f"{spec.total_hours():g}h", "estimated"),
                (str(len(spec.execution_waves())), "waves"),
            ]
        ),
        **HTML,
    )

    # Then zoom and filter.
    view = st.segmented_control(
        "View",
        ["Requirements", "Plan", "Checks", "Files"],
        key="view",
        label_visibility="collapsed",
    )
    st.markdown('<div style="height:0.6rem"></div>', **HTML)

    if view == "Requirements":
        _view_requirements(spec)
    elif view == "Plan":
        _view_plan(spec)
    elif view == "Checks":
        _view_checks(report)
    elif view == "Files":
        _view_files(spec, report)

    spend: Usage = st.session_state["usage"]
    if spend.calls and not st.session_state.get("example"):
        # The endpoint the run actually used, not whatever is selected now:
        # switching provider afterwards must not re-price finished work.
        where = result.base_url or settings().base_url
        spent = pricing.actual_cost(
            spend.input_tokens, spend.output_tokens, result.model, base_url=where
        )
        rounds = (
            f" {result.repair_rounds} repair round(s)."
            if result.repair_rounds
            else ""
        )
        tokens = (
            f"{spend.calls} calls, {spend.input_tokens:,} in / "
            f"{spend.output_tokens:,} out tokens."
        )
        # Three honest answers, and never a fabricated fourth: a price, "this
        # cost you nothing because it ran on your own hardware", or the token
        # counts alone when nothing here can price the model.
        if providers.is_local(where):
            line = f"This run was free — it ran on your own hardware. {tokens}"
        elif spent is not None:
            line = f"This run cost about ${spent:.2f} — {tokens}"
        else:
            line = f"This run used {tokens} No published rate for {result.model} here."
        st.markdown(theme.quiet(line + rounds), **HTML)

    st.markdown(theme.rule(), **HTML)
    left, right = st.columns([1, 1])
    left.button("← Back to questions", width="stretch", on_click=go, args=(2,))
    right.button(
        "Leave the example" if st.session_state.get("example") else "Start over",
        width="stretch",
        on_click=start_over,
    )


def _evidence_lines(spec: SpecDocument, requirement) -> List[str]:
    """The sentence behind each id, not the id on its own.

    `From CLM-001` makes the reader navigate back two screens to decode it.
    """
    claims = spec.claim_index()
    decisions = spec.decision_index()
    lines = []
    for ref in requirement.traces_to:
        if ref in claims:
            lines.append(f"{ref} — your words: “{claims[ref].quote}”")
        elif ref in decisions:
            decision = decisions[ref]
            source = "you answered" if decision.answered else "assumed default"
            lines.append(
                f"{ref} — {source}: {decision.resolution} "
                f"({decision.question})"
            )
        else:
            lines.append(ref)
    return lines


def _view_requirements(spec: SpecDocument) -> None:
    editable = not st.session_state.get("example")
    for requirement in spec.requirements:
        delivered = spec.tasks_for(requirement.id)
        with st.expander(f"{requirement.id} · {requirement.title}"):
            st.markdown(f"*{requirement.user_story}*")
            for criterion in requirement.acceptance_criteria:
                parsed = criterion.parsed
                st.markdown(
                    theme.ears_line(
                        criterion.statement,
                        weak=not parsed.conforms,
                        issues=[issue.message for issue in parsed.issues],
                    ),
                    **HTML,
                )

            for line in _evidence_lines(spec, requirement):
                st.markdown(theme.quiet("↑ " + line), **HTML)
            st.markdown(
                theme.quiet(
                    "↓ Built by "
                    + (", ".join(t.id for t in delivered) or "nothing yet")
                ),
                **HTML,
            )

            if editable:
                _edit_requirement(spec, requirement)


def _edit_requirement(spec: SpecDocument, requirement) -> None:
    """Rewrite a criterion in place, with the EARS grammar checked as you type."""
    key = f"edit-req-{requirement.id}"
    if not st.session_state.get(key):
        st.button(
            "Edit this requirement",
            key=f"btn-{key}",
            on_click=lambda k=key: st.session_state.update({k: True}),
        )
        return

    title = st.text_input("Title", value=requirement.title, key=f"{key}-title")
    for position, criterion in enumerate(requirement.acceptance_criteria):
        statement = st.text_area(
            f"Criterion {criterion.id}",
            value=criterion.statement,
            key=f"{key}-ac-{position}",
            height=80,
        )
        check = ears_parse(statement)
        if not check.parses:
            st.markdown(
                theme.quiet("✕ Not an EARS sentence. " + ears_templates_hint()),
                **HTML,
            )
        elif check.issues:
            st.markdown(
                theme.quiet(
                    "⚠ " + " · ".join(issue.message for issue in check.issues)
                ),
                **HTML,
            )
        else:
            st.markdown(theme.quiet("✓ Valid EARS, and testable."), **HTML)

    left, right = st.columns(2)
    left.button(
        "Save",
        key=f"{key}-save",
        type="primary",
        width="stretch",
        on_click=_save_requirement,
        args=(spec, requirement, key, title),
    )
    right.button(
        "Cancel",
        key=f"{key}-cancel",
        width="stretch",
        on_click=lambda k=key: st.session_state.update({k: False}),
    )


def _save_requirement(spec: SpecDocument, requirement, key: str, title: str) -> None:
    """Apply the edits, then re-verify — an edit is only as good as the gate."""
    statements = [
        st.session_state.get(f"{key}-ac-{position}", criterion.statement)
        for position, criterion in enumerate(requirement.acceptance_criteria)
    ]
    try:
        updated = editing.apply_criteria(requirement, statements, title)
    except Exception as exc:
        st.session_state["error"] = f"That edit is not valid: {exc}"
        return

    result = st.session_state["result"]
    result.spec.requirements = editing.replace_requirement(
        result.spec.requirements, updated
    )
    result.report = verify(result.spec, document_text())
    st.session_state[key] = False
    st.session_state.pop("error", None)
    _persist()


def _view_plan(spec: SpecDocument) -> None:
    st.markdown(theme.waves(spec.execution_waves()), **HTML)
    st.markdown('<div style="height:0.8rem"></div>', **HTML)
    titles = {r.id: r.title for r in spec.requirements}
    for task in spec.tasks:
        with st.expander(f"{task.id} · {task.title}"):
            satisfies = ", ".join(
                f"{ref} ({titles.get(ref, '?')})" for ref in task.satisfies
            )
            st.markdown(
                theme.quiet(
                    f"{task.layer} · {task.estimate_hours:g}h"
                    + (
                        " · after " + ", ".join(task.depends_on)
                        if task.depends_on
                        else ""
                    )
                ),
                **HTML,
            )
            st.markdown(theme.quiet("↑ Satisfies " + satisfies), **HTML)
            st.write(task.intent)
            st.code(task.verification, language="bash")

    if not st.session_state.get("example"):
        st.markdown(theme.rule(), **HTML)
        st.markdown(
            theme.quiet(
                "If the requirements are right but the plan is not, re-plan "
                "instead of recompiling — it costs one model call rather than two."
            ),
            **HTML,
        )
        st.button(
            "Re-plan the tasks",
            width="stretch",
            on_click=request,
            args=("replan",),
        )


def _view_checks(report: VerificationReport) -> None:
    coverage = report.coverage
    st.markdown(
        theme.meters(
            [
                ("Claims found in your document", coverage.claims_grounded, coverage.claims_total),
                ("Acceptance criteria that are testable", coverage.criteria_conformant, coverage.criteria_total),
                ("Requirements with a task", coverage.requirements_covered, coverage.requirements_total),
                ("Questions you answered", coverage.decisions_answered, coverage.decisions_total),
            ]
        ),
        **HTML,
    )
    if not report.findings:
        st.markdown(theme.quiet("No findings. Nothing to fix."), **HTML)
        return
    st.markdown('<div style="height:0.6rem"></div>', **HTML)
    fixable = not st.session_state.get("example")
    for index, finding in enumerate(report.findings):
        with st.expander(
            f"{finding.severity} · {finding.location} — {finding.message[:60]}"
        ):
            st.write(finding.message)
            st.markdown(theme.quiet(f"Fix: {finding.fix_hint}"), **HTML)
            # DEC findings are the user's call, not the model's — assuming a
            # default is a decision, not a defect a rewrite can clear.
            if fixable and not finding.code.startswith("DEC-"):
                st.button(
                    "Fix this",
                    key=f"fix-{index}",
                    on_click=request,
                    args=(f"fix:{index}",),
                    help="Sends this one defect back to the model and re-verifies.",
                )


def _view_files(spec: SpecDocument, report: VerificationReport) -> None:
    bundle = exporter.build_bundle(spec, report)
    st.markdown(
        theme.quiet(
            "What you hand the agent. Every requirement in these files traces "
            "to a sentence in your document, and handoff.txt is the instruction "
            "that says so."
        ),
        **HTML,
    )
    st.markdown('<div style="height:0.8rem"></div>', **HTML)
    columns = st.columns(3)
    for position, (filename, content) in enumerate(bundle.items()):
        columns[position % 3].download_button(
            filename,
            data=content,
            file_name=filename,
            mime="text/markdown" if filename.endswith(".md") else "text/plain",
            width="stretch",
            key=f"dl-{filename}",
        )
    st.markdown('<div style="height:0.8rem"></div>', **HTML)
    chosen = st.selectbox("Preview a file", list(bundle))
    st.code(
        bundle[chosen], language="json" if chosen.endswith(".json") else "markdown"
    )


# --------------------------------------------------------------------------- #
# Shared
# --------------------------------------------------------------------------- #


def _in_source(quote: str, document: str) -> bool:
    return " ".join(quote.split()).lower() in " ".join(document.split()).lower()


def ears_parse(statement: str):
    from core import ears

    return ears.parse(statement)


def ears_templates_hint() -> str:
    return "Start with THE, WHEN, WHILE, WHERE or IF, and use SHALL."


def _footer(
    back_label: str,
    back_step: int,
    primary_label: str,
    primary_action: str,
    forward_step: Optional[int] = None,
) -> None:
    """Back plus one primary action.

    In example mode the primary navigates to `forward_step` instead of calling
    the API — the walkthrough must work without a key.
    """
    st.markdown(theme.rule(), **HTML)
    left, right = st.columns([1, 1.3])
    left.button(back_label, width="stretch", on_click=go, args=(back_step,))
    if st.session_state.get("example") and forward_step is not None:
        right.button(
            primary_label, type="primary", width="stretch",
            on_click=go, args=(forward_step,),
        )
    else:
        right.button(
            primary_label, type="primary", width="stretch",
            on_click=request, args=(primary_action,),
        )


def _example_banner() -> None:
    if st.session_state.get("example"):
        example = registry.get(st.session_state.get("example_key", ""))
        st.markdown(
            theme.notice(f"The {example.short} example — not your document."),
            **HTML,
        )


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main() -> None:
    init_state()
    run_pending()

    current = settings()
    st.markdown(
        theme.strip(
            connected=current.configured,
            model=current.describe,
        ),
        **HTML,
    )

    if error := st.session_state.get("error"):
        st.error(error)

    _saved_run_offer()

    if st.session_state.get("restored"):
        st.markdown(
            theme.notice("Picked up where you left off — this run was restored."),
            **HTML,
        )
        st.button(
            "Discard it and start fresh",
            on_click=discard_saved_run,
            key="discard-restored",
        )

    # Returning to the document ends the example, whichever route got you here.
    # Without this, the reference text sits in the editor and the user's own
    # draft is one keystroke away from being lost.
    if st.session_state["step"] == 0 and st.session_state["example"]:
        _clear_run()

    step = st.session_state["step"]
    claims: Optional[List[SourceClaim]] = st.session_state["claims"]
    decisions: Optional[List[OpenDecision]] = st.session_state["decisions"]
    result: Optional[CompileResult] = st.session_state["result"]

    if step >= 3 and result is not None:
        screen_spec(result)
    elif step == 2 and decisions is not None:
        screen_questions(decisions)
    elif step == 1 and claims is not None:
        screen_claims(claims)
    else:
        screen_document()


if __name__ == "__main__":
    main()
