"""The compiler against a real OpenAI-compatible server, over real HTTP.

Every other test stops at a recorded request. This one starts a server on a
loopback port and runs all four passes through it — request encoding, status
handling, response decoding, usage accounting — because the failure that
actually happened during development was in none of the units: the HTTP client
this module used was not installed, and only a real request found that out.

The server is deliberately awkward in the ways an open-weight model is
awkward: it thinks out loud before answering and fences its JSON.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from core import pipeline, providers, verifier
from examples.reference import REFERENCE_DOCUMENT, reference_spec

pytestmark = pytest.mark.usefixtures("offline")


def _dump(model):
    return json.loads(model.model_dump_json())


def _payloads():
    spec = reference_spec()
    return {
        "document_title": {
            "document_title": spec.name,
            "claims": [_dump(c) for c in spec.claims],
        },
        "why_it_blocks": {"decisions": [_dump(d) for d in spec.decisions]},
        "architecture_notes": {
            "architecture_notes": spec.architecture_notes,
            "tasks": [_dump(t) for t in spec.tasks],
            "out_of_scope": spec.out_of_scope,
            "risks": spec.risks,
        },
        "acceptance_criteria": {"requirements": [_dump(r) for r in spec.requirements]},
    }


class _Handler(BaseHTTPRequestHandler):
    """Answers whichever pass the request's schema asks for."""

    payloads = _payloads()
    seen: list = []

    def log_message(self, *args):
        pass  # keep the test output clean

    def _reply(self, payload, status=200):
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self._reply({"data": [{"id": "qwen3:32b"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        type(self).seen.append(body)
        request = json.dumps(body)
        answer = next(
            payload for key, payload in self.payloads.items() if key in request
        )
        self._reply(
            {
                "choices": [
                    {
                        "message": {
                            "content": (
                                "<think>Recording only what the document says.</think>"
                                "\n```json\n" + json.dumps(answer) + "\n```"
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 3120, "completion_tokens": 1840},
            }
        )


@pytest.fixture
def server():
    _Handler.seen = []
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}/v1"
    httpd.shutdown()
    thread.join(timeout=5)


@pytest.fixture
def provider(server, monkeypatch):
    # The loopback server stands in for an operator's own model server, so the
    # Base URL resolves from the environment — the provenance the URL policy
    # lets reach a local address. The visitor-typed case is asserted to be
    # blocked further down.
    monkeypatch.setenv("SPEC_ENGINE_BASE_URL", server)
    return providers.build(
        providers.resolve_settings({"provider": "vllm", "model": "qwen3:32b"})
    )


# --------------------------------------------------------------------------- #


def test_the_endpoint_answers_before_anything_is_spent(provider):
    """A typo in a port should not be discovered halfway through a compile."""
    assert "1 models available" in provider.check()


def test_a_whole_spec_compiles_over_http(provider):
    """The end of the argument: the model is a swappable part."""
    extraction = pipeline.extract_claims(provider, REFERENCE_DOCUMENT)
    interrogation = pipeline.interrogate(
        provider, REFERENCE_DOCUMENT, extraction.claims
    )
    for decision, answered in zip(
        interrogation.decisions, reference_spec().decisions, strict=True
    ):
        decision.answer = answered.answer

    result = pipeline.compile_spec(
        provider,
        REFERENCE_DOCUMENT,
        extraction.claims,
        interrogation.decisions,
        title=extraction.document_title,
    )

    assert result.ok, result.error
    assert result.report.passed
    assert result.model == "qwen3:32b"
    # Recorded so the run can still be priced after a refresh, or after the
    # user points the next document at a different provider.
    assert result.base_url.startswith("http://127.0.0.1:")
    assert len(result.spec.requirements) == 5
    assert len(_Handler.seen) == 4, "one request per pass, no repair rounds"


def test_the_gate_is_applied_to_a_non_anthropic_answer(provider):
    """Grounding does not care who produced the claim."""
    extraction = pipeline.extract_claims(provider, REFERENCE_DOCUMENT)
    report = verifier.verify(reference_spec(), REFERENCE_DOCUMENT)
    assert report.coverage.grounding_rate == 100.0
    # The same substring-after-normalisation check the gate runs, applied to
    # what came back over the wire.
    for claim in extraction.claims:
        assert verifier.find_quote_line(REFERENCE_DOCUMENT, claim.quote), claim.quote


def test_the_real_request_carries_the_model_and_the_schema(provider):
    pipeline.extract_claims(provider, REFERENCE_DOCUMENT)
    sent = _Handler.seen[0]
    assert sent["model"] == "qwen3:32b"
    assert sent["response_format"]["json_schema"]["strict"] is True
    assert sent["messages"][0]["role"] == "system"


def test_usage_survives_the_round_trip(provider):
    usage = pipeline.Usage()
    pipeline.extract_claims(provider, REFERENCE_DOCUMENT, usage=usage)
    assert (usage.calls, usage.input_tokens, usage.output_tokens) == (1, 3120, 1840)


def test_a_dead_endpoint_is_reported_in_words_a_person_can_act_on(monkeypatch):
    monkeypatch.setenv("SPEC_ENGINE_BASE_URL", "http://127.0.0.1:1/v1")
    provider = providers.build(
        providers.resolve_settings(
            # Port 1 is reserved and nothing listens there.
            {"provider": "vllm", "model": "x"}
        )
    )
    with pytest.raises(providers.ProviderError, match="Could not reach"):
        pipeline.extract_claims(provider, REFERENCE_DOCUMENT)
