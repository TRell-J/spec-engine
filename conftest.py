"""Test configuration.

Makes the package importable from any working directory, and guarantees the
suite cannot reach the Anthropic API: a developer's real key in the environment
must not turn `pytest` into a billed, flaky, network-dependent run. The pipeline
is exercised through a scripted fake client instead.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


#: Everything that can point the compiler at a model. Derived from the
#: provider catalogue so a new preset cannot quietly escape the net.
def _provider_env() -> tuple:
    from core import providers

    return tuple(
        {spec.key_env for spec in providers.PROVIDERS if spec.key_env}
        | {
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_BASE_URL",
            "SPEC_ENGINE_PROVIDER",
            "SPEC_ENGINE_MODEL",
            "SPEC_ENGINE_BASE_URL",
            "SPEC_ENGINE_API_KEY",
            "SPEC_ENGINE_SCHEMA_MODE",
            "SPEC_ENGINE_MAX_TOKENS",
            "SPEC_ENGINE_INPUT_RATE",
            "SPEC_ENGINE_OUTPUT_RATE",
        }
    )


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    """No credentials, no developer configuration, a private run store per test.

    Two separate leaks to close. A key in the shell would turn `pytest` into a
    billed, networked run. And `app.py` calls `load_dotenv()`, so the first app
    test would otherwise pull the developer's own `.env` into `os.environ` for
    every test that follows it — which is how a real `.env` broke three tests
    that had nothing to do with the file.
    """
    for name in _provider_env():
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    monkeypatch.setenv("SPEC_ENGINE_STORE", str(tmp_path / "store"))


@pytest.fixture
def spec():
    from examples.reference import reference_spec

    return reference_spec()


@pytest.fixture
def document():
    from examples.reference import REFERENCE_DOCUMENT

    return REFERENCE_DOCUMENT


# --------------------------------------------------------------------------- #
# Fake Anthropic client, shared by the pipeline and app suites
# --------------------------------------------------------------------------- #


class FakeClient:
    """Scripted stand-in for `anthropic.Anthropic`."""

    def __init__(self, responses):
        from types import SimpleNamespace

        self._responses = list(responses)
        self.calls = []
        # Tests that need cache-read tokens set this before driving the app;
        # the default mirrors a provider that reports none.
        self.cache_read_per_call = 0
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        import json
        from types import SimpleNamespace

        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeClient ran out of scripted responses")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        text = item if isinstance(item, str) else json.dumps(item, default=str)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)],
            usage=SimpleNamespace(
                input_tokens=1000,
                output_tokens=2000,
                cache_read_input_tokens=self.cache_read_per_call,
            ),
        )


@pytest.fixture
def payloads():
    """The four pass payloads that reproduce the reference spec."""
    import json

    from examples.reference import reference_spec

    spec = reference_spec()

    def dump(model):
        return json.loads(model.model_dump_json())

    return {
        "extract": {
            "document_title": spec.name,
            "claims": [dump(c) for c in spec.claims],
        },
        "interrogate": {"decisions": [dump(d) for d in spec.decisions]},
        "specify": {"requirements": [dump(r) for r in spec.requirements]},
        "decompose": {
            "architecture_notes": spec.architecture_notes,
            "tasks": [dump(t) for t in spec.tasks],
            "out_of_scope": spec.out_of_scope,
            "risks": spec.risks,
        },
    }
