# CLAUDE.md — guidance for AI agents working in this repo

## What this project is

A local-first pipeline that turns a YouTube playlist into reusable artifacts
(a Claude Skill, a report, a summary, cited quotes) and answers questions about
it via RAG. Mechanical work (download, transcribe, clean) runs locally and free;
only the "thinking" phases call Claude. See `README.md` and `docs/PRD.md`.

## Conventions to follow

- **Scripts are flat modules in `scripts/`, run as `python scripts/<name>.py`.**
  Sibling imports (`import scope`, `from embeddings import get_embedder`) resolve
  because the script's own dir is on `sys.path` at runtime. Keep this pattern.
- **Two adapter seams isolate replaceable dependencies:** `claude_client.py`
  (the LLM) and `embeddings.py` (the embedding model). Pipeline code depends on
  these interfaces, never on a vendor SDK directly. New model/provider work goes
  behind the seam.
- **Optional/heavy deps are lazy-imported inside functions** (numpy, fastembed,
  vendor SDKs), so importing a module never requires them. CI sanity-imports
  every script with only `requirements.txt` installed — don't add top-level
  heavy imports.
- **Every phase writes an inspectable flat file** under `output/` or
  `distilled/`. Preserve that; it's a core design principle.
- **Don't commit `output/` or `distilled/`** (raw transcripts / derived data) —
  `.gitignore` enforces this.

## Keep the learning walkthrough in sync

`docs/LEARNING_WALKTHROUGH.md` is a teaching document that explains the codebase
to Python/AI newcomers and references **specific files and line numbers**. When
you make a change that the walkthrough describes, **update the walkthrough in the
same change** so it never drifts out of date.

A change is "relevant" (update the walkthrough) when it touches any of:

- **RAG pipeline** — `scripts/embeddings.py`, `scripts/rag_index.py`,
  `scripts/rag_ask.py`, `scripts/rag_eval.py`, or `prompts/06_rag_answer.md` /
  `prompts/07_rag_eval.md`. (Walkthrough Parts 3–4.) Examples: changing the
  chunk size/overlap defaults, the cosine/normalization logic, the
  embedder-mismatch guard, retrieval or the grounding/refusal rules, or the
  eval metrics.
- **Adapters / config** — `scripts/claude_client.py`, `scripts/scope.py`
  (intents, embeddings config), `scripts/accounting.py`, `scripts/pricing.py`
  (model list or prices). (Parts 1–2.)
- **The hands-on lab** — the `make index` / `make ask` / `make rag-eval`
  targets, the `--provider`/`--retrieve-only`/`--sample` flags, or any command
  the walkthrough's "Part 5 — Hands-on lab" tells the reader to run.
- **Python patterns taught** — if you add/remove a pattern the walkthrough
  points at (dataclass usage, the ABC, `__main__` guards, lazy imports).

When updating it:

- **Fix the `file:line` references** the walkthrough cites if your change moved
  those lines. Stale line numbers are the most common way this doc rots — grep
  the walkthrough for the file you changed and re-check each citation.
- Keep the tone beginner-friendly and the "Interview soundbite" boxes accurate.
- If you add a genuinely new concept (e.g. a reranker, a real vector DB, hybrid
  search), add a short section rather than cramming it into an existing one.

If a change is purely internal and teaches nothing the walkthrough covers (a
typo fix, a refactor with no behavior change), you don't need to touch it —
use judgment, but err toward keeping the citations correct.
