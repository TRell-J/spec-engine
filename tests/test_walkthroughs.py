"""Each preset must open its own walkthrough.

Presets and walkthroughs were declared separately once, so clicking `GenAI` and
then the example button silently swapped the document for the deal-desk one.
Both now come from `examples.registry`; these tests hold that together.
"""

import pytest

from examples import registry
from tests.test_app import boot, click, step_marker

pytestmark = pytest.mark.usefixtures("offline")


def rendered(app) -> str:
    return " ".join(m.value for m in app.markdown)


def heading(app) -> str:
    import html

    for block in app.markdown:
        if 'class="ob-title"' in block.value:
            return html.unescape(
                block.value.split('class="ob-title">')[1].split("</h1>")[0]
            )
    return ""


@pytest.fixture(params=registry.EXAMPLES, ids=lambda e: e.key)
def example(request):
    return request.param


def test_every_example_has_a_preset_button(monkeypatch, example):
    app = boot(monkeypatch)
    labels = " ".join(b.label for b in app.button)
    assert example.label.split()[0] in labels


def test_selecting_a_preset_loads_its_document(monkeypatch, example):
    app = boot(monkeypatch)
    click(app, example.label.split()[0])
    assert app.session_state["document_text"] == example.document
    assert app.session_state["example_key"] == example.key


def test_the_walkthrough_button_names_the_selected_scenario(monkeypatch, example):
    app = boot(monkeypatch)
    click(app, example.label.split()[0])
    labels = [b.label for b in app.button]
    assert f"Walk through the {example.short} example" in labels


def test_a_preset_opens_its_own_walkthrough(monkeypatch, example):
    """The regression: GenAI used to open the deal-desk example."""
    app = boot(monkeypatch)
    click(app, example.label.split()[0])
    click(app, "Walk through")
    assert "Step 2 of 4" in step_marker(app)
    assert app.session_state["document_text"] == example.document
    expected = example.spec()
    assert app.session_state["result"].spec.name == expected.name
    assert [c.id for c in app.session_state["claims"]] == [
        c.id for c in expected.claims
    ]


def test_the_banner_names_the_scenario(monkeypatch, example):
    app = boot(monkeypatch)
    click(app, example.label.split()[0])
    click(app, "Walk through")
    assert f"The {example.short} example" in rendered(app)


def test_each_walkthrough_completes_without_an_api_call(monkeypatch, example):
    app = boot(monkeypatch)  # no scripted responses: any call raises
    click(app, example.label.split()[0])
    click(app, "Walk through")
    click(app, "Find the open questions")
    for _ in range(len(example.spec().decisions)):
        click(app, "Next")
    click(app, "Compile the specification")
    assert "Step 4 of 4" in step_marker(app)
    assert "Ready for an agent to build" in rendered(app)
    assert app._fake.calls == [], "a walkthrough must not spend a token"


def test_each_walkthrough_shows_its_own_requirements(monkeypatch, example):
    app = boot(monkeypatch)
    click(app, example.label.split()[0])
    click(app, "Walk through")
    click(app, "Find the open questions")
    for _ in range(len(example.spec().decisions)):
        click(app, "Next")
    click(app, "Compile the specification")
    assert heading(app) == example.spec().name
    labels = " ".join(e.label for e in app.get("expander"))
    for requirement in example.spec().requirements:
        assert requirement.id in labels
        assert requirement.title in labels


def test_leaving_a_walkthrough_restores_the_users_own_draft(monkeypatch, example):
    draft = (
        "My own product notes. The service must accept uploads and tell the user "
        "what happened to each one, with a record of every rejection."
    )
    app = boot(monkeypatch)
    app.text_area[0].set_value(draft).run()
    click(app, "Walk through")
    click(app, "Back to document")
    assert app.session_state["document_text"] == draft


def test_switching_preset_switches_the_walkthrough(monkeypatch):
    """Two presets in one session must not cross-contaminate."""
    app = boot(monkeypatch)
    first, second = registry.EXAMPLES[0], registry.EXAMPLES[1]

    click(app, first.label.split()[0])
    click(app, "Walk through")
    assert app.session_state["result"].spec.name == first.spec().name

    click(app, "Back to document")
    click(app, second.label.split()[0])
    click(app, "Walk through")
    assert app.session_state["result"].spec.name == second.spec().name
