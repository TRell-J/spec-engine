"""Model providers: any Anthropic or OpenAI-compatible endpoint.

The compiler does not care which model reads the document. Everything that
makes the output trustworthy — grounding, EARS parsing, traceability, the
verification gate — is deterministic Python that runs after the model answers.
So the model is a swappable part, and this module is the seam.

Two wire protocols cover essentially every provider worth pointing at:

    anthropic   the Messages API — Claude, and Anthropic-compatible gateways
    openai      /v1/chat/completions — OpenAI, Google, Groq, Together,
                OpenRouter, DeepSeek, Mistral, Ollama, LM Studio, vLLM,
                llama.cpp, and anything else that speaks the same shape

Structured output is where providers actually differ, so it degrades rather
than fails:

    json_schema   the schema is enforced at generation (OpenAI, recent Ollama)
    json_object   the model is told to emit JSON; the schema rides in the prompt
    prompt        the schema rides in the prompt alone

A provider that rejects a tier drops to the next one and retries, once, and
remembers the answer for the rest of the run. Every tier ends at the same
place: the JSON is validated against the Pydantic model locally and repaired
in-conversation if it does not fit. Schema enforcement on the wire is a
convenience, not the guarantee.
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from functools import partial
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit, urlparse

# --------------------------------------------------------------------------- #
# Catalogue
# --------------------------------------------------------------------------- #

SCHEMA_MODES = ("json_schema", "json_object", "prompt")

#: Ordered fallback: a provider that rejects one tier tries the next.
_DEGRADE = {"json_schema": "json_object", "json_object": "prompt", "prompt": None}


@dataclass(frozen=True)
class ProviderSpec:
    """A preset. Every field stays editable — this is a starting point."""

    key: str
    label: str
    kind: str  # "anthropic" | "openai"
    default_model: str
    base_url: str = ""
    key_env: str = ""
    key_hint: str = ""
    schema_mode: str = "json_schema"
    needs_key: bool = True
    hosted_locally: bool = False
    note: str = ""

    @property
    def env_key(self) -> str:
        return os.getenv(self.key_env, "").strip() if self.key_env else ""


PROVIDERS: Tuple[ProviderSpec, ...] = (
    ProviderSpec(
        key="anthropic",
        label="Anthropic — Claude",
        kind="anthropic",
        default_model="claude-opus-5",
        key_env="ANTHROPIC_API_KEY",
        key_hint="sk-ant-…",
        note="The four prompts were written against Claude.",
    ),
    ProviderSpec(
        key="openai",
        label="OpenAI",
        kind="openai",
        default_model="gpt-5",
        base_url="https://api.openai.com/v1",
        key_env="OPENAI_API_KEY",
        key_hint="sk-…",
        note="Enforces the JSON Schema at generation time.",
    ),
    ProviderSpec(
        key="google",
        label="Google — Gemini",
        kind="openai",
        default_model="gemini-2.5-pro",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        key_env="GEMINI_API_KEY",
        key_hint="AIza…",
        note="Gemini's OpenAI-compatible endpoint.",
    ),
    ProviderSpec(
        key="openrouter",
        label="OpenRouter — any hosted model",
        kind="openai",
        default_model="deepseek/deepseek-chat",
        base_url="https://openrouter.ai/api/v1",
        key_env="OPENROUTER_API_KEY",
        key_hint="sk-or-…",
        note="One key, several hundred open and closed models.",
    ),
    ProviderSpec(
        key="groq",
        label="Groq",
        kind="openai",
        default_model="llama-3.3-70b-versatile",
        base_url="https://api.groq.com/openai/v1",
        key_env="GROQ_API_KEY",
        key_hint="gsk_…",
        schema_mode="json_object",
    ),
    ProviderSpec(
        key="together",
        label="Together AI",
        kind="openai",
        default_model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        base_url="https://api.together.xyz/v1",
        key_env="TOGETHER_API_KEY",
        key_hint="…",
    ),
    ProviderSpec(
        key="deepseek",
        label="DeepSeek",
        kind="openai",
        default_model="deepseek-chat",
        base_url="https://api.deepseek.com/v1",
        key_env="DEEPSEEK_API_KEY",
        key_hint="sk-…",
        schema_mode="json_object",
    ),
    ProviderSpec(
        key="mistral",
        label="Mistral",
        kind="openai",
        default_model="mistral-large-latest",
        base_url="https://api.mistral.ai/v1",
        key_env="MISTRAL_API_KEY",
        key_hint="…",
        schema_mode="json_object",
    ),
    ProviderSpec(
        key="ollama",
        label="Ollama — local",
        kind="openai",
        default_model="llama3.1:8b",
        base_url="http://localhost:11434/v1",
        needs_key=False,
        hosted_locally=True,
        note=(
            "Runs on your machine, so it is free and nothing leaves it. "
            "This is a long structured-output job: models below roughly 30B "
            "often cannot hold the schema, and the compile will fail rather "
            "than return something half-valid."
        ),
    ),
    ProviderSpec(
        key="lmstudio",
        label="LM Studio — local",
        kind="openai",
        default_model="local-model",
        base_url="http://localhost:1234/v1",
        needs_key=False,
        hosted_locally=True,
        note="Load a model in LM Studio and start its local server.",
    ),
    ProviderSpec(
        key="vllm",
        label="vLLM / self-hosted",
        kind="openai",
        default_model="",
        base_url="http://localhost:8000/v1",
        needs_key=False,
        hosted_locally=True,
        note="Any OpenAI-compatible server you run yourself.",
    ),
    ProviderSpec(
        key="custom",
        label="Custom — any OpenAI-compatible URL",
        kind="openai",
        default_model="",
        base_url="",
        key_env="SPEC_ENGINE_API_KEY",
        key_hint="…",
        needs_key=False,
        schema_mode="json_object",
        note="Point this at any endpoint that speaks /v1/chat/completions.",
    ),
)

BY_KEY: Dict[str, ProviderSpec] = {spec.key: spec for spec in PROVIDERS}
BY_LABEL: Dict[str, ProviderSpec] = {spec.label: spec for spec in PROVIDERS}
DEFAULT_PROVIDER = "anthropic"


def spec_for(key: str) -> ProviderSpec:
    """Look a preset up by key or label. Unknown falls back to the default."""
    key = (key or "").strip()
    return BY_KEY.get(key) or BY_LABEL.get(key) or BY_KEY[DEFAULT_PROVIDER]


def is_local(base_url: str) -> bool:
    """A loopback endpoint costs nothing and sends nothing off the machine."""
    host = (urlparse(base_url or "").hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"}


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #

DEFAULT_MAX_TOKENS = 16000


@dataclass(frozen=True)
class Settings:
    """A resolved, ready-to-use provider configuration."""

    provider: str
    label: str
    kind: str
    model: str
    base_url: str
    api_key: str
    schema_mode: str
    max_tokens: int
    needs_key: bool
    hosted_locally: bool
    note: str = ""
    #: Where base_url resolved from: "ui" (a visitor typed it), "env" (the
    #: operator's environment) or "preset" (the in-code default). The network
    #: seam holds visitor-typed URLs to the public web and lets the operator's
    #: own configuration reach local model servers.
    base_url_source: str = "preset"

    @property
    def configured(self) -> bool:
        """Enough to attempt a call: a model, and a key unless one is not needed."""
        if not self.model.strip():
            return False
        return bool(self.api_key.strip()) or not self.needs_key

    @property
    def free(self) -> bool:
        return self.hosted_locally or is_local(self.base_url)

    @property
    def describe(self) -> str:
        return f"{self.label.split(' — ')[0]} · {self.model or 'no model set'}"


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def resolve_settings(overrides: Optional[Dict[str, Any]] = None) -> Settings:
    """Preset defaults, then environment, then whatever the UI set.

    The environment is how you run this headless or in a container; the UI
    overrides are how you change your mind without restarting.
    """
    overrides = {k: v for k, v in (overrides or {}).items() if v not in (None, "")}

    spec = spec_for(
        overrides.get("provider") or _env("SPEC_ENGINE_PROVIDER") or DEFAULT_PROVIDER
    )

    base_override = overrides.get("base_url")
    base_from_env = _env("SPEC_ENGINE_BASE_URL")
    base_url = (base_override or base_from_env or spec.base_url).strip().rstrip("/")
    # Where the URL came from decides what the network seam will do with it.
    if base_override:
        base_url_source = "ui"
    elif base_from_env:
        base_url_source = "env"
    else:
        base_url_source = "preset"

    model = (
        overrides.get("model") or _env("SPEC_ENGINE_MODEL") or spec.default_model
    ).strip()

    api_key = (
        overrides.get("api_key")
        or spec.env_key
        or _env("SPEC_ENGINE_API_KEY")
        # A gateway speaking the Anthropic protocol still reads this one.
        or (_env("ANTHROPIC_AUTH_TOKEN") if spec.kind == "anthropic" else "")
    ).strip()

    mode = (
        overrides.get("schema_mode") or _env("SPEC_ENGINE_SCHEMA_MODE") or spec.schema_mode
    ).strip()
    if mode not in SCHEMA_MODES:
        mode = spec.schema_mode

    raw_tokens = str(overrides.get("max_tokens") or _env("SPEC_ENGINE_MAX_TOKENS"))
    max_tokens = (
        int(raw_tokens) if raw_tokens.isdigit() and int(raw_tokens) > 0
        else DEFAULT_MAX_TOKENS
    )

    return Settings(
        provider=spec.key,
        label=spec.label,
        kind=spec.kind,
        model=model,
        base_url=base_url,
        api_key=api_key,
        schema_mode=mode,
        max_tokens=max_tokens,
        needs_key=spec.needs_key,
        hosted_locally=spec.hosted_locally,
        note=spec.note,
        base_url_source=base_url_source,
    )


# --------------------------------------------------------------------------- #
# The wire
# --------------------------------------------------------------------------- #


class ProviderError(RuntimeError):
    """A provider refused the request, and the message says what it said."""


@dataclass
class Reply:
    """One model response, normalised across protocols."""

    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0


_JSON_INSTRUCTION = (
    "\n\nOUTPUT FORMAT. Return exactly one JSON object and nothing else — no "
    "prose before or after it, no markdown fence, no explanation. It must "
    "conform to this JSON Schema:\n\n{schema}\n\n"
    "Every property listed in `required` must be present. Where a value is "
    "genuinely unknown, use null rather than omitting the key."
)

# Keywords OpenAI's strict structured-output mode rejects outright. Dropping
# them from the wire schema costs nothing: Pydantic re-validates the response
# locally with every constraint intact, and a violation goes back to the model
# through the same repair turn a malformed response would.
_UNSUPPORTED_KEYWORDS = {
    "minLength", "maxLength", "pattern", "format", "minimum", "maximum",
    "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minItems",
    "maxItems", "uniqueItems", "default", "examples",
}


def portable_schema(node: Any) -> Any:
    """Strip validation keywords that strict structured-output modes reject."""
    if isinstance(node, list):
        return [portable_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    return {
        key: portable_schema(value)
        for key, value in node.items()
        if key not in _UNSUPPORTED_KEYWORDS
    }


@dataclass
class Provider:
    """Base: holds the settings and the degrade state for one run."""

    settings: Settings
    schema_mode: str = ""

    def __post_init__(self) -> None:
        self.schema_mode = self.schema_mode or self.settings.schema_mode

    # -- helpers shared by both protocols ---------------------------------- #

    def _with_schema_prompt(self, system: str, schema: Dict[str, Any]) -> str:
        if self.schema_mode == "json_schema":
            return system
        return system + _JSON_INSTRUCTION.format(
            schema=json.dumps(portable_schema(schema), indent=2)
        )

    def _degrade(self) -> bool:
        """Drop to the next structured-output tier. False when there is none."""
        nxt = _DEGRADE.get(self.schema_mode)
        if nxt is None:
            return False
        self.schema_mode = nxt
        return True

    def complete(  # pragma: no cover - overridden
        self,
        *,
        system: str,
        messages: List[Dict[str, Any]],
        schema: Dict[str, Any],
        model: str = "",
        max_tokens: int = 0,
    ) -> Reply:
        raise NotImplementedError

    def check(self) -> str:  # pragma: no cover - overridden
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# Anthropic
# --------------------------------------------------------------------------- #


@dataclass
class AnthropicProvider(Provider):
    """The Messages API, through the official SDK."""

    client: Any = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.client is None:
            self.client = _anthropic_client(self.settings)

    def complete(
        self,
        *,
        system: str,
        messages: List[Dict[str, Any]],
        schema: Dict[str, Any],
        model: str = "",
        max_tokens: int = 0,
    ) -> Reply:
        request: Dict[str, Any] = {
            "model": model or self.settings.model,
            "max_tokens": max_tokens or self.settings.max_tokens,
            # The system prompt is stable across every pass; caching it is the
            # single biggest saving available on a multi-pass run.
            "system": [
                {
                    "type": "text",
                    "text": self._with_schema_prompt(system, schema),
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": messages,
        }
        if self.schema_mode == "json_schema":
            request["output_config"] = {
                "format": {"type": "json_schema", "schema": schema}
            }

        try:
            response = self.client.messages.create(**request)
        except Exception as exc:
            if _is_schema_rejection(str(exc)) and self._degrade():
                return self.complete(
                    system=system, messages=messages, schema=schema,
                    model=model, max_tokens=max_tokens,
                )
            raise

        usage = getattr(response, "usage", None)
        return Reply(
            text="".join(
                block.text
                for block in getattr(response, "content", [])
                if getattr(block, "type", None) == "text"
            ).strip(),
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        )

    def check(self) -> str:
        models = self.client.models.list(limit=1)
        return f"Reached {self.settings.label}." if models is not None else "No answer."


def _anthropic_client(settings: Settings) -> Any:
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - only without the SDK
        raise ProviderError(
            "The anthropic package is not installed: pip install anthropic"
        ) from exc
    kwargs: Dict[str, Any] = {"timeout": _request_timeout()}
    if settings.api_key:
        kwargs["api_key"] = settings.api_key
    if settings.base_url:
        kwargs["base_url"] = settings.base_url
    return anthropic.Anthropic(**kwargs)


# --------------------------------------------------------------------------- #
# OpenAI-compatible
# --------------------------------------------------------------------------- #

_SCHEMA_TOKENS = (
    "response_format",
    "json_schema",
    "json_object",
    "json mode",
    "output_config",
    "structured output",
    "structured_output",
    "schema",
)
_REFUSAL_TOKENS = (
    "not supported",
    "unsupported",
    "does not support",
    "unknown",
    "unrecognized",
    "invalid",
    "extra inputs are not permitted",
    "not permitted",
    "no longer",
)


def _is_schema_rejection(message: str) -> bool:
    """Did the server refuse the *structured-output* part of the request?

    Deliberately narrow. A broad match would read "model not supported" as a
    schema problem and burn two retries degrading something that was never the
    cause, then report the wrong error.
    """
    lowered = message.lower()
    return any(t in lowered for t in _SCHEMA_TOKENS) and any(
        t in lowered for t in _REFUSAL_TOKENS
    )


def _wants_max_completion_tokens(message: str) -> bool:
    lowered = message.lower()
    return "max_completion_tokens" in lowered and "max_tokens" in lowered


@dataclass
class OpenAICompatProvider(Provider):
    """`POST {base_url}/chat/completions`.

    Raw HTTP rather than the OpenAI SDK on purpose: the point is to reach *any*
    server speaking this shape — Ollama, vLLM, llama.cpp, a gateway of your own
    — and the request body is four keys. Taking on a vendor SDK to send them
    would buy nothing and rule out the servers that are not that vendor.
    """

    token_field: str = "max_tokens"
    transport: Any = None  # injectable for tests; None means the network

    def _headers(self) -> Dict[str, str]:
        headers = {"content-type": "application/json"}
        if self.settings.api_key:
            headers["authorization"] = f"Bearer {self.settings.api_key}"
        return headers

    def _url(self, path: str) -> str:
        base = self.settings.base_url.rstrip("/")
        if not base:
            raise ProviderError(
                "This provider needs a base URL — the address of the server, "
                "ending in /v1."
            )
        return f"{base}{path}"

    def _post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        post = self.transport or partial(
            _send, source=self.settings.base_url_source
        )
        status, text = post(self._url(path), self._headers(), body)
        if status >= 400:
            raise _HTTPRefusal(status, text)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderError(
                f"{self.settings.label} returned something that is not JSON: "
                f"{text[:300]}"
            ) from exc

    def complete(
        self,
        *,
        system: str,
        messages: List[Dict[str, Any]],
        schema: Dict[str, Any],
        model: str = "",
        max_tokens: int = 0,
    ) -> Reply:
        for _ in range(len(SCHEMA_MODES) + 1):
            body: Dict[str, Any] = {
                "model": model or self.settings.model,
                "messages": [
                    {"role": "system", "content": self._with_schema_prompt(system, schema)},
                    *messages,
                ],
                self.token_field: max_tokens or self.settings.max_tokens,
            }
            if self.schema_mode == "json_schema":
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "spec_engine_pass",
                        "strict": True,
                        "schema": portable_schema(schema),
                    },
                }
            elif self.schema_mode == "json_object":
                body["response_format"] = {"type": "json_object"}

            try:
                payload = self._post("/chat/completions", body)
            except _HTTPRefusal as refusal:
                if _wants_max_completion_tokens(refusal.body):
                    self.token_field = "max_completion_tokens"
                    continue
                if _is_schema_rejection(refusal.body) and self._degrade():
                    continue
                raise ProviderError(refusal.readable(self.settings.label)) from None

            return _reply_from_chat_completion(payload, self.settings.label)

        raise ProviderError(
            f"{self.settings.label} rejected every request shape this client knows."
        )

    def check(self) -> str:
        """Ask the endpoint what it has, before a compile finds out the hard way.

        Answers the three questions that otherwise surface mid-run: is the
        server there, does it have this model, and can that model actually
        hold a schema.
        """
        post = self.transport or partial(
            _send, source=self.settings.base_url_source
        )
        status, text = post(self._url("/models"), self._headers(), None)
        if status >= 400:
            raise ProviderError(_HTTPRefusal(status, text).readable(self.settings.label))

        catalogue = self._catalogue(text)
        model = self.settings.model
        if not catalogue:
            return f"Reached {self.settings.label}."
        if model and model not in catalogue:
            return (
                f"Reached the server ({len(catalogue)} models available), but "
                f"'{model}' is not one of them."
            )

        message = f"Reached {self.settings.label}. {len(catalogue)} models available."
        if _lacks_structured_outputs(catalogue.get(model)):
            # Not a failure — the client degrades on its own. But finding out
            # here is cheaper than finding out four passes in.
            message += (
                f" Note: {model} does not support structured outputs, so the "
                "schema will travel in the prompt rather than being enforced "
                "at generation. Expect more repair rounds."
            )
        return message

    @staticmethod
    def _catalogue(text: str) -> Dict[str, Dict[str, Any]]:
        try:
            entries = json.loads(text).get("data", [])
        except (json.JSONDecodeError, AttributeError):
            return {}
        return {
            entry["id"]: entry
            for entry in entries
            if isinstance(entry, dict) and entry.get("id")
        }


class _HTTPRefusal(Exception):
    """An HTTP error carrying the server's own words, unmangled."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status
        self.body = body or ""

    def readable(self, label: str) -> str:
        detail = self.body.strip()
        try:
            parsed = json.loads(detail)
            error = parsed.get("error", parsed)
            detail = error.get("message", detail) if isinstance(error, dict) else detail
        except (json.JSONDecodeError, AttributeError):
            pass
        return f"{label} refused the request (HTTP {self.status}): {detail[:400]}"


def _lacks_structured_outputs(entry: Optional[Dict[str, Any]]) -> bool:
    """Does the catalogue positively say this model cannot do schemas?

    `supported_parameters` is an OpenRouter extension. A server that does not
    publish it — Ollama, vLLM, most gateways — tells us nothing, and silence is
    not evidence, so the answer is False rather than a guess.
    """
    if not entry:
        return False
    published = entry.get("supported_parameters")
    if not isinstance(published, list):
        return False
    return "structured_outputs" not in published


def _reply_from_chat_completion(payload: Dict[str, Any], label: str) -> Reply:
    choices = payload.get("choices") or []
    if not choices:
        raise ProviderError(f"{label} returned no choices: {str(payload)[:300]}")
    message = choices[0].get("message") or {}
    text = message.get("content") or ""
    if isinstance(text, list):  # some servers return content parts
        text = "".join(
            part.get("text", "") for part in text if isinstance(part, dict)
        )
    usage = payload.get("usage") or {}
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
    return Reply(
        text=(text or "").strip(),
        input_tokens=int(usage.get("prompt_tokens") or 0),
        output_tokens=int(usage.get("completion_tokens") or 0),
        cache_read_input_tokens=int(cached or 0),
    )


_WEB_SCHEMES = {"http", "https"}


def _validate_url(url: str, source: str) -> None:
    """The URL policy for everything this module fetches, run before any
    bytes leave the process.

    Non-web schemes, embedded userinfo and — for visitor-typed URLs only —
    loopback/link-local/private/reserved hosts are refused here, so a refused
    request dies as a status-only ProviderError with no response body to
    reflect. Preset and operator-environment URLs skip the host check: the
    shipped local-model presets (Ollama, LM Studio, vLLM) are loopback on
    purpose.
    """
    parts = urlsplit(url)
    if parts.scheme.lower() not in _WEB_SCHEMES or parts.username is not None:
        raise ProviderError("Base URL must be a plain http(s) address.")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise ProviderError("Base URL is missing a host.")
    if source != "ui":
        return  # preset / operator env: the local model servers keep working
    try:
        lookups = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError) as exc:
        raise ProviderError(
            f"Could not resolve the host in {url}. Check the address."
        ) from exc
    for _family, _type, _proto, _canonname, sockaddr in lookups:
        ip = ipaddress.ip_address(sockaddr[0])
        if (
            ip.is_loopback
            or ip.is_link_local
            or ip.is_private
            or ip.is_reserved
        ):
            raise ProviderError(
                "This Base URL is not reachable from a hosted app."
            )


class _ValidatingRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect handler that re-runs the URL policy on every hop.

    urllib follows redirects with the original headers attached, so without
    this a first-hop host (or an open redirector on it) could launder the
    request to a target the policy just refused — with the Authorization
    header still attached. Every hop is validated against the provenance the
    chain started with, and the Authorization header is dropped when a hop
    changes host.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        provenance = getattr(req, "provenance", "ui")
        _validate_url(newurl, provenance)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        redirected.provenance = provenance  # every hop re-checks the origin
        if urlsplit(newurl).hostname != urlsplit(req.full_url).hostname:
            redirected.headers.pop("Authorization", None)
        return redirected


def _build_opener() -> urllib.request.OpenerDirector:
    """The stock opener, minus every handler that could answer a non-web
    scheme. No FileHandler, FTPHandler or DataHandler: the validator above is
    the gate, and this is the backstop — nothing here can turn a URL into a
    local file read even if the gate were bypassed. (build_opener cannot
    leave these out, so the opener is assembled by hand.)
    """
    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.ProxyHandler(),  # the operator's proxy config, honoured
        urllib.request.HTTPHandler(),
        urllib.request.HTTPSHandler(),
        urllib.request.HTTPErrorProcessor(),
        urllib.request.HTTPDefaultErrorHandler(),
        _ValidatingRedirects(),
    ):
        opener.add_handler(handler)
    return opener


_OPENER = _build_opener()


#: A compile on local hardware is slow; an unbounded wait is how one
#: black-holed request pins a worker thread for ten minutes.
DEFAULT_TIMEOUT_SECONDS = 300.0


def _request_timeout() -> float:
    """Seconds before a request is abandoned. Unset or unreadable → default."""
    try:
        timeout = float(os.getenv("SPEC_ENGINE_TIMEOUT_SECONDS", ""))
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    return timeout if timeout > 0 else DEFAULT_TIMEOUT_SECONDS


def _send(
    url: str,
    headers: Dict[str, str],
    body: Optional[Dict[str, Any]],
    source: str = "ui",
) -> Tuple[int, str]:
    """The only place this module touches the network.

    Standard library on purpose. The whole point of this seam is that someone
    can point the app at a model running on their own machine; making that path
    depend on a third-party HTTP client — one whose distribution name has
    already changed once under us — would be an odd way to spend a dependency.
    A JSON POST is a JSON POST.

    `source` is where the configuration that produced `url` came from — "ui"
    for a visitor-typed Base URL, "env" or "preset" otherwise — and defaults
    to the strictest policy. The URL is validated, provenance included, before
    any bytes leave the process; a refused target never dispatches, so there
    is no response body to reflect.
    """
    _validate_url(url, source)
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers=headers,
        method="GET" if body is None else "POST",
    )
    request.provenance = source  # rides along; the redirect handler re-checks
    try:
        with _OPENER.open(request, timeout=_request_timeout()) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as refusal:
        # The body of a 4xx carries the server's explanation, which is the
        # only part worth showing anyone.
        return refusal.code, refusal.read().decode("utf-8", "replace")
    except urllib.error.URLError as exc:
        raise ProviderError(
            f"Could not reach {url}. If this is a local server, check it is "
            f"running and the port is right. ({exc.reason})"
        ) from None
    except TimeoutError:
        raise ProviderError(
            f"{url} did not answer in time. Local models are slow; a smaller "
            "model or a lower response budget may be the fix."
        ) from None


# --------------------------------------------------------------------------- #
# Construction
# --------------------------------------------------------------------------- #


def build(settings: Optional[Settings] = None, **overrides: Any) -> Optional[Provider]:
    """Return a provider for these settings, or None if it cannot be used."""
    settings = settings or resolve_settings(overrides or None)
    if not settings.configured:
        return None
    if settings.kind == "anthropic":
        return AnthropicProvider(settings=settings)
    return OpenAICompatProvider(settings=settings)


def adapt(client: Any, settings: Optional[Settings] = None) -> Provider:
    """Accept a Provider, or wrap a Messages-API-shaped client as one.

    The pipeline takes a `client` argument that predates this module, and the
    test suite passes a scripted stand-in for `anthropic.Anthropic` directly.
    Both keep working.
    """
    if isinstance(client, Provider):
        return client
    return AnthropicProvider(
        settings=settings or resolve_settings(), client=client
    )
