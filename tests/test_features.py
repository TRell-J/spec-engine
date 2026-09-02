"""Tests for the five improvements: editing, cost, persistence, re-runs, fixes.

Driven through the real app with a scripted client, like `test_app.py`.
"""

import json

import pytest
from streamlit.testing.v1 import AppTest

from core import store
from tests.test_app import APP, boot, click, step_marker

pytestmark = pytest.mark.usefixtures("offline")


@pytest.fixture
def walked(monkeypatch, payloads):
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


@pytest.fixture
def compiled(walked):
    click(walked, "Find the open questions")
    click(walked, "Skip")
    click(walked, "Skip")
    click(walked, "Compile the specification")
    return walked


def rendered(app) -> str:
    return " ".join(m.value for m in app.markdown)


# --------------------------------------------------------------------------- #
# 1 · Edit anything you can see
# --------------------------------------------------------------------------- #


def test_the_claims_screen_offers_an_editor(walked):
    assert [b for b in walked.button if "wrong or missing" in b.label]
    click(walked, "Something is wrong or missing")
    assert walked.session_state["editing_claims"] is True
    labels = [b.label for b in walked.button]
    assert "Save changes" in labels and "Cancel" in labels


def test_saving_edited_claims_invalidates_everything_downstream(walked):
    """Driven through the UI: the Save button must clear stale downstream state."""
    click(walked, "Find the open questions")
    assert walked.session_state["decisions"] is not None
    click(walked, "Back")
    click(walked, "Something is wrong or missing")
    click(walked, "Save changes")
    assert walked.session_state["decisions"] is None
    assert walked.session_state["result"] is None
    assert walked.session_state["editing_claims"] is False


def test_example_claims_are_not_editable(monkeypatch):
    app = boot(monkeypatch)
    click(app, "Walk through")
    assert not [b for b in app.button if "wrong or missing" in b.label]


def test_a_criterion_can_be_rewritten_through_the_ui(compiled):
    click(compiled, "Edit this requirement")
    editors = [t for t in compiled.text_area]
    assert editors, "no criterion editor rendered"
    editors[0].set_value(
        "WHEN a rep submits a quote, the quote service SHALL return HTTP 418"
    ).run()
    click(compiled, "Save")
    spec = compiled.session_state["result"].spec
    assert "418" in spec.requirements[0].acceptance_criteria[0].statement


def test_a_broken_criterion_edit_is_refused_in_the_ui(compiled):
    click(compiled, "Edit this requirement")
    before = compiled.session_state["result"].spec.requirements[0]
    original = before.acceptance_criteria[0].statement
    compiled.text_area[0].set_value("the system should be fast").run()
    click(compiled, "Save")
    spec = compiled.session_state["result"].spec
    assert spec.requirements[0].acceptance_criteria[0].statement == original
    assert compiled.error, "a refused edit must say why"


def test_the_editor_shows_live_ears_feedback(compiled):
    click(compiled, "Edit this requirement")
    compiled.text_area[0].set_value("the system should be fast").run()
    assert "Not an EARS sentence" in rendered(compiled)


def test_requirements_are_not_editable_in_the_example(monkeypatch):
    app = boot(monkeypatch)
    click(app, "Walk through")
    click(app, "Find the open questions")
    click(app, "Next")
    click(app, "Next")
    click(app, "Compile the specification")
    assert not [b for b in app.button if "Edit this requirement" in b.label]


# --------------------------------------------------------------------------- #
# 2 · Cost, before and after
# --------------------------------------------------------------------------- #


def test_cost_is_estimated_before_spending(monkeypatch):
    app = boot(monkeypatch)
    text = rendered(app)
    assert "A full compile is roughly" in text
    assert "model calls" in text
    assert "An estimate, not a quote" in text


def test_the_estimate_tracks_the_document(monkeypatch):
    app = boot(monkeypatch)
    small = rendered(app)
    app.text_area[0].set_value("word " * 4000).run()
    large = rendered(app)
    assert small != large, "the estimate should change with the document"


def test_actual_cost_is_reported_after_a_run(compiled):
    text = rendered(compiled)
    assert "This run cost about $" in text
    assert "4 calls" in text


def test_no_estimate_is_shown_for_an_uncompilable_document(monkeypatch):
    app = boot(monkeypatch)
    app.text_area[0].set_value("Too short.").run()
    assert "A full compile is roughly" not in rendered(app)


# --------------------------------------------------------------------------- #
# 2b · Persistence
# --------------------------------------------------------------------------- #


def test_a_compile_survives_a_new_session(compiled):
    assert store.exists(), "the compile was never saved"
    fresh = AppTest.from_file(APP, default_timeout=60)
    fresh.run()
    assert not fresh.exception, [str(e.value) for e in fresh.exception]
    assert "Step 4 of 4" in step_marker(fresh)
    assert fresh.session_state["result"].spec is not None
    assert "Picked up where you left off" in rendered(fresh)


def test_the_restored_run_keeps_the_verification_result(compiled):
    fresh = AppTest.from_file(APP, default_timeout=60)
    fresh.run()
    assert fresh.session_state["result"].report.passed
    assert "Ready for an agent to build" in rendered(fresh)


def test_a_restored_run_can_be_discarded(walked):
    fresh = AppTest.from_file(APP, default_timeout=60)
    fresh.run()
    assert "Picked up where you left off" in rendered(fresh)
    [b for b in fresh.button if "Discard" in b.label][0].click().run()
    assert "Step 1 of 4" in step_marker(fresh)
    assert fresh.session_state["claims"] is None
    assert not store.exists()


def test_claims_are_saved_before_a_spec_exists(walked):
    saved = store.load()
    assert saved is not None
    assert saved.claims and len(saved.claims) == 6
    assert saved.spec is None


def test_the_example_is_never_saved_as_your_work(monkeypatch):
    app = boot(monkeypatch)
    click(app, "Walk through")
    click(app, "Find the open questions")
    saved = store.load()
    assert saved is None or saved.title != "Deal Desk: discount approval routing"


def test_a_read_only_disk_does_not_break_the_app(monkeypatch, payloads):
    def explode(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(store, "save", explode)
    app = boot(monkeypatch, [payloads["extract"]])
    click(app, "Read the document")
    assert not app.exception
    assert "Step 2 of 4" in step_marker(app)


# --------------------------------------------------------------------------- #
# 3 · Targeted re-runs
# --------------------------------------------------------------------------- #


def test_replanning_costs_one_call_and_keeps_the_requirements(monkeypatch, payloads):
    app = boot(
        monkeypatch,
        [
            payloads["extract"],
            payloads["interrogate"],
            payloads["specify"],
            payloads["decompose"],
            payloads["decompose"],
        ],
    )
    click(app, "Read the document")
    click(app, "Find the open questions")
    click(app, "Skip")
    click(app, "Skip")
    click(app, "Compile the specification")
    calls_before = len(app._fake.calls)
    before = [r.id for r in app.session_state["result"].spec.requirements]

    app.segmented_control[0].set_value("Plan").run()
    click(app, "Re-plan the tasks")

    assert len(app._fake.calls) == calls_before + 1, "a re-plan should cost one call"
    assert [r.id for r in app.session_state["result"].spec.requirements] == before


def test_replan_is_not_offered_in_the_example(monkeypatch):
    app = boot(monkeypatch)
    click(app, "Walk through")
    click(app, "Find the open questions")
    click(app, "Next")
    click(app, "Next")
    click(app, "Compile the specification")
    app.segmented_control[0].set_value("Plan").run()
    assert not [b for b in app.button if "Re-plan" in b.label]


# --------------------------------------------------------------------------- #
# 4 · The sentence, not the id
# --------------------------------------------------------------------------- #


def test_a_requirement_shows_the_words_behind_each_claim(compiled):
    text = rendered(compiled)
    assert "your words:" in text
    assert "Any quote with a discount above 20%" in text


def test_a_decision_trace_says_whether_a_human_answered(compiled):
    assert "assumed default:" in rendered(compiled)


def test_an_answered_decision_is_labelled_as_yours(walked):
    click(walked, "Find the open questions")
    walked.radio[0].set_value("Any discount above 40% goes to the CFO").run()
    click(walked, "Next")
    click(walked, "Next")
    click(walked, "Compile the specification")
    assert "you answered:" in rendered(walked)


def test_a_task_names_the_requirement_it_satisfies(compiled):
    compiled.segmented_control[0].set_value("Plan").run()
    assert "Discount approval gate" in rendered(compiled)


# --------------------------------------------------------------------------- #
# 5 · Fix one finding
# --------------------------------------------------------------------------- #


def _broken_plan(payloads):
    broken = json.loads(json.dumps(payloads["decompose"]))
    broken["tasks"] = [t for t in broken["tasks"] if "REQ-004" not in t["satisfies"]]
    return broken


def test_a_single_finding_can_be_fixed(monkeypatch, payloads):
    repair = {
        "requirements": payloads["specify"]["requirements"],
        "tasks": payloads["decompose"]["tasks"],
    }
    app = boot(
        monkeypatch,
        [
            payloads["extract"],
            payloads["interrogate"],
            payloads["specify"],
            _broken_plan(payloads),
            RuntimeError("internal repair unavailable"),
            repair,  # the user-triggered fix is what lands
        ],
    )
    click(app, "Read the document")
    click(app, "Find the open questions")
    click(app, "Skip")
    click(app, "Skip")
    click(app, "Compile the specification")
    assert not app.session_state["result"].report.passed

    app.segmented_control[0].set_value("Checks").run()
    fixes = [b for b in app.button if b.label == "Fix this"]
    assert fixes, "a finding with no way to act on it"
    fixes[0].click().run()
    assert not app.exception, [str(e.value) for e in app.exception]
    assert app.session_state["result"].report.passed
    assert app.session_state["result"].spec.tasks_for("REQ-004")


def test_a_failed_fix_reports_and_keeps_the_spec(monkeypatch, payloads):
    app = boot(
        monkeypatch,
        [
            payloads["extract"],
            payloads["interrogate"],
            payloads["specify"],
            _broken_plan(payloads),
            RuntimeError("internal repair unavailable"),
            {  # the "fix" changes nothing
                "requirements": payloads["specify"]["requirements"],
                "tasks": _broken_plan(payloads)["tasks"],
            },
        ],
    )
    click(app, "Read the document")
    click(app, "Find the open questions")
    click(app, "Skip")
    click(app, "Skip")
    click(app, "Compile the specification")
    before = len(app.session_state["result"].spec.tasks)

    app.segmented_control[0].set_value("Checks").run()
    [b for b in app.button if b.label == "Fix this"][0].click().run()

    assert app.error, "a failed fix must say so"
    assert len(app.session_state["result"].spec.tasks) == before


def test_an_assumed_default_is_not_offered_as_a_fixable_defect(compiled):
    compiled.segmented_control[0].set_value("Checks").run()
    labels = [e.label for e in compiled.get("expander")]
    assert any("DEC-" in label for label in labels), "expected assumed-default findings"
    assert not [b for b in compiled.button if b.label == "Fix this"]


def test_fixes_are_not_offered_in_the_example(monkeypatch):
    app = boot(monkeypatch)
    click(app, "Walk through")
    click(app, "Find the open questions")
    click(app, "Next")
    click(app, "Next")
    click(app, "Compile the specification")
    app.segmented_control[0].set_value("Checks").run()
    assert not [b for b in app.button if b.label == "Fix this"]


# --------------------------------------------------------------------------- #
# The document must survive the whole flow
# --------------------------------------------------------------------------- #


def test_the_document_is_still_there_on_the_final_screen(compiled):
    """Regression: the document lived under a widget key, and Streamlit discards
    widget state for widgets it is not currently rendering. On step 4 it
    vanished, taking the grounding check and the saved run with it."""
    document = compiled.session_state["document_text"]
    assert document.strip(), "the document disappeared before the final screen"
    assert "discount above 20%" in document

    saved = store.load()
    assert saved is not None
    assert saved.document.strip() == document.strip()


def test_grounding_still_works_on_the_final_screen(compiled):
    from core.verifier import verify

    result = compiled.session_state["result"]
    report = verify(result.spec, compiled.session_state["document_text"])
    assert report.coverage.claims_total == 6
    assert report.coverage.grounding_rate == 100.0
