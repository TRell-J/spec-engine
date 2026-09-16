---
name: local-dev
description: Bring TRell-J/spec-engine to a working local dev environment and verify it is healthy
---

# local-dev — Spec-Engine bring-up record

Recorded 2026-09-03 from a verified run (live session `isqx0rqfptvf7jvz9xylc`, later captured as the sandbox template).

## What this repo needs

Nothing external. No database, no Docker, no services, no required env vars. A venv plus `pip install -r requirements.txt` is the entire setup. With no API key the app refuses to fabricate a spec and offers three hand-authored walkthroughs instead; the test suite is fully offline by design — `conftest.py` scrubs every provider env var and substitutes a scripted fake client, so it can never reach a vendor or spend a token.

## Bring-up sequence

1. `python3 -m venv .venv` — Python 3.13.14 was used.
2. `.venv/bin/pip install -r requirements.txt` — resolves streamlit 1.63.0, pydantic 2.13.5, python-dotenv 1.2.3, pytest 9.1.1, anthropic 1.3.0.
3. `.venv/bin/python -m pytest -q` — expect **393 passed** in ~40s.
4. `nohup .venv/bin/streamlit run app.py > /tmp/streamlit.log 2>&1 &`
5. Parse the port from the startup log's `Local URL:` line (8501 in the verified run). No port is pinned anywhere in the repo — do not assume.
6. `curl http://localhost:8501/_stcore/health` → `ok` (HTTP 200); `curl http://localhost:8501/` serves the Streamlit page.

## Primary flow (keyless path)

The "Deal desk" walkthrough through the same verification gate a live compile faces — this is what a user without an API key sees:

```python
from examples import registry
from core.verifier import verify, assess_source
from core.exporter import build_bundle

ex = registry.get("deal_desk")
readiness = assess_source(ex.document)    # compilable=True, 107 words
report = verify(ex.spec(), ex.document)   # passed=True, 0 findings of any severity
bundle = build_bundle(ex.spec(), report)  # 7 artifacts
```

Verified output: gate passed with `{'blocker': 0, 'major': 0, 'minor': 0}`, and all seven artifacts (`requirements.md`, `design.md`, `tasks.md`, `traceability.md`, `spec.json`, `plan.mmd`, `handoff.txt`) generated.

## Gotchas

- All env vars are optional (`.env.example` documents them). Do not commit a `.env`.
- `.spec_engine/` is local working state — the saved run — and is gitignored.
- `tests/test_over_http.py` binds a loopback port for a full compile over HTTP; offline, but it needs a free local port.
- `.streamlit/config.toml` sets `headless = true` — the app never opens a browser.
- No lint/typecheck configuration ships in the repo; `pytest -q` is the verification command.
- No browser automation is installed in this sandbox. UI liveness was verified via the health endpoint, the served page HTML, and the programmatic walkthrough above; `tests/test_app.py` (549 lines) covers the screen logic.
