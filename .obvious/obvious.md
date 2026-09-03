# Spec-Engine — agent guide

**Repo:** TRell-J/spec-engine — an applied-AI prototype that turns an unstructured product document (PRD, discovery notes, workshop scribbles) into requirements an autonomous coding agent can execute, where every requirement traces to a verbatim quoted sentence in the source and the build fails if one doesn't. Single-user, local, no server-side state. Live demo: https://spec-engine.streamlit.app

## Stack

| Layer | Choice |
| --- | --- |
| Language | Python 3.13 (plain `requirements.txt` — no pyproject/setup.py) |
| Package manager | pip inside a venv (`.venv/`, gitignored) |
| UI | Streamlit — four-screen wizard in `app.py`, themed by `theme.py` + `.streamlit/config.toml` |
| Data model | Pydantic v2 (`core/schemas.py`) |
| LLM access | Anthropic Messages API (SDK) + any OpenAI-compatible `/v1/chat/completions` via stdlib (`core/providers.py`) |
| Persistence | Local JSON written atomically (`core/store.py` → `.spec_engine/`, gitignored) |
| Tests | pytest — 393 tests, fully offline by design |
| External services | **None.** No database, no Docker, no workers. The only process is the Streamlit server |

## Commands

| Task | Command |
| --- | --- |
| Setup | `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt` |
| Run app | `.venv/bin/streamlit run app.py` |
| Tests | `.venv/bin/python -m pytest -q` |
| One file | `.venv/bin/python -m pytest tests/test_ears.py -q` |

`.streamlit/config.toml` sets `headless = true`, so no browser opens. The port is not pinned anywhere in the repo — Streamlit's startup log prints the real `Local URL:` (8501 in the verified run); parse it from the log rather than assuming.

## Environment

Every env var is optional; there is no required configuration (see `.env.example`). With nothing set the app runs three hand-authored walkthroughs through the same verification gate a live compile faces. Provider keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, …) enable live compiles; `SPEC_ENGINE_PROVIDER` / `SPEC_ENGINE_MODEL` / `SPEC_ENGINE_BASE_URL` override presets. Do not commit a `.env` — `conftest.py` scrubs all of these during tests anyway.

## Codebase map

Full map in `codebase-map.md`. In brief: `app.py` is the entire UI (1.3k lines, four screens — Document → What I read → Open questions → Your spec). `core/` is the engine: schemas, the provider catalogue, the four-pass pipeline with repair, the hand-written EARS parser, the deterministic verifier (no model in the gate), the seven-artifact exporter, pricing, and the atomic store. `examples/` holds three hand-authored scenarios (fictional data) that back the preset documents and walkthroughs. `tests/` mirrors it all with 393 offline tests.

## Local Verification Summary

Run 2026-09-03, Python 3.13.14, streamlit 1.63.0, pydantic 2.13.5, pytest 9.1.1:

- `pytest -q` → **393 passed** in 38.6s, zero network calls (autouse offline fixture).
- `streamlit run app.py` → served on port 8501 (parsed from startup log).
- `curl http://localhost:8501/_stcore/health` → `ok`, HTTP 200.
- `curl http://localhost:8501/` → Streamlit page HTML served.
- Primary flow (keyless): "Deal desk" walkthrough → `assess_source` (compilable, 107 words) → `verify` against the source document → **gate passed, 0 findings** → `build_bundle` produced all 7 artifacts (`requirements.md`, `design.md`, `tasks.md`, `traceability.md`, `spec.json`, `plan.mmd`, `handoff.txt`).
- No lint/typecheck config ships in the repo (`ruff` appears only as a gitignored cache entry); the test suite is the repo's own verification command.

## Snapshot

Sandbox captured **2026-09-03T15:46:18Z** — live session `isqx0rqfptvf7jvz9xylc`, template `fczgan6mtgjg0e97uwpd:default`. Baked in: Python 3.13.14, `.venv/` with `requirements.txt` installed, app verified healthy on port 8501, 393-test suite green. Bring-up details: `skills/local-dev/SKILL.md`.
