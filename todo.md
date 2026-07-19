# todo — next workflow

Most original items shipped in the `claude/complete-todo-batch` branch.
See bottom for what remains.

---

## Roadmap — knowledge-reuse features (2 → 3 → 4)

A compounding progression on top of the existing extract/distill assets:
**retrieval → personalization → agentic planning**. Full evaluation and
rationale live in the approved plan. Build order and status below.

### ▶ Phase 1 (in progress): RAG Q&A over a playlist + eval

Ask questions about an indexed playlist; answers are grounded in the
transcripts with `[video_NN @ MM:SS]` citations and fall back to general
knowledge only when clearly labelled.

- [ ] `scripts/embeddings.py` — `Embedder` gateway. Providers: `hash`
      (zero-dep baseline/testing), `local` (fastembed / sentence-transformers,
      **default**), `voyage` (API, primary), `openai` (API, alternate).
      Config-only switch via `scope.json:embeddings`.
- [ ] `scripts/rag_index.py` — chunk over `transcript.timestamped.json`
      segments (precise timestamps), embed, persist `rag_index.npz` +
      `rag_index.meta.json` + `chunks.jsonl` under `distilled/<pl>/`.
- [ ] `scripts/rag_ask.py` — cosine top-k retrieval, `--retrieve-only`
      offline inspection, grounded answer via `prompts/06_rag_answer.md`,
      cost recorded to `cost.json`.
- [ ] `scripts/rag_eval.py` + `prompts/07_rag_eval.md` — LLM-judged
      faithfulness / answer-relevance / context-relevance → `rag_score.json`.
- [ ] `qa` intent + `embeddings` config in `scope.json`; `make index` /
      `make ask` / `make rag-eval`; optional deps; README section + intents row.

### Phase 2 (queued): second-brain recommender

Cron over watch history → model the user's knowledge state → for a new video,
recommend watch / skip / watch-only-[MM:SS–MM:SS] based on novelty vs. what
they already know. Builds directly on the Phase 1 retrieval index. Watch
history ingested via Google Takeout export.

### Phase 3 (queued): syllabus-maker capstone

Topic → research current syllabi (web) → build a market-relevant syllabus →
match YouTube transcripts/timestamps to objectives → assemble a free
"bootcamp-level" curriculum with gap analysis. Agentic planning + RAG.

### Add-on B (optional, bridges 1→3): concept graph / graph-RAG

Extract concepts + prerequisite edges across a playlist; powers better
retrieval and becomes the prerequisite backbone for the syllabus maker.

### Side-quest (decoupled): grocery MCP-authoring exercise

Author an MCP server (recipe → scaled ingredient list → cart) against a
**mocked** grocery backend. Purpose is learning MCP authoring only — no
official Blinkit/Zepto/Instamart API exists, so do not build against a real one.

---

## Shipped

### ✓ 1. Phase 0 interactive scoping CLI
`scripts/scope_init.py`. Walks intent, language, depth, themes,
question, audience, rights confirmation. Emits `scope.json` and
`consent.json`. `--non-interactive --assume-rights` for tests/CI.

### ✓ 2. Phase 3 orchestrator
`scripts/run_phase3.py`. Reads per-video JSONs, calls Sonnet with the
synthesis prompt, writes `synthesis.json`, records cost. Prompt
caching on the corpus. Phase 3 prompt now requires source citations
(≥2 supporting videos per recurring pattern).

### ✓ 3. Phase 4 orchestrator + skill versioning
`scripts/run_phase4.py`. Reads `synthesis.json`, takes
`--mode {Teacher,Reviewer,Advisor}`, writes a versioned `SKILL.md`
(citation-free), updates `CHANGELOG.md`, regenerates `citations.md`
sidecar. Previous SKILL.md is backed up as `SKILL.v<old>.md` on
overwrite. Defensive strip of `[video_NN @ MM:SS]` markers in case
the model adds them despite instructions.

### ✓ 4. Validate + auto-retry on Phase 2 schema regressions
`run_phase2.py` detects verbose-key responses and re-prompts once with
a stricter system message. Falls back to accepting either form rather
than failing. Logs which path each video took.

### ✓ 5. `topical-report` intent
`prompts/02_topical_extract.md`, `prompts/04_topical_report.md`,
`scripts/run_topical.py`. Per-video targeted extraction, then a Sonnet
report writer. PDF rendering via pandoc when available; falls back to
Markdown otherwise. Citations emitted as a sidecar grouped by facet.

### ✓ 6. `stats` intent (local, $0)
`scripts/run_stats.py`. Word + bigram + trigram frequencies, vocab
size, per-video duration (from timestamped sidecar), user-term
counts. Emits `stats.json` + `stats.md`.

### ✓ 7. `summary` intent
`prompts/02_summary.md`, `prompts/03_summary_rollup.md`,
`scripts/run_summary.py`. Lightweight per-video summary + playlist
rollup. No SKILL.md, no synthesis JSON.

### ✓ 8. Citation extractor + universal sidecar
`scripts/citations.py`. Walks per-video JSONs, groups claims /
heuristics / patterns by label, emits `citations.json` + `citations.md`
with `[video_NN @ MM:SS]` references. Phase 4 calls this automatically.

### ✓ faster-whisper as default backend
`extract_playlist.py` adapter prefers `faster-whisper` with VAD on,
falls back to `openai-whisper`. mlx-whisper preferred on Apple
Silicon when installed.

### Whisper quality follow-ups (partial)

- ✓ **mlx-whisper on Apple Silicon** — wired in adapter, preferred when
  available on M-series Macs.
- ✓ **`--whisper-language` flag** — passes through to the backend
  (`language="en"` etc) to prevent misidentification.
- ✗ **WhisperX** — deferred. Heavy (needs HF token + alignment +
  diarization models). Belongs in a separate item-9 / item-11 sweep
  when speaker labels become a real need.
- Note: default Whisper model stays at `base`. Users who need higher
  quality can pass `--whisper-model medium` or set per-playlist in
  scope.json (already supported via the existing flag).

### ✓ Deictic screenshots
`scripts/capture_screenshots.py` + `transcript.timestamped.json` in
extractor. Keyword scan + cluster + ffmpeg frame extract.

### ✓ 10. Chapter awareness in extractor
`extract_playlist.py` now writes `description.txt` per video via
`yt-dlp --get-description`. The preprocessor already used these for
chapter splitting; the loop is now closed.

### ✓ 11. Diff mode + skill versioning
`scripts/diff_synthesis.py` compares two `synthesis.json` snapshots
and prepends a dated entry to `CHANGELOG.md`. `run_phase4.py` bumps
the SKILL.md `version` field on re-runs and backs up the old version.

### ✓ 12. Eval harness (`score.json`)
`prompts/05_eval_rubric.md` + `scripts/run_eval.py`. Hold-one-out
scoring; emits `score.json` with method recall/precision, vocabulary
match, tone match, overall. Honest in the docstring about meta-eval
biases.

### ✓ 13. CI workflow
`.github/workflows/ci.yml`. Python 3.10/3.11/3.12 matrix: compile,
import-sanity each script, validate prompts (UTF-8, non-empty, leading
`#` heading), markdown link check via lychee (non-blocking). No
pipeline execution in CI by design.

### ✓ 14. Issue templates
`.github/ISSUE_TEMPLATE/{bug,feature,prompt-improvement}.yml`. Each
includes a compliance checkbox that gates submission on "I have not
included third-party transcripts."

### ✓ 15. GitHub Private Vulnerability Reporting
`SECURITY.md` updated to point at GitHub's built-in private reporting
flow instead of a personal email.

### ✓ 16. Examples scaffold
`examples/README.md` documents the contribution bar (CC-licensed
source, `score.json` ≥ 0.60, explicit attribution). Directory is
intentionally empty in initial release — needs a real CC playlist run
to populate.

---

## Open (deferred from this batch)

### Whisper item 9 — speaker diarization
Best done via WhisperX as a single bundle (forced-alignment + diarization).
Needs Hugging Face token + extra models. Reserved for a focused sweep
when interview/panel content becomes a real use case.

### Smarter deictic screenshots
The `--smart` opt-in (Haiku re-ranking of trigger hits) and per-frame
OCR remain as easy adds if the keyword version's false-positive rate
proves annoying in practice.

### Example outputs in `examples/`
The directory exists but is empty. Populating requires running the
full pipeline on a real CC-licensed playlist, scoring it via
`run_eval.py`, and committing the result. Cannot be done from a
sandboxed environment.

### Phase 0 Claude assistance (open design Q — decided)
**Decision:** not now. Keep the CLI dependency-free and offline-capable
for the first version. Claude-assisted question sharpening is a clean
follow-up but it complicates the consent flow (now Phase 0 itself
costs money before the user has confirmed rights).

### Marketplace location (open design Q — decided)
**Decision:** in this repo, under `examples/`. The contribution bar
(score threshold, license declaration) keeps quality up. Splitting
into a separate repo can happen later if the volume justifies it.

### Cost-delta across runs (open design Q — decided)
**Decision:** the `accounting.write()` helper already merges across
phases. Cross-run deltas can be derived by diffing `cost.json` files
— a future `scripts/diff_cost.py` is trivial when there's demand.

---

## Won't do (out of scope, per PRD §4)

- Any hosted service / website / SaaS / paid tier.
- Real-time / streaming ingestion.
- Fine-tuning a model.
- Web UI or multi-user system.
- Anything targeting a specific creator without that creator's permission.
