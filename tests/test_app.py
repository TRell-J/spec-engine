"""End-to-end tests for the wizard itself.

Every bug found in this app so far has been in the state machine, not the core,
so these drive the real screens through `AppTest` with a scripted client. No
network, no key, no tokens.
"""

import os

import pytest
from streamlit.testing.v1 import AppTest

from conftest import FakeClient
from core import store

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")

SHORT_DOC = "Build a tool."
MY_DRAFT = (
    "We need an approvals workflow. Reps submit quotes and the desk approves "
    "them. Finance needs an audit trail with the approver and a timestamp. "
    "Anything over twenty percent needs a second signature before it is sent."
)


def boot(monkeypatch, responses=None) -> AppTest:
    """Start the app with a scripted client in place of the real one.

    The key is a placeholder that never leaves this process: `build_client` is
    replaced, so nothing can be sent anywhere. It exists so the app considers
    itself configured, which is what a real user with a key would see.
    """
    from core import pipeline, providers

    client = FakeClient(responses or [])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")

    def fake_build_client(*args, **kwargs):
        # Mirror the real build_client: the returned provider carries the
        # session's resolved settings, so the pipeline stamps results with
        # the model the session actually selected.
        try:
            resolved = pipeline.resolve_settings(kwargs.get("overrides"))
        except providers.ProviderError:
            return None
        return providers.adapt(client, settings=resolved)

    monkeypatch.setattr(pipeline, "build_client", fake_build_client)
    app = AppTest.from_file(APP, default_timeout=60)
    app.run()
    assert not app.exception, [str(e.value) for e in app.exception]
    app._fake = client  # for assertions about what was sent
    return app


def click(app: AppTest, fragment: str) -> AppTest:
    matches = [b for b in app.button if fragment.lower() in b.label.lower()]
    assert matches, f"no button matching {fragment!r} in {[b.label for b in app.button]}"
    matches[0].click().run()
    assert not app.exception, [str(e.value) for e in app.exception]
    return app


def heading(app: AppTest) -> str:
    import html

    for block in app.markdown:
        if 'class="ob-title"' in block.value:
            raw = block.value.split('class="ob-title">')[1].split("</h1>")[0]
            return html.unescape(raw)
    return ""


def step_marker(app: AppTest) -> str:
    for block in app.markdown:
        if 'class="ob-step"' in block.value:
            return block.value.split('class="ob-step">')[1].split("</div>")[0]
    return ""


# --------------------------------------------------------------------------- #
# Screen 1
# --------------------------------------------------------------------------- #


def test_app_starts_on_step_one(monkeypatch):
    app = boot(monkeypatch)
    assert "Step 1 of 4" in step_marker(app)
    assert "Paste your product document" in heading(app)


def test_primary_action_is_disabled_for_an_unusable_document(monkeypatch):
    app = boot(monkeypatch)
    app.text_area[0].set_value(SHORT_DOC).run()
    primary = [b for b in app.button if "Read the document" in b.label][0]
    assert primary.disabled


def test_presets_replace_the_document(monkeypatch):
    app = boot(monkeypatch)
    app.text_area[0].set_value(MY_DRAFT).run()
    click(app, "GenAI")
    assert "GenAI search quality" in app.text_area[0].value


# --------------------------------------------------------------------------- #
# The happy path, end to end
# --------------------------------------------------------------------------- #


@pytest.fixture
def walked(monkeypatch, payloads):
    """An app driven through extract → interrogate → compile."""
    app = boot(
        monkeypatch,
        [
            payloads["extract"],
            payloads["interrogate"],
            payloads["specify"],
            payloads["decompose"],
        ],
    )
    click(app, "Read the document")
    return app


def test_extract_moves_to_the_claims_screen(walked):
    assert "Step 2 of 4" in step_marker(walked)
    assert "I found" in heading(walked)


def test_claims_screen_shows_every_quote(walked, payloads):
    rendered = " ".join(m.value for m in walked.markdown)
    for claim in payloads["extract"]["claims"]:
        assert claim["quote"][:40] in rendered


def test_interrogate_moves_to_one_question_per_screen(walked):
    click(walked, "Find the open questions")
    assert "Step 3 of 4" in step_marker(walked)
    assert "Question 1 of 2" in step_marker(walked)


def test_full_walk_reaches_a_verified_spec(walked):
    click(walked, "Find the open questions")
    click(walked, "Next")
    click(walked, "Next")
    assert "That's everything" in heading(walked)
    click(walked, "Compile the specification")
    assert "Step 4 of 4" in step_marker(walked)
    rendered = " ".join(m.value for m in walked.markdown)
    assert "Ready for an agent to build" in rendered
    assert "5</b> requirements" in rendered


def test_compile_sends_the_answers_to_the_model(walked):
    click(walked, "Find the open questions")
    walked.radio[0].set_value("Any discount above 40% goes to the CFO").run()
    click(walked, "Next")
    click(walked, "Next")
    click(walked, "Compile the specification")
    specify_prompt = walked._fake.calls[2]["messages"][0]["content"]
    assert "Any discount above 40% goes to the CFO" in specify_prompt


def test_skipping_records_the_default_as_assumed(walked):
    click(walked, "Find the open questions")
    click(walked, "Skip")
    click(walked, "Skip")
    click(walked, "Compile the specification")
    specify_prompt = walked._fake.calls[2]["messages"][0]["content"]
    assert "assumed default, nobody answered" in specify_prompt


def test_skip_survives_navigating_back(walked):
    """A skipped question must not be re-answered by a stale radio widget."""
    click(walked, "Find the open questions")
    walked.radio[0].set_value("Deal desk approves all discounts").run()
    click(walked, "Next")
    click(walked, "Previous")
    click(walked, "Skip")
    click(walked, "Next")
    click(walked, "Compile the specification")
    specify_prompt = walked._fake.calls[2]["messages"][0]["content"]
    assert "DEC-001" in specify_prompt
    decision_line = [
        line for line in specify_prompt.splitlines() if line.startswith("- DEC-001")
    ][0]
    assert "assumed default, nobody answered" in decision_line


def test_duplicate_decision_ids_are_renumbered_before_the_questions_render(
    monkeypatch, payloads
):
    """The model sometimes hands back two DEC-001s. Widget keys are
    `choice-{id}`, so a duplicate makes question 2's radio silently restore
    question 1's answer with zero user input. The renumber happens before the
    first render, so every question gets its own key."""
    decisions = payloads["interrogate"]["decisions"]
    decisions[1] = dict(decisions[1], id=decisions[0]["id"])  # the slip
    app = boot(
        monkeypatch,
        [
            payloads["extract"],
            {"decisions": decisions},
            payloads["specify"],
            payloads["decompose"],
        ],
    )
    click(app, "Read the document")
    click(app, "Find the open questions")

    # Distinct widget keys: nothing downstream can collide on `choice-{id}`.
    assert app.radio[0].key == "choice-DEC-001"
    app.radio[0].set_value("Deal desk approves all discounts").run()
    click(app, "Next")
    assert app.radio[0].key == "choice-DEC-002"

    # The user answered only question 1 — question 2 must not self-answer
    # from question 1's restored widget state.
    answers = [d.answer for d in app.session_state["decisions"]]
    assert answers == ["Deal desk approves all discounts", None]

    # The persisted run shows the same.
    click(app, "Next")
    click(app, "Compile the specification")
    saved = store.load()
    assert [d.id for d in saved.decisions] == ["DEC-001", "DEC-002"]
    assert [d.answer for d in saved.decisions] == [
        "Deal desk approves all discounts",
        None,
    ]


# --------------------------------------------------------------------------- #
# Navigation
# --------------------------------------------------------------------------- #


def test_back_from_claims_returns_to_the_document(walked):
    click(walked, "Back to document")
    assert "Step 1 of 4" in step_marker(walked)
    assert walked.text_area[0].value


def test_back_from_questions_returns_to_claims(walked):
    click(walked, "Find the open questions")
    click(walked, "Back")
    assert "Step 2 of 4" in step_marker(walked)


def test_editing_the_document_invalidates_the_run(walked):
    click(walked, "Back to document")
    walked.text_area[0].set_value(MY_DRAFT).run()
    assert "Step 1 of 4" in step_marker(walked)
    # The claims from the previous document must not survive.
    assert [b for b in walked.button if "Read the document" in b.label]
    assert not [b for b in walked.button if "open questions" in b.label]


# --------------------------------------------------------------------------- #
# Failure handling
# --------------------------------------------------------------------------- #


def test_a_failing_pass_surfaces_the_error(monkeypatch, payloads):
    app = boot(monkeypatch, [RuntimeError("overloaded")])
    click(app, "Read the document")
    assert app.error, "the failure was swallowed"
    assert "overloaded" in app.error[0].value


def test_the_error_clears_on_the_next_successful_action(monkeypatch, payloads):
    app = boot(monkeypatch, [RuntimeError("overloaded"), payloads["extract"]])
    click(app, "Read the document")
    assert app.error
    click(app, "Read the document")
    assert not app.error, "a stale error survived a successful run"
    assert "Step 2 of 4" in step_marker(app)


def test_missing_key_reports_instead_of_fabricating(monkeypatch):
    from core import pipeline

    monkeypatch.setattr(pipeline, "build_client", lambda *a, **k: None)
    app = AppTest.from_file(APP, default_timeout=60)
    app.run()
    click(app, "Read the document")
    assert app.error and "API key" in app.error[0].value
    assert "Step 1 of 4" in step_marker(app)


def test_a_failed_compile_stays_recoverable(monkeypatch, payloads):
    app = boot(
        monkeypatch,
        [
            payloads["extract"],
            payloads["interrogate"],
            RuntimeError("specify exploded"),
        ],
    )
    click(app, "Read the document")
    click(app, "Find the open questions")
    click(app, "Skip")
    click(app, "Skip")
    click(app, "Compile the specification")
    rendered = " ".join(m.value for m in app.markdown)
    assert "did not finish" in rendered or app.error
    assert [b for b in app.button if "Back" in b.label], "no way back from a failure"


# --------------------------------------------------------------------------- #
# The example walkthrough
# --------------------------------------------------------------------------- #


def test_example_starts_at_step_two_and_never_calls_the_api(monkeypatch):
    app = boot(monkeypatch)  # no scripted responses: any call raises
    app.text_area[0].set_value(MY_DRAFT).run()
    click(app, "Walk through")
    assert "Step 2 of 4" in step_marker(app)
    click(app, "Find the open questions")
    click(app, "Next")
    click(app, "Next")
    click(app, "Compile the specification")
    assert "Step 4 of 4" in step_marker(app)
    assert app._fake.calls == [], "the example must not spend a token"


def test_example_is_labelled_on_every_screen(monkeypatch):
    app = boot(monkeypatch)
    click(app, "Walk through")
    for _ in range(3):
        rendered = " ".join(m.value for m in app.markdown)
        assert "not your document" in rendered
        forward = [
            b for b in app.button
            if "open questions" in b.label or b.label.startswith("Next")
        ]
        if not forward:
            break
        forward[0].click().run()


def test_leaving_the_example_restores_the_users_draft(monkeypatch):
    app = boot(monkeypatch)
    app.text_area[0].set_value(MY_DRAFT).run()
    click(app, "Walk through")
    click(app, "Find the open questions")
    click(app, "Next")
    click(app, "Next")
    click(app, "Compile the specification")
    click(app, "Leave the example")
    assert "Step 1 of 4" in step_marker(app)
    assert app.text_area[0].value == MY_DRAFT


def test_going_back_to_the_document_from_an_example_keeps_the_draft(monkeypatch):
    """The example must never eat the user's own text."""
    app = boot(monkeypatch)
    app.text_area[0].set_value(MY_DRAFT).run()
    click(app, "Walk through")
    click(app, "Back to document")
    assert app.text_area[0].value == MY_DRAFT


# --------------------------------------------------------------------------- #
# The spec screen
# --------------------------------------------------------------------------- #


@pytest.fixture
def compiled(walked):
    click(walked, "Find the open questions")
    click(walked, "Skip")
    click(walked, "Skip")
    click(walked, "Compile the specification")
    return walked


def test_every_view_renders(compiled):
    for view in ["Plan", "Checks", "Files", "Requirements"]:
        compiled.segmented_control[0].set_value(view).run()
        assert not compiled.exception, [str(e.value) for e in compiled.exception]


def test_files_view_offers_every_artifact(compiled):
    compiled.segmented_control[0].set_value("Files").run()
    names = {b.label for b in compiled.get("download_button")}
    assert names == {
        "requirements.md",
        "design.md",
        "tasks.md",
        "traceability.md",
        "spec.json",
        "plan.mmd",
        "handoff.txt",
    }


def test_the_files_screen_says_what_the_files_are_for(compiled):
    """The seam is the point: this is where the tool ends and the agent starts."""
    compiled.segmented_control[0].set_value("Files").run()
    rendered = " ".join(m.value for m in compiled.markdown)
    assert "What you hand the agent" in rendered
    assert "handoff.txt" in rendered


def test_checks_view_reports_the_coverage(compiled):
    compiled.segmented_control[0].set_value("Checks").run()
    rendered = " ".join(m.value for m in compiled.markdown)
    assert "Claims found in your document" in rendered
    assert "Requirements with a task" in rendered


def test_start_over_returns_to_an_empty_run(compiled):
    click(compiled, "Start over")
    assert "Step 1 of 4" in step_marker(compiled)
    assert [b for b in compiled.button if "Read the document" in b.label]


# --------------------------------------------------------------------------- #
# Edge cases
# --------------------------------------------------------------------------- #


def test_a_stale_error_does_not_follow_you_around(monkeypatch, payloads):
    """An error from one action must not haunt an unrelated screen."""
    app = boot(monkeypatch, [RuntimeError("overloaded")])
    click(app, "Read the document")
    assert app.error
    click(app, "GenAI")  # pure navigation, no API call
    assert not app.error, "a stale error survived a navigation click"


def test_extraction_that_finds_nothing_does_not_pretend_otherwise(monkeypatch):
    app = boot(monkeypatch, [{"document_title": "Empty", "claims": []}])
    click(app, "Read the document")
    rendered = " ".join(m.value for m in app.markdown)
    assert "0 commitments" in rendered or "nothing" in rendered.lower()
    # It must not offer to compile a spec grounded in no evidence.
    assert not [b for b in app.button if "Compile the specification" in b.label]


def test_an_ungrounded_quote_is_flagged_on_the_claims_screen(monkeypatch, payloads):
    fabricated = {
        "document_title": "Deal desk",
        "claims": [
            {
                "id": "CLM-001",
                "quote": "The product must settle payments on a blockchain.",
                "line": 1,
                "kind": "requirement",
                "reading": "Invented requirement.",
            }
        ],
    }
    app = boot(monkeypatch, [fabricated])
    click(app, "Read the document")
    rendered = " ".join(m.value for m in app.markdown)
    assert "not found in your document" in rendered
    assert "is-ungrounded" in rendered


def test_no_open_questions_still_reaches_a_spec(monkeypatch, payloads):
    app = boot(
        monkeypatch,
        [
            payloads["extract"],
            {"decisions": []},
            payloads["specify"],
            payloads["decompose"],
        ],
    )
    click(app, "Read the document")
    click(app, "Find the open questions")
    assert "No open questions" in heading(app)
    click(app, "Compile the specification")
    assert "Step 4 of 4" in step_marker(app)


def test_a_failing_verification_is_shown_as_failing(monkeypatch, payloads):
    """Drop the task that covers REQ-004 so the gate must reject the spec."""
    import json

    broken = json.loads(json.dumps(payloads["decompose"]))
    broken["tasks"] = [
        t for t in broken["tasks"] if "REQ-004" not in t["satisfies"]
    ]
    app = boot(
        monkeypatch,
        [
            payloads["extract"],
            payloads["interrogate"],
            payloads["specify"],
            broken,
            payloads["specify"],  # repair attempt returns the same defect
            broken,
        ],
    )
    click(app, "Read the document")
    click(app, "Find the open questions")
    click(app, "Skip")
    click(app, "Skip")
    click(app, "Compile the specification")
    rendered = " ".join(m.value for m in app.markdown)
    assert "Not ready" in rendered
    app.segmented_control[0].set_value("Checks").run()
    findings = " ".join(m.value for m in app.markdown)
    assert "No task delivers this requirement." in findings
    labels = " ".join(e.label for e in app.get("expander"))
    assert "REQ-004" in labels, "the finding does not say which requirement it is"


def test_recompiling_after_changing_an_answer_uses_the_new_answer(
    monkeypatch, payloads
):
    app = boot(
        monkeypatch,
        [
            payloads["extract"],
            payloads["interrogate"],
            payloads["specify"],
            payloads["decompose"],
            payloads["specify"],
            payloads["decompose"],
        ],
    )
    click(app, "Read the document")
    click(app, "Find the open questions")
    click(app, "Skip")
    click(app, "Skip")
    click(app, "Compile the specification")
    click(app, "Back to questions")
    click(app, "Review the questions again")
    app.radio[0].set_value("Any discount above 40% goes to the CFO").run()
    click(app, "Next")
    click(app, "Next")
    click(app, "Compile the specification")
    latest_specify = app._fake.calls[-2]["messages"][0]["content"]
    assert "Any discount above 40% goes to the CFO" in latest_specify


def test_zero_claims_blocks_the_flow_with_a_way_back(monkeypatch):
    app = boot(monkeypatch, [{"document_title": "Empty", "claims": []}])
    click(app, "Read the document")
    assert "could not find anything" in heading(app)
    assert not [b for b in app.button if "open questions" in b.label]
    assert [b for b in app.button if "Back to document" in b.label]


def test_a_document_of_only_context_cannot_proceed(monkeypatch):
    """Context is not a commitment; there is nothing to build from it."""
    context_only = {
        "document_title": "Background",
        "claims": [
            {
                "id": "CLM-001",
                "quote": "Reps currently wait on Slack for discount approval",
                "line": 3,
                "kind": "context",
                "reading": "Today's process is a Slack thread.",
            }
        ],
    }
    app = boot(monkeypatch, [context_only])
    click(app, "Read the document")
    assert "could not find anything" in heading(app)
    assert not [b for b in app.button if "open questions" in b.label]


def test_every_preset_gets_a_button(monkeypatch):
    """Guards the column/preset zip: adding a preset must not hide it."""
    import app as app_module

    app = boot(monkeypatch)
    labels = " ".join(b.label for b in app.button)
    for name in app_module.PRESETS:
        assert name.split()[0] in labels, f"{name} has no button"


def test_a_failed_compile_error_does_not_follow_you_into_the_example(monkeypatch):
    """Found while using the app: the key error stayed up during the example."""
    from core import pipeline

    monkeypatch.setattr(pipeline, "build_client", lambda *a, **k: None)
    app = AppTest.from_file(APP, default_timeout=60)
    app.run()
    click(app, "Read the document")
    assert app.error
    click(app, "Walk through")
    assert not app.error, "an API-key error survived into the example"


def test_the_example_never_claims_to_have_read_your_document(monkeypatch):
    app = boot(monkeypatch)
    click(app, "Walk through")
    click(app, "Find the open questions")
    click(app, "Next")
    click(app, "Next")
    click(app, "Compile the specification")
    rendered = " ".join(m.value for m in app.markdown)
    assert "Verified against your document" not in rendered
    assert "example document" in rendered


# --------------------------------------------------------------------------- #
# The usage line: cache reads and per-model pricing                         #
# --------------------------------------------------------------------------- #


def _cost_line(app):
    """The rendered cost sentence, or empty when nothing was priced."""
    for block in app.markdown:
        if "This run cost" in block.value:
            return block.value
    return ""


def test_cached_reads_render_and_price_as_their_own_segment(monkeypatch, payloads):
    """Cache reads show as their own segment and pay the 0.1x cache rate."""
    from core import pricing

    app = boot(
        monkeypatch,
        [
            payloads["extract"],
            payloads["interrogate"],
            payloads["specify"],
            payloads["decompose"],
        ],
    )
    app._fake.cache_read_per_call = 50_000
    click(app, "Read the document")
    click(app, "Find the open questions")
    click(app, "Skip")
    click(app, "Skip")
    click(app, "Compile the specification")

    spend = app.session_state["usage"]
    assert spend.cache_read_input_tokens == 200_000  # four passes x 50,000
    rate = pricing.rate_for(app.session_state["result"].model)
    expected = (
        spend.input_tokens * rate[0] / 1e6
        + spend.cache_read_input_tokens * rate[0] * 0.1 / 1e6
        + spend.output_tokens * rate[1] / 1e6
    )
    line = _cost_line(app)
    assert f"${expected:.2f}" in line
    # The cached figure is its own segment, never folded into the input count.
    assert (
        f"{spend.input_tokens:,} in / {spend.cache_read_input_tokens:,} cached" in line
    )


def test_zero_cache_run_renders_the_original_line(compiled):
    """No cache reads, no model switch: the sentence keeps its exact shape."""
    from core import pricing

    spend = compiled.session_state["usage"]
    rate = pricing.rate_for(compiled.session_state["result"].model)
    expected = (
        spend.input_tokens * rate[0] / 1e6 + spend.output_tokens * rate[1] / 1e6
    )
    line = _cost_line(compiled)
    assert "cached" not in line
    assert (
        f"This run cost about ${expected:.2f} — {spend.calls} calls, "
        f"{spend.input_tokens:,} in / {spend.output_tokens:,} out tokens."
    ) in line


def test_two_models_price_each_segment_at_its_own_rate(monkeypatch, payloads):
    """Extract under one model, switch the model, finish the run: the first
    segment keeps its own rate instead of being re-priced at the new one."""
    from core import pricing

    app = boot(
        monkeypatch,
        [
            payloads["extract"],  # first extract, under the default model
            payloads["extract"],  # re-extract after the model switch
            payloads["interrogate"],
            payloads["specify"],
            payloads["decompose"],
        ],
    )
    click(app, "Read the document")
    click(app, "Back to document")

    model_widget = [t for t in app.text_input if t.key == "model_widget"][0]
    model_widget.set_value("claude-sonnet-5").run()
    assert not app.exception, [str(e.value) for e in app.exception]
    history = (
        app.session_state["usage_history"]
        if "usage_history" in app.session_state
        else []
    )
    assert history and history[0]["model"] == "claude-opus-5", (
        "the first tally was not frozen when the model changed"
    )
    assert history[0]["usage"].calls == 1

    click(app, "Read the document")
    click(app, "Find the open questions")
    click(app, "Skip")
    click(app, "Skip")
    click(app, "Compile the specification")

    second = app.session_state["usage"]
    assert second.calls == 4
    rate_a = pricing.rate_for("claude-opus-5")
    rate_b = pricing.rate_for("claude-sonnet-5")
    expected = (
        history[0]["usage"].input_tokens * rate_a[0] / 1e6
        + history[0]["usage"].output_tokens * rate_a[1] / 1e6
        + second.input_tokens * rate_b[0] / 1e6
        + second.output_tokens * rate_b[1] / 1e6
    )
    line = _cost_line(app)
    assert f"${expected:.2f}" in line
    # And the session's tokens were not silently re-priced at the new model.
    wrong = (
        (history[0]["usage"].input_tokens + second.input_tokens) * rate_b[0] / 1e6
        + (history[0]["usage"].output_tokens + second.output_tokens) * rate_b[1] / 1e6
    )
    assert f"${wrong:.2f}" not in line



# --------------------------------------------------------------------------- #
# The stored run is offered, never assumed
# --------------------------------------------------------------------------- #

RESTORED_DOC = "Restored run sentinel — the saved draft text."


def seed_run(**overrides) -> store.SavedRun:
    """A minimal saved run in the per-test store the offline fixture points at."""
    run = store.SavedRun(
        title="Nightly export",
        document=RESTORED_DOC,
        step=1,
        model="claude-opus-5",
        **overrides,
    )
    store.save(run)
    return run


def test_a_fresh_session_does_not_auto_restore(monkeypatch):
    """The old boot auto-restore handed a stranger's run to whoever came next."""
    seed_run()
    app = boot(monkeypatch)
    assert "Step 1 of 4" in step_marker(app)
    assert app.session_state["step"] == 0
    assert app.session_state["claims"] is None
    assert app.session_state["result"] is None
    assert RESTORED_DOC not in app.text_area[0].value


def test_a_saved_run_is_offered_metadata_first(monkeypatch):
    saved = seed_run()
    app = boot(monkeypatch)
    rendered = " ".join(m.value for m in app.markdown)
    assert "Nightly export" in rendered
    assert "claude-opus-5" in rendered
    assert saved.saved_at.replace("T", " ")[:16] in rendered
    assert [b for b in app.button if "Restore last run" in b.label]
    # Metadata first: the run itself is not on screen until someone asks.
    assert RESTORED_DOC not in app.text_area[0].value


def test_restoring_loads_the_run_behind_the_button(monkeypatch):
    seed_run()
    app = boot(monkeypatch)
    click(app, "Restore last run")
    assert app.text_area[0].value == RESTORED_DOC
    assert app.session_state["step"] == 1
    assert app.session_state["restored"] is True
    assert not [b for b in app.button if "Restore last run" in b.label]
    assert [b for b in app.button if "Discard" in b.label]


def test_discarding_the_offer_clears_the_stored_run(monkeypatch):
    seed_run()
    app = boot(monkeypatch)
    click(app, "Discard it and start fresh")
    assert not store.exists()
    assert "Step 1 of 4" in step_marker(app)
    assert not [b for b in app.button if "Restore last run" in b.label]


def test_an_absent_run_is_just_not_there(monkeypatch):
    """Uniformly not-found: no run, no offer, no special-cased message."""
    app = boot(monkeypatch)
    assert not [b for b in app.button if "Restore last run" in b.label]
    assert "saved run" not in " ".join(m.value for m in app.markdown).lower()


def test_a_disabled_store_renders_nothing_even_over_a_saved_run(monkeypatch):
    seed_run()
    monkeypatch.setenv("SPEC_ENGINE_STORE", "off")
    app = boot(monkeypatch)
    assert not [b for b in app.button if "Restore last run" in b.label]
    assert "Nightly export" not in " ".join(m.value for m in app.markdown)


# --------------------------------------------------------------------------- #
# Server keys are opt-in
# --------------------------------------------------------------------------- #


def test_the_billing_notice_renders_when_a_server_key_is_in_use(monkeypatch):
    monkeypatch.setenv("SPEC_ENGINE_ALLOW_SERVER_KEY", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    app = AppTest.from_file(APP, default_timeout=60)
    app.run()
    assert not app.exception, [str(e.value) for e in app.exception]
    assert "billed to the operator" in " ".join(m.value for m in app.markdown)


def test_no_billing_notice_while_the_flag_is_off(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    app = AppTest.from_file(APP, default_timeout=60)
    app.run()
    assert not app.exception, [str(e.value) for e in app.exception]
    rendered = " ".join(m.value for m in app.markdown)
    assert "billed to the operator" not in rendered


def test_the_key_state_stays_visible_when_configured(monkeypatch):
    """The Model panel no longer collapses once the app is configured: the
    key field — empty, and where a key would come from — stays on screen."""
    monkeypatch.setenv("SPEC_ENGINE_ALLOW_SERVER_KEY", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    app = AppTest.from_file(APP, default_timeout=60)
    app.run()
    assert not app.exception, [str(e.value) for e in app.exception]
    assert any("Model ·" in e.label for e in app.expander)
    assert any(t.key == "api_key_widget" for t in app.text_input)

