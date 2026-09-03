# Spec-Engine — Specification Verification

An applied-AI prototype that turns an unstructured product document (a PRD, discovery notes, workshop scribbles) into requirements an autonomous coding agent can execute — where every requirement traces back to a quoted sentence in the source, and the build fails if one doesn't. Live demo: https://spec-engine.streamlit.app · Runs with no API key (three hand-authored walkthroughs, held to the same verification gate), or bring your own key for any Anthropic or OpenAI-compatible model, hosted or local.

The premise in one line: **an agent will build whatever your document implies, so something has to check that the document actually said it.**

---

## Why this exists

A specification is an import job — messy input, crossing a boundary, into a system about to act on it. Everywhere else we treat that moment seriously. Nobody lets an unvalidated CSV into a production database: you check it at the boundary, reject what fails, show a human exactly what broke, and only then let it through.

Documents get handed to coding agents with none of that. Give an agent a PRD saying "approvals should be fast and secure" and it will invent a latency threshold, invent a permission model, and write confident code around both. Nothing looks wrong until three commits later, when the thing it invented meets the thing you assumed.

Agents don't fail on hard requirements. They fail on absent ones. So the tool is built around two questions:

1. **What does the document actually say?** Every extracted claim carries a verbatim quote, checked against your document. A claim whose quote isn't found fails the build. A model cannot cite a sentence it never saw, which turns hallucination from a judgement call into a mechanical error.
2. **What does the document never say?** A dedicated pass surfaces the decisions nobody made — system of record, permission model, retry behaviour, what happens to in-flight work — each with the default that ships if you don't answer. You settle them *before* a single requirement is written.

---

## What it does

Five passes, each a separate API call pinned to a JSON Schema and re-validated locally:

1. **Extract** — records every commitment in the document, each with a verbatim quote and line number.
2. **Interrogate** — surfaces the decisions the document leaves open, one question per screen, each with the default that ships unanswered.
3. **Specify** — writes requirements in EARS notation, each tracing to the claims and decisions it rests on. A requirement tracing to nothing is rejected as an invention.
4. **Decompose** — turns requirements into agent-executable tasks in dependency order, each naming what it satisfies and the command that verifies it.
5. **Verify** — runs a deterministic gate, hands blocking defects back for repair, and re-verifies. Fails closed rather than returning a half-valid spec.

The output is seven files: `requirements.md`, `design.md`, `tasks.md`, `traceability.md`, `spec.json`, `plan.mmd`, and `handoff.txt` — the instruction that hands the set to an agent and states which properties already hold. The requirements/design/tasks split follows the convention [GitHub Spec Kit](https://github.com/github/spec-kit) and [Amazon Kiro](https://kiro.dev/docs/specs/) both settled on, so the output drops into an existing agent workflow rather than requiring one be built around it.

---

## What makes it credible (the interesting part)

### Grounding is enforced, not requested

Prompts asking a model to "only use the source document" are a preference. Here every claim carries a quote, and the verifier goes and looks for that quote in the document you pasted. Not found is a **blocking** defect and the build stops. The check is a substring match after whitespace normalisation — unglamorous, and impossible to talk your way past.

### EARS is a parser, not a word list

Acceptance criteria are written in [EARS](https://en.wikipedia.org/wiki/Easy_Approach_to_Requirements_Syntax) (Mavin et al., IEEE RE'09), the requirements grammar used in aerospace and medical software: six sentence templates keyed on THE / WHEN / WHILE / WHERE / IF, and a closed keyword set.

"The system should be fast" isn't rejected because *fast* is on a blocklist. It's rejected because the grammar has nowhere to put it, and because the clause after SHALL must name something a test can assert — a threshold with units, a status code, an exact message, a count. The parser also catches hedging (`SHALL try to`) and compound criteria hiding two behaviours in one sentence.

### The gate is deterministic

Verification runs no model at all:

| Check | Severity |
| --- | --- |
| Every claim quote appears in the source document | blocking |
| Every acceptance criterion parses as EARS | blocking |
| Every requirement is delivered by at least one task | blocking |
| Response clauses name a measurable outcome and don't hedge | major |
| Claims that reached no requirement (silently dropped scope) | major |
| Task verification is not a runnable command | major |
| Duplicate requirements, oversized tasks, assumed defaults | minor |

The output is a defect list with a fix hint per finding — **not a score**. There is deliberately no 0–100 number anywhere. An earlier version had one; it rated every real PRD near zero, which is alarming and unactionable.

Review screens are editable, and corrections are held to the same standard as the model's output: a hand-typed quote is grounded against your document, a hand-edited criterion still has to parse. Editing is a way through the gate, not a hole in it.

### The model is a swappable part

Every property above is deterministic Python that runs *after* the model answers, so which model answers is a setting. Two wire protocols cover the field — the Anthropic Messages API, and `/v1/chat/completions`, which OpenAI, Google, OpenRouter, Groq, Together, DeepSeek, Mistral, Ollama, LM Studio and vLLM all speak. Structured output degrades rather than failing: schema enforced at generation, then JSON mode with the schema in the prompt, then the prompt alone. A server that rejects one tier drops to the next and remembers.

### It refuses rather than fabricates

With no model configured the compiler doesn't run. It will not generate a spec from a template and present it as a compile of your document. Three hand-authored walkthroughs are available instead, each labelled as an example on every screen, and each held to the same gate a live compile faces.

---

## Architecture & design decisions

| Component | Choice | Why |
| --- | --- | --- |
| Compiler | Four passes over any Anthropic or OpenAI-compatible endpoint | Separating extract / interrogate / specify / decompose gives evidence and open questions somewhere to live |
| Output contract | Structured outputs pinned to a JSON Schema | Adherence enforced at generation, then re-validated locally |
| Data model | Pydantic v2 | ID formats, resolvable traces and acyclicity are type-level facts, not conventions |
| Requirements grammar | EARS, hand-written parser | A sentence either parses or it doesn't; no model in the loop |
| Verification | Deterministic Python | The compiler may be probabilistic; the gate is not |
| UI | Streamlit, four-screen wizard | One question per screen, after the GOV.UK "one thing per page" pattern |
| Persistence | Local JSON, written atomically | A compile costs money; a browser refresh must not destroy it |

---

## Running it

The [live demo](https://spec-engine.streamlit.app) needs no installation and no key — the three walkthroughs run with zero API calls. To run it yourself:

```bash
git clone https://github.com/TRell-J/spec-engine.git && cd spec-engine
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Pick a model in the **Model** panel on the first screen, or set it once in a `.env` (`cp .env.example .env`). Each provider reads its own conventional key — `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY` — and `SPEC_ENGINE_PROVIDER` / `SPEC_ENGINE_MODEL` override the defaults. A model on localhost needs no key. **Check connection** verifies the endpoint, the model, and whether that model can hold a JSON schema before you spend anything.

A compile is four model calls — low tens of cents on a frontier model, free on your own hardware. The app estimates before you spend and measures after; where it has no published rate for a model it shows token counts and says so rather than guessing.

`pytest -q` runs 393 tests, fully offline. An autouse fixture clears credentials and the pipeline runs against a scripted fake client, so the suite never reaches a vendor or spends a token. One exception is worth naming: `tests/test_over_http.py` starts an OpenAI-compatible server on a loopback port and compiles a whole spec through it, because the one bug that got past the unit tests was in none of the units.

---

## Limitations (stated plainly)

- **The prompts work but are not tuned.** They have run against live APIs and produced correct specs, but they have not been tuned against a corpus of real documents, and quality varies by model — a weaker model clears the gate with more repair rounds, or not at all.
- **A decision-support tool, not an autonomous pipeline.** It produces a specification a person should read. Pointing an agent at `tasks.md` without reading `design.md` first defeats the purpose of the interrogation pass.
- **Single user, local.** The run is saved to a JSON file on disk. No auth, no multi-user state, no server-side storage. A pasted API key lives in memory for the session and is never written to disk, but there is no secret management beyond that.
- **Untested at document scale.** A forty-claim PRD produces a long scroll on the review screen with no grouping or filtering.
- **All example data is fictional.** The deal desk, GenAI search and RevOps scenarios are invented for demonstration and reflect no real customer.

---

## Roadmap

- Tune the four prompts against a held-out set of real documents, and publish what changed
- Per-model prompt profiles, so a smaller open-weight model gets a schema it can actually hold
- Diff view between compiles, so changing one answer shows what moved
- Write artifacts straight into a repository's `specs/` directory instead of downloading them

---

## Author

**Terrell Johnson** — strategy and operations leader focused on applied-AI prototyping and product execution. This project demonstrates end-to-end ownership of an applied-AI tool: problem framing, schema and grammar design, a deterministic evaluation gate, provider architecture, UI/UX, and shipping.

- GitHub: [@TRell-J](https://github.com/TRell-J)

An independent portfolio prototype using fictional data. Not affiliated with, endorsed by, or sponsored by any company named in its examples.
