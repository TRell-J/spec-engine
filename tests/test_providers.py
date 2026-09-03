"""The provider seam: any Anthropic or OpenAI-compatible endpoint.

Nothing here touches the network. The OpenAI-compatible client takes an
injectable transport, so every request shape, every degrade path and every
error message is asserted against a recorded call rather than a live server.
"""

import json
import socket

import pytest

from core import pipeline, providers
from core.providers import (
    AnthropicProvider,
    OpenAICompatProvider,
    ProviderError,
)

pytestmark = pytest.mark.usefixtures("offline")

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "minLength": 3, "pattern": "^X"},
        "count": {"type": "integer", "minimum": 1},
    },
    "required": ["count", "title"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------------- #
# A recording transport
# --------------------------------------------------------------------------- #


class Transport:
    """Scripted stand-in for the network. Records every request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, body):
        self.calls.append({"url": url, "headers": headers, "body": body})
        if not self.responses:
            raise AssertionError("transport ran out of scripted responses")
        status, payload = self.responses.pop(0)
        return status, payload if isinstance(payload, str) else json.dumps(payload)


def completion(text="{}", prompt=11, completion_tokens=22):
    return 200, {
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion_tokens},
    }


def openai_provider(transport, **overrides):
    settings = providers.resolve_settings(
        {"provider": "openai", "api_key": "sk-test", **overrides}
    )
    return OpenAICompatProvider(settings=settings, transport=transport)


def send(provider, system="SYSTEM"):
    return provider.complete(
        system=system,
        messages=[{"role": "user", "content": "USER"}],
        schema=SCHEMA,
    )


# --------------------------------------------------------------------------- #
# The catalogue
# --------------------------------------------------------------------------- #


def test_every_preset_is_distinct_and_well_formed():
    keys = [s.key for s in providers.PROVIDERS]
    labels = [s.label for s in providers.PROVIDERS]
    assert len(set(keys)) == len(keys)
    assert len(set(labels)) == len(labels)
    for spec in providers.PROVIDERS:
        assert spec.kind in ("anthropic", "openai")
        assert spec.schema_mode in providers.SCHEMA_MODES
        # Anything not speaking the Messages API needs somewhere to send the
        # request, or the preset is a dead end.
        assert spec.base_url or spec.kind == "anthropic" or spec.key in ("custom",)


def test_both_open_and_closed_models_are_reachable():
    """The point of the exercise: not one vendor."""
    assert {s.key for s in providers.PROVIDERS} >= {
        "anthropic", "openai", "ollama", "openrouter", "custom"
    }
    assert any(s.hosted_locally for s in providers.PROVIDERS)
    assert any(not s.needs_key for s in providers.PROVIDERS)


def test_an_unknown_provider_falls_back_to_the_default():
    assert providers.spec_for("no-such-provider").key == providers.DEFAULT_PROVIDER
    assert providers.spec_for("").key == providers.DEFAULT_PROVIDER


def test_a_preset_can_be_named_by_key_or_by_label():
    spec = providers.BY_KEY["ollama"]
    assert providers.spec_for("ollama") is spec
    assert providers.spec_for(spec.label) is spec


# --------------------------------------------------------------------------- #
# Settings resolution
# --------------------------------------------------------------------------- #


def test_settings_default_to_the_preset():
    settings = providers.resolve_settings({"provider": "ollama"})
    assert settings.model == "llama3.1:8b"
    assert settings.base_url == "http://localhost:11434/v1"
    assert settings.kind == "openai"


def test_the_environment_beats_the_preset(monkeypatch):
    monkeypatch.setenv("SPEC_ENGINE_PROVIDER", "groq")
    monkeypatch.setenv("SPEC_ENGINE_MODEL", "some-other-model")
    monkeypatch.setenv("SPEC_ENGINE_MAX_TOKENS", "4096")
    settings = providers.resolve_settings()
    assert settings.provider == "groq"
    assert settings.model == "some-other-model"
    assert settings.max_tokens == 4096


def test_an_explicit_override_beats_the_environment(monkeypatch):
    monkeypatch.setenv("SPEC_ENGINE_MODEL", "from-the-environment")
    assert providers.resolve_settings({"model": "from-the-ui"}).model == "from-the-ui"


def test_each_provider_reads_its_own_conventional_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    assert providers.resolve_settings({"provider": "openai"}).api_key == "sk-openai"
    assert providers.resolve_settings({"provider": "anthropic"}).api_key == "sk-ant"


def test_a_generic_key_covers_a_provider_with_no_convention(monkeypatch):
    monkeypatch.setenv("SPEC_ENGINE_API_KEY", "sk-whatever")
    assert providers.resolve_settings({"provider": "vllm"}).api_key == "sk-whatever"


def test_a_local_model_is_configured_without_any_key():
    """The open-source path must not demand a secret that does not exist."""
    assert providers.resolve_settings({"provider": "ollama"}).configured


def test_a_hosted_model_is_not_configured_without_a_key():
    assert not providers.resolve_settings({"provider": "openai"}).configured
    assert providers.resolve_settings(
        {"provider": "openai", "api_key": "sk-test"}
    ).configured


def test_a_provider_with_no_model_named_is_not_configured():
    assert not providers.resolve_settings({"provider": "vllm"}).configured
    assert providers.resolve_settings({"provider": "vllm", "model": "qwen"}).configured


def test_a_bogus_schema_mode_is_ignored():
    assert providers.resolve_settings({"schema_mode": "wishful"}).schema_mode in (
        providers.SCHEMA_MODES
    )


@pytest.mark.parametrize(
    "url,local",
    [
        ("http://localhost:11434/v1", True),
        ("http://127.0.0.1:8000/v1", True),
        ("https://api.openai.com/v1", False),
        ("", False),
    ],
)
def test_loopback_endpoints_are_recognised_as_local(url, local):
    assert providers.is_local(url) is local


# --------------------------------------------------------------------------- #
# The OpenAI-compatible wire
# --------------------------------------------------------------------------- #


def test_the_request_carries_the_system_prompt_and_the_schema():
    transport = Transport(completion('{"title": "Xy", "count": 1}'))
    send(openai_provider(transport))

    body = transport.calls[0]["body"]
    assert transport.calls[0]["url"] == "https://api.openai.com/v1/chat/completions"
    assert transport.calls[0]["headers"]["authorization"] == "Bearer sk-test"
    assert body["messages"][0] == {"role": "system", "content": "SYSTEM"}
    assert body["messages"][1] == {"role": "user", "content": "USER"}
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["max_tokens"] == 16000


def test_usage_is_translated_from_the_openai_shape():
    transport = Transport(completion("{}", prompt=1234, completion_tokens=567))
    reply = send(openai_provider(transport))
    assert (reply.input_tokens, reply.output_tokens) == (1234, 567)


def test_content_returned_as_parts_is_joined():
    transport = Transport(
        (
            200,
            {
                "choices": [
                    {"message": {"content": [{"text": '{"a":'}, {"text": " 1}"}]}}
                ],
                "usage": {},
            },
        )
    )
    assert send(openai_provider(transport)).text == '{"a": 1}'


def test_the_wire_schema_drops_keywords_strict_mode_rejects():
    """Pydantic still enforces them locally — that is where it matters."""
    transport = Transport(completion())
    send(openai_provider(transport))
    schema = transport.calls[0]["body"]["response_format"]["json_schema"]["schema"]
    assert "minLength" not in schema["properties"]["title"]
    assert "pattern" not in schema["properties"]["title"]
    assert "minimum" not in schema["properties"]["count"]
    assert schema["properties"]["title"]["type"] == "string"
    assert schema["required"] == ["count", "title"]
    assert schema["additionalProperties"] is False


def test_json_object_mode_puts_the_schema_in_the_prompt():
    transport = Transport(completion())
    send(openai_provider(transport, schema_mode="json_object"))
    body = transport.calls[0]["body"]
    assert body["response_format"] == {"type": "json_object"}
    assert "JSON Schema" in body["messages"][0]["content"]
    assert '"count"' in body["messages"][0]["content"]


def test_prompt_mode_sends_no_response_format_at_all():
    """The floor: a server with no structured-output support at all."""
    transport = Transport(completion())
    send(openai_provider(transport, schema_mode="prompt"))
    body = transport.calls[0]["body"]
    assert "response_format" not in body
    assert "JSON Schema" in body["messages"][0]["content"]


# --------------------------------------------------------------------------- #
# Degrading rather than failing
# --------------------------------------------------------------------------- #


def test_a_server_that_rejects_json_schema_drops_to_json_object():
    transport = Transport(
        (400, {"error": {"message": "response_format json_schema is not supported"}}),
        completion('{"ok": true}'),
    )
    provider = openai_provider(transport)
    reply = send(provider)

    assert reply.text == '{"ok": true}'
    assert provider.schema_mode == "json_object"
    assert transport.calls[1]["body"]["response_format"] == {"type": "json_object"}


def test_a_server_that_rejects_both_falls_back_to_the_prompt():
    refusal = (400, {"error": {"message": "response_format is not supported"}})
    transport = Transport(refusal, refusal, completion('{"ok": true}'))
    provider = openai_provider(transport)
    send(provider)

    assert provider.schema_mode == "prompt"
    assert "response_format" not in transport.calls[2]["body"]


def test_the_degraded_mode_sticks_for_the_rest_of_the_run():
    """Rediscovering the same limitation on every pass would cost four calls."""
    transport = Transport(
        (400, {"error": {"message": "json_schema is not supported"}}),
        completion(),
        completion(),
    )
    provider = openai_provider(transport)
    send(provider)
    send(provider)
    assert len(transport.calls) == 3
    assert transport.calls[2]["body"]["response_format"] == {"type": "json_object"}


def test_a_model_that_wants_max_completion_tokens_gets_it():
    transport = Transport(
        (
            400,
            {
                "error": {
                    "message": "Unsupported parameter: 'max_tokens' is not "
                    "supported with this model. Use 'max_completion_tokens'."
                }
            },
        ),
        completion(),
    )
    provider = openai_provider(transport)
    send(provider)
    assert "max_tokens" not in transport.calls[1]["body"]
    assert transport.calls[1]["body"]["max_completion_tokens"] == 16000
    # and the structured-output mode was not blamed for it
    assert provider.schema_mode == "json_schema"


def test_an_unrelated_failure_is_reported_rather_than_degraded():
    """A wrong model name must not be mistaken for a schema problem."""
    transport = Transport(
        (404, {"error": {"message": "The model 'gpt-nope' does not exist"}})
    )
    provider = openai_provider(transport)
    with pytest.raises(ProviderError, match="does not exist"):
        send(provider)
    assert len(transport.calls) == 1
    assert provider.schema_mode == "json_schema"


def test_the_error_carries_the_servers_own_words():
    transport = Transport((401, {"error": {"message": "Incorrect API key provided"}}))
    with pytest.raises(ProviderError) as exc:
        send(openai_provider(transport))
    assert "Incorrect API key provided" in str(exc.value)
    assert "401" in str(exc.value)


def test_a_non_json_error_body_still_reaches_the_user():
    transport = Transport((502, "<html>Bad Gateway</html>"))
    with pytest.raises(ProviderError, match="Bad Gateway"):
        send(openai_provider(transport))


def test_a_provider_with_no_base_url_says_so():
    settings = providers.resolve_settings({"provider": "custom", "model": "x"})
    with pytest.raises(ProviderError, match="base URL"):
        send(OpenAICompatProvider(settings=settings, transport=Transport()))


def test_a_response_with_no_choices_is_an_error_not_an_empty_spec():
    transport = Transport((200, {"choices": [], "usage": {}}))
    with pytest.raises(ProviderError, match="no choices"):
        send(openai_provider(transport))


# --------------------------------------------------------------------------- #
# Reaching the endpoint before spending anything
# --------------------------------------------------------------------------- #


def test_check_reports_the_models_the_server_offers():
    transport = Transport((200, {"data": [{"id": "gpt-5"}, {"id": "gpt-4o"}]}))
    message = openai_provider(transport, model="gpt-5").check()
    assert transport.calls[0]["url"].endswith("/models")
    assert transport.calls[0]["body"] is None
    assert "2 models" in message


def test_check_names_a_model_the_server_does_not_have():
    transport = Transport((200, {"data": [{"id": "gpt-4o"}]}))
    message = openai_provider(transport, model="llama3.1:8b").check()
    assert "llama3.1:8b" in message and "not one of them" in message


def test_check_warns_when_the_model_cannot_hold_a_schema():
    """The question a user cannot otherwise answer without reading vendor docs."""
    transport = Transport(
        (
            200,
            {
                "data": [
                    {
                        "id": "moonshotai/kimi-k2",
                        "supported_parameters": ["temperature", "max_tokens"],
                    }
                ]
            },
        )
    )
    message = openai_provider(transport, model="moonshotai/kimi-k2").check()
    assert "does not support structured outputs" in message
    assert "repair rounds" in message


def test_check_stays_quiet_when_the_model_does_support_them():
    transport = Transport(
        (
            200,
            {
                "data": [
                    {
                        "id": "deepseek/deepseek-chat",
                        "supported_parameters": ["structured_outputs", "temperature"],
                    }
                ]
            },
        )
    )
    message = openai_provider(transport, model="deepseek/deepseek-chat").check()
    assert "structured outputs" not in message
    assert "1 models available" in message


def test_check_does_not_invent_a_capability_claim_for_a_plain_server():
    """Ollama and vLLM publish no capability field. Silence is not evidence."""
    transport = Transport((200, {"data": [{"id": "qwen3:32b"}]}))
    message = openai_provider(transport, model="qwen3:32b").check()
    assert "does not support" not in message


def test_check_surfaces_a_refusal():
    transport = Transport((401, {"error": {"message": "no key"}}))
    with pytest.raises(ProviderError, match="no key"):
        openai_provider(transport).check()


# --------------------------------------------------------------------------- #
# Anthropic, unchanged
# --------------------------------------------------------------------------- #


def test_the_anthropic_request_is_untouched_by_any_of_this(payloads):
    from conftest import FakeClient

    client = FakeClient([payloads["extract"]])
    provider = providers.adapt(client)
    provider.complete(
        system="SYSTEM", messages=[{"role": "user", "content": "USER"}], schema=SCHEMA
    )
    request = client.calls[0]
    assert request["output_config"]["format"]["schema"] == SCHEMA
    assert request["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert request["system"][0]["text"] == "SYSTEM"


def test_a_messages_shaped_client_is_adapted_and_a_provider_is_not():
    from conftest import FakeClient

    wrapped = providers.adapt(FakeClient([]))
    assert isinstance(wrapped, AnthropicProvider)
    assert providers.adapt(wrapped) is wrapped


def test_an_anthropic_gateway_that_rejects_output_config_degrades():
    from conftest import FakeClient

    class Rejecting(FakeClient):
        def _create(self, **kwargs):
            self.calls.append(kwargs)
            if "output_config" in kwargs:
                raise RuntimeError("output_config: unknown field")
            return super()._create(**{k: v for k, v in kwargs.items()})

    client = Rejecting(['{"ok": true}'])
    provider = providers.adapt(client)
    reply = provider.complete(
        system="S", messages=[{"role": "user", "content": "U"}], schema=SCHEMA
    )
    assert reply.text == '{"ok": true}'
    assert provider.schema_mode == "json_object"
    assert "JSON Schema" in client.calls[1]["system"][0]["text"]


# --------------------------------------------------------------------------- #
# Construction
# --------------------------------------------------------------------------- #


def test_build_returns_nothing_when_there_is_nothing_to_build_with():
    assert providers.build(providers.resolve_settings({"provider": "openai"})) is None
    assert pipeline.build_client() is None


def test_build_picks_the_protocol_from_the_preset():
    local = providers.build(providers.resolve_settings({"provider": "ollama"}))
    assert isinstance(local, OpenAICompatProvider)
    assert local.settings.base_url == "http://localhost:11434/v1"


def test_build_client_accepts_a_pasted_key(monkeypatch):
    provider = pipeline.build_client("sk-pasted", {"provider": "openai"})
    assert isinstance(provider, OpenAICompatProvider)
    assert provider.settings.api_key == "sk-pasted"


# --------------------------------------------------------------------------- #
# The compiler over a non-Anthropic provider
# --------------------------------------------------------------------------- #


def test_the_whole_extraction_pass_runs_over_an_openai_compatible_server(payloads):
    """The compiler does not care who answered; the gate is downstream."""
    from examples.reference import REFERENCE_DOCUMENT

    transport = Transport(completion(json.dumps(payloads["extract"])))
    provider = openai_provider(transport, model="llama-3.3-70b")
    usage = pipeline.Usage()

    result = pipeline.extract_claims(provider, REFERENCE_DOCUMENT, usage=usage)

    assert len(result.claims) == 6
    assert transport.calls[0]["body"]["model"] == "llama-3.3-70b"
    assert usage.calls == 1 and usage.input_tokens == 11


def test_a_reasoning_trace_before_the_json_is_not_a_schema_failure(payloads):
    """Open-weight reasoning models think out loud. That is not a defect."""
    from examples.reference import REFERENCE_DOCUMENT

    thinking = (
        "<think>The document mentions approvals, so I should record that as a "
        "requirement claim.</think>\n" + json.dumps(payloads["extract"])
    )
    transport = Transport(completion(thinking))
    result = pipeline.extract_claims(openai_provider(transport), REFERENCE_DOCUMENT)
    assert len(result.claims) == 6
    assert len(transport.calls) == 1, "it should not have cost a repair round"


def test_a_weak_model_that_never_conforms_fails_rather_than_half_succeeding():
    transport = Transport(*[completion('{"nope": true}')] * 3)
    with pytest.raises(pipeline.PipelineError, match="schema adherence failed"):
        pipeline.call_structured(
            openai_provider(transport), "system", "user", pipeline.ExtractionResult
        )
    assert len(transport.calls) == 3


def test_the_repair_turn_travels_over_the_openai_protocol_too(payloads):
    from examples.reference import REFERENCE_DOCUMENT

    transport = Transport(
        completion('{"document_title": "x", "claims": [{"id": "NOPE"}]}'),
        completion(json.dumps(payloads["extract"])),
    )
    result = pipeline.extract_claims(openai_provider(transport), REFERENCE_DOCUMENT)
    assert len(result.claims) == 6
    conversation = transport.calls[1]["body"]["messages"]
    assert conversation[-1]["role"] == "user"
    assert "failed validation" in conversation[-1]["content"]


# --------------------------------------------------------------------------- #
# Provenance tagging
# --------------------------------------------------------------------------- #


def test_a_typed_base_url_is_tagged_ui():
    settings = providers.resolve_settings(
        {
            "provider": "openai",
            "api_key": "sk-test",
            "base_url": "https://api.example.com/v1",
        }
    )
    assert settings.base_url_source == "ui"


def test_an_environment_base_url_is_tagged_env(monkeypatch):
    monkeypatch.setenv("SPEC_ENGINE_BASE_URL", "https://api.example.com/v1")
    settings = providers.resolve_settings({"provider": "openai", "api_key": "sk-test"})
    assert settings.base_url_source == "env"


def test_the_preset_base_url_is_tagged_preset():
    settings = providers.resolve_settings({"provider": "openai"})
    assert settings.base_url_source == "preset"
    assert settings.base_url == "https://api.openai.com/v1"


# --------------------------------------------------------------------------- #
# The URL policy at the seam
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("source", ["ui", "env", "preset"])
def test_non_web_schemes_are_refused_for_every_provenance(source):
    """file:// read the host's files through this seam once. Not any more."""
    with pytest.raises(ProviderError, match="plain http"):
        providers._validate_url("file:///etc/hostname", source)
    with pytest.raises(ProviderError, match="plain http"):
        providers._validate_url("ftp://mirror.example.net/model.gguf", source)


def test_the_scheme_gate_is_case_insensitive():
    providers._validate_url("HTTPS://api.example.com/v1", "preset")


def test_embedded_userinfo_is_refused():
    with pytest.raises(ProviderError, match="plain http"):
        providers._validate_url("https://user:pass@api.example.com/v1", "env")
    with pytest.raises(ProviderError, match="plain http"):
        providers._validate_url("https://attacker@api.example.com/v1", "preset")


def test_a_url_without_a_host_is_refused():
    with pytest.raises(ProviderError, match="missing a host"):
        providers._validate_url("http:///v1", "preset")


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:11434/v1",
        "http://localhost:1234/v1",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.5/v1",
        "http://192.168.1.10:8000/v1",
        "http://172.16.0.1/v1",
    ],
)
def test_visitor_urls_may_not_reach_private_addresses(url):
    with pytest.raises(ProviderError, match="not reachable from a hosted app"):
        providers._validate_url(url, "ui")


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:11434/v1",
        "http://localhost:1234/v1",
        "http://10.0.0.5/v1",
    ],
)
def test_operator_provenance_keeps_its_local_servers(url):
    """The shipped presets are loopback on purpose; so is the operator's env."""
    providers._validate_url(url, "preset")
    providers._validate_url(url, "env")


def test_a_public_host_passes_the_visitor_gate(monkeypatch):
    """No real DNS in the suite — the resolution itself is stubbed."""
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("93.184.216.34", 0))],
    )
    providers._validate_url("https://api.example.com/v1", "ui")


def test_an_unresolvable_host_is_a_clear_error_not_a_stack_trace(monkeypatch):
    def refuse(*args, **kwargs):
        raise socket.gaierror("name resolution failed")

    monkeypatch.setattr("socket.getaddrinfo", refuse)
    with pytest.raises(ProviderError, match="Could not resolve"):
        providers._validate_url("https://api.example.com/v1", "ui")


# --------------------------------------------------------------------------- #
# The request timeout
# --------------------------------------------------------------------------- #


def test_the_timeout_defaults_to_five_minutes(monkeypatch):
    monkeypatch.delenv("SPEC_ENGINE_TIMEOUT_SECONDS", raising=False)
    assert providers._request_timeout() == 300.0


def test_the_timeout_can_be_raised_for_slow_local_hardware(monkeypatch):
    monkeypatch.setenv("SPEC_ENGINE_TIMEOUT_SECONDS", "900.5")
    assert providers._request_timeout() == 900.5


@pytest.mark.parametrize("raw", ["banana", "", "   ", "-5", "0"])
def test_an_unreadable_timeout_falls_back_to_the_default(monkeypatch, raw):
    monkeypatch.setenv("SPEC_ENGINE_TIMEOUT_SECONDS", raw)
    assert providers._request_timeout() == 300.0


# --------------------------------------------------------------------------- #
# Key provenance: server keys are opt-in for interactive sessions
# --------------------------------------------------------------------------- #


def test_a_visitor_key_resolves_with_ui_provenance():
    settings = providers.resolve_settings({"provider": "openai", "api_key": "sk-visitor"})
    assert settings.api_key == "sk-visitor"
    assert settings.key_provenance == "ui"
    assert settings.configured


def test_flag_off_still_honours_a_visitor_key():
    """The gate is about whose key runs, not whether a key can exist."""
    settings = providers.resolve_settings(
        {"provider": "openai", "api_key": "sk-visitor"}, allow_env_keys=False
    )
    assert settings.api_key == "sk-visitor"
    assert settings.key_provenance == "ui"
    assert settings.configured


def test_an_environment_key_resolves_with_server_provenance(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-host")
    settings = providers.resolve_settings({"provider": "openai"})
    assert settings.api_key == "sk-host"
    assert settings.key_provenance == "server"


def test_the_gateway_token_belongs_to_the_server_chain(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-gateway")
    settings = providers.resolve_settings({"provider": "anthropic"})
    assert settings.api_key == "sk-gateway"
    assert settings.key_provenance == "server"


def test_flag_off_ignores_the_whole_environment_chain(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-host")
    monkeypatch.setenv("SPEC_ENGINE_API_KEY", "sk-generic")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-gateway")
    settings = providers.resolve_settings({"provider": "openai"}, allow_env_keys=False)
    assert settings.api_key == ""
    assert settings.key_provenance == "none"
    assert not settings.configured


def test_a_server_key_works_with_the_preset_url(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-host")
    settings = providers.resolve_settings({"provider": "openai"})
    assert settings.key_provenance == "server"
    assert settings.base_url_source == "preset"
    assert settings.configured


def test_a_server_key_works_with_an_operator_env_url(monkeypatch):
    """env key + env URL is the operator's own configuration, all of it."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-host")
    monkeypatch.setenv("SPEC_ENGINE_BASE_URL", "http://localhost:8000/v1")
    settings = providers.resolve_settings({"provider": "openai"})
    assert settings.key_provenance == "server"
    assert settings.base_url_source == "env"


def test_a_server_key_and_a_visitor_url_are_refused(monkeypatch):
    """Wherever the visitor's URL points decides where the operator's
    credentials travel — the combination is refused outright."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-host")
    with pytest.raises(providers.ProviderError, match="preset URL"):
        providers.resolve_settings(
            {"provider": "openai", "base_url": "https://api.example.com/v1"}
        )


def test_flag_off_never_triggers_the_url_refusal(monkeypatch):
    """With no server key in play, a visitor URL has nothing to pair with."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-host")
    settings = providers.resolve_settings(
        {"provider": "openai", "base_url": "https://api.example.com/v1"},
        allow_env_keys=False,
    )
    assert settings.key_provenance == "none"
    assert settings.base_url_source == "ui"
    assert not settings.configured


def test_keyless_interactive_sessions_build_no_client(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-host")
    settings = providers.resolve_settings({"provider": "openai"}, allow_env_keys=False)
    assert providers.build(settings) is None


def test_a_keyless_check_never_touches_the_wire(monkeypatch):
    """Keyless + flag off: the pre-flight asks for a key instead of sending a
    doomed anonymous request — on a hosted endpoint that round trip still
    happens, and zero is the only number that costs nothing."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-host")
    settings = providers.resolve_settings(
        {"provider": "openai", "base_url": "https://api.example.com/v1"},
        allow_env_keys=False,
    )
    transport = Transport()  # nothing scripted: any call trips an assertion
    provider = OpenAICompatProvider(settings=settings, transport=transport)
    with pytest.raises(providers.ProviderError, match="Add an API key"):
        provider.check()
    assert transport.calls == []

