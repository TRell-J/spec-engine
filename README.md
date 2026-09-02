# Spec-Engine

**An agent will build whatever your document implies. This checks that the document actually said it.**

Paste a PRD, discovery notes, or workshop scribbles. Get requirements an autonomous agent can execute, where every single one traces back to the sentence it came from — and a build that fails if one doesn't.

It is deliberately not a prompt wrapper around "turn this PRD into tickets." The design choices — citation grounding, a requirements grammar with a parser behind it, a deterministic verification gate, and a human approval step before any requirement is written — are the point.

It runs on whatever model you point it at: Claude, GPT, Gemini, or an open-weight model on your own laptop. None of the guarantees come from the model.

Live demo: _deploy to Streamlit Community Cloud and drop the URL here._

---

## Why this exists

**A specification is an import job.** Messy input, crossing a boundary, into a system that is about to act on it.

Everywhere else we treat that moment seriously. Nobody lets an unvalidated CSV into a production database — you check it at the boundary, reject the rows that fail, show a human exactly what broke, and let them fix it before anything lands. Then you let it through.

Documents get handed to coding agents with none of that. A paragraph of prose goes in and a week of work comes out, and the only validation anywhere in the loop is someone reading the output and hoping.

Agents do not fail on hard requirements. They fail on absent ones.

Hand an agent a PRD that says "approvals should be fast and secure" and it will invent a latency threshold, invent a permission model, and write confident code around both. Nothing looks wrong until three commits later, when the thing it invented meets the thing you assumed.

The gap is not model capability. It is that the specification never contained the decision, and nothing in the workflow forced anyone to notice. Spec-Engine is built around the two questions that close that gap:

1. **What does the document actually say?** Every extracted claim carries a verbatim quote, and that quote is checked against your document. A claim whose quote is not found fails the build. A model cannot cite a sentence it never saw, which turns hallucination from a judgement call into a mechanical error.
2. **What does the document never say?** A dedicated pass surfaces the decisions nobody made — system of record, permission model, retry behaviour, what happens to in-flight work — each with the default that ships if you do not answer. You settle them *before* a single requirement is written.

A specification tool, not a replacement for product judgement, architecture review, or the conversation with the customer that should have produced the document in the first place.

### Where it sits

**In front of a build agent, not instead of one.** It writes no code and runs no tasks. It produces the artifacts a coding agent, an agentic workspace, or a human team builds from, plus `handoff.txt` — the instruction that hands them over and states which properties already hold:

```
1. tasks.md is ordered into 4 waves. Complete a wave before starting the next.
2. Every task names the requirements it satisfies. Do not build anything that
   no requirement asks for — if you think something is missing, say so instead
   of adding it.
3. The acceptance criteria in requirements.md are written in EARS notation and
   each one names an outcome a test can assert. Satisfy them literally; do not
   soften a threshold, status code, or message.
...
5. Every requirement traces back to a quoted sentence from the source document
   — see traceability.md. If a requirement looks wrong, read the quote it came
   from before changing it.
```

Nothing in that prompt is a request. The waves come from the dependency graph, the criteria parsed as EARS, and every quote was found in the source — or the build failed. The agent is not being asked to trust the document. It is being told which properties were already checked, and where to look when one seems wrong.

---

## What it does

1. **Extract** — reads the document and records every commitment it contains, each with a verbatim quote and a line number. Quotes are re-checked against the source afterwards; line numbers are repaired locally rather than trusted.
2. **Interrogate** — surfaces the decisions the document leaves open, each with the concrete default that will ship unanswered. Presented one question per screen, with Back and Skip.
3. **Specify** — writes requirements in EARS notation, each tracing to the claims and decisions it rests on. A requirement that traces to nothing is rejected as an invention.
4. **Decompose** — turns requirements into agent-executable tasks in dependency order, each naming the requirements it satisfies and the command that verifies it.
5. **Verify** — runs a deterministic gate over the result, hands any blocking defects back to the model for repair, and re-verifies. Fails closed rather than returning a half-valid spec.

Each pass is a separate API call pinned to a JSON Schema derived from the Pydantic model, re-validated locally, and repaired in-conversation on failure. The separation is deliberate: one call that reads prose and emits a task list has nowhere to put the evidence or the open questions.

---

## What it produces

| Artifact | Purpose |
| --- | --- |
| `requirements.md` | User stories with EARS acceptance criteria. The document a human approves. |
| `design.md` | Architecture, every decision and whether a human answered it or a default was assumed, scope boundary, risks. |
| `tasks.md` | Wave-ordered checklist, one verification command per task. This is the file you point a coding agent at. |
| `traceability.md` | Source quote → claim → requirement → task, in both directions. |
| `spec.json` | The full structured document. |
| `plan.mmd` | Task dependency graph as Mermaid. |
| `handoff.txt` | The instruction that hands the set to an agent, naming the waves, the scope boundary, and any decision no human answered. |

The requirements / design / tasks split follows the convention [GitHub Spec Kit](https://github.com/github/spec-kit) and [Amazon Kiro](https://kiro.dev/docs/specs/) have both settled on, so the output drops into an existing agent workflow rather than requiring one be built around it.

A representative requirement:

```markdown
## REQ-001 — Discount approval gate

**Priority:** must  ·  **Derived from:** `CLM-001`, `DEC-001`

> As a deal desk analyst, I want quotes over the discount threshold held for
> approval, so that no rep can send a discount the business has not agreed to.

**Acceptance criteria**

AC-1. WHEN a rep submits a quote with a discount greater than 20%, the quote
      service SHALL set the quote status to `pending_approval` and return HTTP 202

AC-2. IF a rep attempts to send a quote whose status is `pending_approval`, THEN
      the quote service SHALL reject the request with HTTP 403 and the error
      code `approval.required`

**Delivered by:** `TASK-001`, `TASK-002`, `TASK-007`
```

---

## What makes it credible

### Grounding is enforced, not requested

Prompts asking a model to "only use the source document" are a preference. Here, every claim carries a quote, and `verify()` goes and looks for that quote in the document you pasted. A quote that is not found is a **blocking** defect and the build stops. The check is a substring match after whitespace normalisation — unglamorous, and impossible to talk your way past.

### EARS is a parser, not a word list

Acceptance criteria are written in [EARS](https://en.wikipedia.org/wiki/Easy_Approach_to_Requirements_Syntax) (Easy Approach to Requirements Syntax, Mavin et al., IEEE RE'09), the requirements grammar used in aerospace, automotive, and medical software. Six sentence templates, a closed keyword set:

```
THE <system> SHALL <response>
WHEN <trigger>, the <system> SHALL <response>
WHILE <precondition>, the <system> SHALL <response>
WHERE <feature>, the <system> SHALL <response>
IF <trigger>, THEN the <system> SHALL <response>
WHILE <precondition>, WHEN <trigger>, the <system> SHALL <response>
```

"The system should be fast" is not rejected because *fast* appears on a blocklist. It is rejected because the grammar has nowhere to put it, and because the clause after `SHALL` must name something a test can assert — a threshold, a status code, an exact message, a count. The parser also catches hedging (`SHALL try to`) and compound criteria hiding two behaviours in one sentence.

### The gate is deterministic

Verification runs no model at all:

| Check | Severity |
| --- | --- |
| Every claim quote appears in the source document | blocking |
| Every acceptance criterion parses as EARS | blocking |
| Every requirement is delivered by at least one task | blocking |
| Response clauses name a measurable outcome and do not hedge | major |
| Claims that reached no requirement (silently dropped scope) | major |
| Task verification is not a runnable command | major |
| Duplicate requirements, oversized tasks, assumed defaults | minor |

The output is a defect list with a fix hint per finding — not a score. There is deliberately no 0–100 number anywhere in the tool. An earlier version had one; it rated every real PRD near zero, which is alarming and unactionable.

### The human is in the loop before the work, not after

Review screens are editable. Correct a quote, delete a claim, add one the reader missed, rewrite an acceptance criterion with the grammar checked as you type. Corrections are held to the same standard as the model's output: a hand-typed quote is grounded against your document, a hand-edited criterion still has to parse. Editing is a way through the gate, not a hole in it, and every edit re-runs verification immediately.

### The model is a swappable part

Every property above — the quote check, the EARS parser, the traceability graph, the defect list — is deterministic Python that runs *after* the model answers. So which model answers is a setting, not an architectural commitment. Pick a provider on the first screen, or set `SPEC_ENGINE_PROVIDER`; a model running on your own machine needs no key and costs nothing.

Two wire protocols cover the field: the Anthropic Messages API, and `/v1/chat/completions`, which OpenAI, Google, OpenRouter, Groq, Together, DeepSeek, Mistral, Ollama, LM Studio, vLLM and llama.cpp all speak. Structured output is where providers genuinely differ, so it degrades instead of failing — schema enforced at generation, then JSON mode with the schema in the prompt, then the prompt alone. A server that rejects one tier drops to the next, once, and remembers. Every tier ends in the same place: the response is validated against the Pydantic model locally and repaired in-conversation if it does not fit.

The open-weight path is reached with the standard library — no vendor SDK — so pointing this at a model on your own hardware adds no dependency to the install.

### It refuses rather than fabricates

With no model configured the compiler does not run. It will not generate a spec from a template and present it as a compile of your document. Three hand-authored walkthroughs are available instead, each labelled as an example on every screen, and each held to the same verification gate a live compile faces.

---

## Architecture

| Component | Choice | Why |
| --- | --- | --- |
| Compiler | Four passes, over any Anthropic or OpenAI-compatible endpoint | Separating extract / interrogate / specify / decompose gives evidence and open questions somewhere to live |
| Provider layer | Two wire protocols, structured output that degrades | The gate is deterministic, so the model is a setting rather than a dependency |
| Output contract | Structured outputs pinned to a JSON Schema | Schema adherence is enforced at generation, then re-validated locally |
| Data model | Pydantic v2 | ID formats, resolvable traces, and acyclicity are type-level facts, not conventions |
| Requirements grammar | EARS, hand-written parser | A sentence either parses or it does not; no model in the loop |
| Verification | Deterministic Python | The compiler may be probabilistic; the gate is not |
| UI | Streamlit, four-screen wizard | One question per screen, borrowed from the GOV.UK "one thing per page" pattern |
| Visual language | Adapted from obvious.ai — palette and proportions measured from the live site | A prototype aimed at a company should speak that company's design language |
| Persistence | Local JSON, written atomically | A compile costs money; a browser refresh must not destroy it |
| Cost | Estimated before, measured after | Nobody should click a button that spends money without a number attached |

---

## Running it

```bash
git clone https://github.com/TRell-J/spec-engine.git && cd spec-engine
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

```bash
streamlit run app.py
```

The compiler needs a model. Choose one in the **Model** panel on the first screen, or set it once in a `.env` (`cp .env.example .env`). With nothing configured, the three walkthroughs still run with no API calls at all — the compiler refuses rather than inventing a spec.

### Pointing it at a different model

| Preset | Endpoint | Key |
| --- | --- | --- |
| Anthropic — Claude | Messages API | `ANTHROPIC_API_KEY` |
| OpenAI | `api.openai.com/v1` | `OPENAI_API_KEY` |
| Google — Gemini | OpenAI-compatible endpoint | `GEMINI_API_KEY` |
| OpenRouter | `openrouter.ai/api/v1` | `OPENROUTER_API_KEY` |
| Groq · Together · DeepSeek · Mistral | each vendor's `/v1` | that vendor's variable |
| Ollama · LM Studio · vLLM | `localhost` | none needed |
| Custom | any `/v1/chat/completions` URL | optional |

Running it entirely on your own hardware:

```bash
ollama serve && ollama pull qwen3:32b
```

Then pick **Ollama — local** and set the model to `qwen3:32b`. Nothing leaves the machine and nothing is billed.

**Check connection** answers the three questions that otherwise surface mid-compile: whether the server is reachable, whether it has the model you named, and — where the endpoint publishes capabilities, as OpenRouter does — whether that model can hold a JSON schema at all. A model that cannot is not a failure; the client falls back to putting the schema in the prompt. But it costs repair rounds, and knowing before you start is the point.

Model choice is not free of consequence. This is a long structured-output job: the extraction pass has to hold a schema across a whole document, and small models tend to drift out of it. Below roughly 30B parameters, expect the repair loop to work hard and the compile to fail outright rather than hand back something half-valid — which is the correct outcome, but it is not a free lunch.

| Variable | Default | Purpose |
| --- | --- | --- |
| `SPEC_ENGINE_PROVIDER` | `anthropic` | Which preset to start from |
| `SPEC_ENGINE_MODEL` | the preset's default | Model used for every pass |
| `SPEC_ENGINE_BASE_URL` | the preset's default | For a self-hosted or proxied endpoint |
| `SPEC_ENGINE_API_KEY` | _(unset)_ | Fallback for a provider with no conventional variable |
| `SPEC_ENGINE_SCHEMA_MODE` | the preset's default | Force a structured-output tier instead of degrading |
| `SPEC_ENGINE_MAX_TOKENS` | `16000` | Response budget per pass |
| `SPEC_ENGINE_INPUT_RATE` / `_OUTPUT_RATE` | _(unset)_ | USD per million tokens, to price a model the app does not know |
| `SPEC_ENGINE_STORE` | `.spec_engine` | Where the current run is saved |

A compile is four model calls. On a frontier model a one-page PRD lands in the low tens of cents; on your own hardware it is free. The app shows an estimated range before you spend and the measured figure afterwards — and where it has no published rate for a model, it shows the token counts and says so rather than guessing.

### Tests

```bash
pytest -q
```

383 tests, fully offline. An autouse fixture clears credentials and the pipeline runs against a scripted fake client, so the suite never reaches a vendor or spends a token. `tests/test_over_http.py` is the exception worth naming: it starts an OpenAI-compatible server on a loopback port and compiles a whole spec through it — real request encoding, real status handling, real usage accounting — because the one bug that got past the unit tests was in none of the units. The three examples double as fixtures, and `tests/test_examples.py` verifies each one against its own document — a hand-authored example that failed its own gate would be worse than having no example.

---

## Limitations (stated plainly)

- **The prompts have never run against a paid API.** Every deterministic path — grounding, EARS parsing, traceability, the repair loop, the wire protocols, the full UI — is tested, the last of those against a real HTTP server. Prompt *quality* is the one thing the suite cannot vouch for, and it is the thing most likely to vary between models.
- **The prompts were written against Claude and are not tuned per model.** They are plain instructions with a schema attached, so they should travel, but a smaller open-weight model will need more repair rounds and may not clear the gate at all. The tool fails closed when that happens, which is the right behaviour and still a real limit.
- **A decision-support tool, not an autonomous pipeline.** It produces a specification a person should read. Pointing an agent at `tasks.md` without reading `design.md` first defeats the purpose of the interrogation pass.
- **Single user, local.** The run is saved to a JSON file on disk. No auth, no multi-user state, no server-side storage.
- **The API key lives in memory for the session** when pasted into the UI. It is never written to disk, but there is no secret management beyond that.
- **Untested at document scale.** A forty-claim PRD will produce a long scroll on the review screen with no grouping or filtering.
- **Cost figures are estimates.** Input tokens are approximated from character length; the post-run figure uses reported usage and ignores cache effects.
- **The claims editor is not covered end-to-end.** Streamlit's `AppTest` cannot drive `st.data_editor`, so the transformation is unit-tested in `core/editing.py` while the UI wiring is covered only as far as Save and Cancel.
- **All example data is fictional.** The deal desk, GenAI search, and RevOps scenarios are invented for demonstration and reflect no real customer.
- **The visual language is adapted from obvious.ai**, whose palette and proportions were measured from their public site. No logo, wordmark, or brand asset is used, and nothing here is endorsed by or affiliated with them. Their typeface (Booton) is licensed, so Figtree stands in for it.

---

## Roadmap

- Run the four prompts against the live API and tune them against a held-out set of real documents
- Diff view between compiles, so changing one answer shows what moved rather than producing a fresh wall of text
- Write artifacts straight into a repository's `specs/` directory instead of downloading them
- Grouping and filtering on the review screen for documents that produce thirty or more claims
- A cheaper model for the extraction pass, where the work is closer to reading than reasoning
- Per-model prompt profiles, so a smaller open-weight model gets a shorter schema and more passes instead of one it cannot hold

---

## Author

**Terrell Johnson** — AI Strategy & Operations, Atlanta GA. I advise on AI value and then build the thing.

Built as a portfolio prototype exploring what it takes to make an AI-generated specification trustworthy enough to hand to an autonomous agent: source grounding, a real requirements grammar, a deterministic gate, and a human decision point before the work starts.

Disclaimer: an independent portfolio prototype using fictional data. Not affiliated with, endorsed by, or sponsored by any company named in its examples. For questions or feedback, please open an issue on this repository.
