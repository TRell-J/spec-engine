# Codebase map — TRell-J/spec-engine

Single app, ~10.4k lines of Python, no sub-apps. Depth capped at 2.

| Path | ~Lines | What it is |
| --- | --- | --- |
| `app.py` | 1324 | The entire Streamlit UI — four-screen wizard: Document → What I read → Open questions → Your spec |
| `theme.py` | 328 | Visual-language helpers for the UI (palette adapted from obvious.ai, proportions only) |
| `conftest.py` | 128 | Makes the repo importable from any cwd; autouse fixture scrubs every provider env var and swaps in a scripted fake client so tests can never reach a vendor |
| `.streamlit/config.toml` | — | Theme palette, fonts, `headless = true`, `gatherUsageStats = false` |
| `.env.example` | — | Every optional env var, documented inline |
| `requirements.txt` | — | streamlit, pydantic, python-dotenv, pytest, anthropic |
| `core/` | 2882 | The engine — everything deterministic runs here, after the model answers |
| `core/providers.py` | 790 | Provider catalogue/presets (Anthropic, OpenAI, Google, OpenRouter, Groq, Together, DeepSeek, Mistral, Ollama, LM Studio, vLLM, custom), wire protocols, structured-output tiering with graceful step-down |
| `core/pipeline.py` | 708 | The four model passes — extract, interrogate, specify, decompose — plus the repair loop and settings resolution |
| `core/schemas.py` | 415 | Pydantic v2 data model: source claims, open decisions, EARS requirements, tasks, traceability |
| `core/exporter.py` | 372 | Builds the seven output artifacts (requirements/design/tasks/traceability/spec.json/plan.mmd/handoff.txt) |
| `core/verifier.py` | 363 | The deterministic gate: quote grounding, EARS parse, task coverage, severity-ranked findings; `assess_source` gives a pre-compile readiness read |
| `core/ears.py` | 290 | Hand-written EARS grammar parser (THE/WHEN/WHILE/WHERE/IF templates, closed keyword set, hedging detection) |
| `core/pricing.py` | 195 | Cost estimate before a compile, measured usage after; token counts when no rate is known |
| `core/store.py` | 158 | Atomic local JSON persistence into `.spec_engine/` |
| `core/editing.py` | 91 | Review-screen edits, held to the same gate as model output |
| `examples/` | 1492 | Three hand-authored scenarios (all fictional) |
| `examples/registry.py` | 75 | The example catalogue — the app derives both preset documents and walkthroughs from this one list |
| `examples/genai_search.py` | 496 | GenAI search evaluation scenario |
| `examples/revops_ingestion.py` | 488 | RevOps ingestion scenario |
| `examples/reference.py` | 433 | Deal desk approval routing — the reference spec every invariant is checked against |
| `tests/` | 3494 | 13 files, 393 tests, fully offline |
| `tests/test_app.py` | 549 | UI logic, all four screens |
| `tests/test_providers.py` | 552 | Presets, wire protocols, tiered structured output |
| `tests/test_pipeline.py` | 428 | The four passes and repair, via the scripted fake client |
| `tests/test_features.py` | 391 | Cross-cutting features |
| `tests/test_schemas.py` | 263 | Data-model invariants (ID formats, resolvable traces, acyclicity) |
| `tests/test_exporter.py` | 236 | The seven artifacts |
| `tests/test_store.py` | 223 | Atomic persistence |
| `tests/test_verifier.py` | 210 | The gate |
| `tests/test_over_http.py` | 181 | Spins an OpenAI-compatible server on a loopback port and compiles a whole spec through it — the one integration test |
| `tests/test_editing.py` | 181 | Edits held to the gate |
| `tests/test_ears.py` | 157 | The grammar parser |
| `tests/test_examples.py` | 142 | Every example passes the same gate a live compile faces |
| `tests/test_walkthroughs.py` | 129 | The keyless walkthrough path |
| `tests/test_pricing.py` | 117 | Estimates and measured usage |
