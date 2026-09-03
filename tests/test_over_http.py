"""The compiler against a real OpenAI-compatible server, over real HTTP.

Every other test stops at a recorded request. This one starts a server on a
loopback port and runs all four passes through it — request encoding, status
handling, response decoding, usage accounting — because the failure that
actually happened during development was in none of the units: the HTTP client
this module used was not installed, and only a real request found that out.

The server is deliberately awkward in the ways an open-weight model is
awkward: it thinks out loud before answering and fences its JSON.
"""

import dataclasses
import json
import threading
import urllib.request
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
    #: Every request that reached the server, GET and POST alike — the URL
    #: policy assertions watch this log, not just the exception type.
    requests: list = []

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
        type(self).requests.append(("GET", self.path))
        self._reply({"data": [{"id": "qwen3:32b"}]})

    def do_POST(self):
        type(self).requests.append(("POST", self.path))
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
    _Handler.requests = []
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


# --------------------------------------------------------------------------- #
# The URL policy, against a real server
# --------------------------------------------------------------------------- #


class _Redirector(BaseHTTPRequestHandler):
    """Answers every GET with a 302 to a fixed target, recording what arrived."""

    target = ""
    seen_authorization: list = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        type(self).seen_authorization.append(self.headers.get("Authorization"))
        self.send_response(302)
        self.send_header("Location", self.target)
        self.send_header("content-length", "0")
        self.end_headers()


class _Destination(BaseHTTPRequestHandler):
    """The redirect target. What it received is the assertion."""

    seen_authorization: list = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        type(self).seen_authorization.append(self.headers.get("Authorization"))
        raw = json.dumps({"data": []}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def _serve(handler_class):
    httpd = HTTPServer(("127.0.0.1", 0), handler_class)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def test_a_visitor_typed_loopback_base_url_never_reaches_the_server(server):
    """The request dies at the seam — the server's own log is the witness."""
    settings = providers.resolve_settings(
        {
            "provider": "vllm",
            "base_url": server,
            "model": "qwen3:32b",
            "api_key": "sk-visitor",
        }
    )
    assert settings.base_url_source == "ui"
    provider = providers.build(settings)

    with pytest.raises(
        providers.ProviderError, match="not reachable from a hosted app"
    ):
        provider.check()

    assert _Handler.requests == [], "a blocked target is contacted zero times"


def test_a_preset_provenance_loopback_url_still_reaches_the_server(server):
    """The shipped presets point at loopback on purpose; the egress check is
    for visitor-typed URLs only."""
    settings = dataclasses.replace(
        providers.resolve_settings(
            {"provider": "vllm", "base_url": server, "model": "qwen3:32b"}
        ),
        base_url_source="preset",
    )

    assert "1 models available" in providers.build(settings).check()
    assert len(_Handler.requests) == 1


def test_a_redirect_to_a_private_target_is_never_followed():
    """Every hop re-runs the policy against the provenance the chain started
    with — a public first hop cannot launder a private redirect target past
    it. Handler-level because a visitor-provenance request can, by design,
    never reach a local first-hop server to be redirected from."""
    request = urllib.request.Request("http://api.provider.example/v1/models")
    request.provenance = "ui"
    with pytest.raises(
        providers.ProviderError, match="not reachable from a hosted app"
    ):
        providers._ValidatingRedirects().redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "http://169.254.169.254/latest/meta-data/",
        )


def test_a_cross_host_redirect_drops_the_authorization_header():
    """Same machine, different host: 127.0.0.1 → localhost is the cross-host
    boundary the Authorization rule keys on."""
    _Redirector.seen_authorization = []
    _Destination.seen_authorization = []
    first, first_thread = _serve(_Redirector)
    second, second_thread = _serve(_Destination)
    _Redirector.target = f"http://localhost:{second.server_port}/v1/models"
    try:
        settings = dataclasses.replace(
            providers.resolve_settings(
                {
                    "provider": "vllm",
                    "base_url": f"http://127.0.0.1:{first.server_port}/v1",
                    "model": "qwen3:32b",
                    "api_key": "sk-visitor-secret",
                }
            ),
            base_url_source="preset",
        )
        message = providers.build(settings).check()
    finally:
        first.shutdown()
        second.shutdown()
        first_thread.join(timeout=5)
        second_thread.join(timeout=5)

    assert "Reached" in message
    assert _Redirector.seen_authorization == ["Bearer sk-visitor-secret"]
    assert _Destination.seen_authorization == [None], (
        "the key must not ride along to the second host"
    )


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
