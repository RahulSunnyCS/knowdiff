# todo — next workflow

Most original items shipped in the `claude/complete-todo-batch` branch.
See bottom for what remains.

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

### ✓ 17. Extraction fixes (2026-10 review)
- Idempotent extract: a video with `transcript.txt` + sidecar is never
  refetched (`--force` to override). PRD F1.6 was documented but not
  implemented; re-runs after a block used to re-hit every video.
- Two yt-dlp calls per video — metadata (info.json + description), then
  exactly one json3 caption track chosen from it via `--load-info-json` —
  replace the description call + 2–4 `youtube-transcript-api` calls. (A
  single call asking for `en.*` was tried first; live, YouTube answers the
  second caption track with HTTP 429 and yt-dlp then drops the metadata.)
  `youtube-transcript-api` dropped. Raw `source.*` files kept as cache.
- Paced by default (`--sleep-requests`, `--sleep-subtitles`,
  `--pause-sec`); stops with exit code 3 on the first block signature;
  `--cookies-from-browser` / `--extractor-args` / `--yt-dlp-args`
  pass-through. README "Rate limits" section.
- `--playlist-name` flag; the Makefile now passes `PLAYLIST_NAME` to
  `extract`/`test1` (previously everything landed in `output/playlist/`
  while later phases read `output/<PLAYLIST_NAME>/`).
- `metadata.json` written per video (`capture_screenshots.py` was reading
  a `url` nobody wrote).
- The cached `source.info.json` keeps auto-caption entries only for the
  caption language (≈10 MB → ≈100 KB per video).
- Intro/outro trims are each capped at 5% of the video's length, so a
  2-minute clip no longer loses 40% of its transcript.

### ✓ 18. Real timestamps in citations
Phase 2 never saw timestamps (flat text only), so every `ts` was a guess.
The preprocessor now renders `[MM:SS]` markers every 30 s from the
sidecar, trims intro/outro by time and splits chapters by segment time;
the distill + topical prompts read the nearest marker. Quote-mining
reports `ts` per hit.

### ✓ 19. Leak-free eval
`run_eval.py` rebuilt Phase 3 + 4 in a scratch root with the held-out
JSON removed, as the docstring always claimed. `score.json` carries
`leak_free`.

### ✓ 20. Models, prices, client
Defaults → `claude-haiku-4-5` / `claude-sonnet-5-5`; pricing table
corrected (Opus 4.7 was 3× its real price) and extended; the client
delegates retries to the SDK (no more retrying 400s) and surfaces
truncation / refusals.

### ✓ 21. Unit tests
`tests/` (stdlib `unittest`, fake yt-dlp on PATH) + CI step. `make test`.

### ✓ 22. MCP server
`scripts/mcp_server.py` (stdio, or streamable HTTP on loopback behind a
tunnel with a token in the path). Local work stays on the laptop; the
connected Claude does Phases 2–4 itself from the same prompts, so no API
key is needed. `save_distilled` rejects any `ts` that is not a marker in
the transcript. README "MCP server". Verified live over stdio and HTTP
against a real extracted playlist; not yet verified through a real tunnel
or from claude.ai.

---

## Open (deferred from this batch)

### Structured outputs for the JSON phases
Phase 2 still parses free-text JSON and re-prompts on verbose keys. The
Messages API's `output_config.format` would guarantee the compact schema
in one call and delete the retry path. Needs a live key to verify.

### Batch API for Phase 2
Per-video distillation is not latency-sensitive and is embarrassingly
parallel: the Message Batches API halves its cost. Resumability must then
key on batch ids, not just on-disk JSON.

### Hierarchical Phase 3
One synthesis call caps at ~150 videos of compact JSON. Chunk + reduce
for bigger channels.

### `knowledge-diff` intent (the name on the repo)
Segment-level claims compared against a personal knowledge store (every
distilled JSON so far + a syllabus/progress file) → "watch these 12
minutes of 58" with deep links. Haiku as the "is this already covered"
judge. Not started.

### `strategy-claims` intent
Per-video hypothesis cards (instrument, entry, exit, sizing, stated
regime, quote, ts) for trading content, clustered across creators, then
translated into the sibling option-backtesting DSL via its MCP
`propose_strategy`/`validate_strategy` with the untranslatable parts
listed, never guessed. Not started.

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
