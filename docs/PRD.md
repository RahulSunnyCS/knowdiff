# PRD: knowdiff (formerly youtube-skill-fetch)

**Status:** Draft
**Owner:** TBD
**Last updated:** 2026-05-20

## 1. Summary

`youtube-skill-fetch` is an **open-source, local-first** pipeline that turns
a YouTube creator's playlist into a reusable Claude Skill (`SKILL.md`). It
captures *how* a creator thinks about their domain — their recurring
patterns, heuristics, and moves — so that Claude can apply that style on
demand.

Mechanical work (downloading, captioning, OCR) runs locally and free via
`yt-dlp`, `ffmpeg`, `tesseract`, and optionally Whisper. Only the
distillation and synthesis steps use Claude.

**Distribution model.** This project is distributed **only as source code
on GitHub** under Apache-2.0. There is no hosted service, paid tier, or
managed offering, and none is planned within this PRD. Users clone the
repo and run it locally against playlists they are entitled to use.

## 2. Problem

Creators encode meaningful expertise in long-form video, but that knowledge is
trapped in audio + slides. Practitioners who want to *work in the creator's
style* — write like them, review like them, decide like them — have no
mechanical way to extract that style into a tool they can reuse.

Manually watching hours of video and taking notes is expensive and
inconsistent. Feeding raw transcripts to an LLM at query time is wasteful and
loses the cross-video patterns that make the creator's approach distinctive.

## 3. Goals

- **G1.** From a playlist URL, produce a `SKILL.md` that captures the
  creator's recurring patterns, usable as a Claude Skill.
- **G2.** Keep the bulk-data path (download, transcript, OCR) local, free,
  and deterministic. Reserve Claude for distillation/synthesis only.
- **G3.** Make each phase resumable and inspectable — every intermediate
  artifact is a flat file the human can eyeball.
- **G4.** Support both talking-head and screen-heavy content (slides, code,
  diagrams) via OCR.
- **G5.** Support multiple output modes: `Teacher`, `Reviewer`, `Advisor`.

## 4. Non-goals

- **Any hosted service, website, paid tier, SaaS, or commercial offering.**
  This project is OSS code on GitHub only; users self-host and self-operate.
- Real-time / streaming ingestion.
- A web UI or multi-user system.
- Fine-tuning a model. The output is a skill prompt, not weights.
- Scraping content the user is not entitled to use.
- Bit-for-bit reproduction of a creator's voice; the goal is method, not mimicry.
- Revenue, monetization, or any pricing model.

## 5. Users & use cases

- **Practitioner** wants to draft work in a respected creator's method
  (Teacher mode).
- **Learner** wants their own drafts critiqued the way the creator would
  critique them (Reviewer mode).
- **Operator** wants a "what would X do here?" oracle while making a
  decision (Advisor mode).

## 6. User journey

1. **Phase 0 scoping** — user answers a short pre-flight: source language,
   intent, depth. The pipeline branches from here.
2. User picks a playlist URL and a content mode (`talking-head` or
   `screen-heavy`).
3. `make test1` extracts one video as a sanity check; user eyeballs the
   transcript.
4. `make extract` runs the full playlist; transcripts (and OCR text for
   screen-heavy) land under `output/<playlist>/video_NN_*/`.
5. For each video, user runs the Phase 2 prompt with Claude; saves JSON to
   `distilled/<playlist>/video_NN.json`. *(Skipped for `stats` intent.)*
6. User runs the Phase 3 synthesis prompt over the concatenated JSONs;
   reviews `synthesis.json` at the **human review gate**.
7. User picks a mode and runs the Phase 4 prompt to produce
   `distilled/<playlist>/SKILL.md`. *(Replaced by report for non-skill intents.)*
8. User installs `SKILL.md` as a Claude Skill and uses it.

## 7. Functional requirements

### Phase 0 — Scoping (local, interactive)

A short pre-flight that the rest of the pipeline reads from
`distilled/<playlist>/scope.json`. Skipping it falls back to today's defaults
(`intent=method-distillation`, `language=auto`, `depth=full`).

- **F0.1** Capture **intent**, one of:
  - `method-distillation` — current behavior; produce a `SKILL.md`.
  - `stats` — word/phrase frequency, topic counts, speaker time. Local-only,
    no Claude calls.
  - `summary` — one-shot Claude summary per video + a playlist summary.
    No synthesis, no `SKILL.md`.
  - `quote-mining` — extract verbatim quotes matching user-supplied themes.
  - `style-clone` — like `method-distillation` but Phase 2 biases toward
    verbatim phrasing and rhetorical moves.
  - `topical-report` — answer a specific question about the creator's
    body of work (e.g., "what does X think about commodities?", "how is
    X learning AI?"). Extracts only relevant claims, deduplicates them,
    clusters them, and emits a structured report as Markdown and PDF.
    Does **not** produce a `SKILL.md`.
- **F0.2** Capture **language**: source language code (ISO 639-1) or
  `auto`; optional target language for translated output.
- **F0.3** Capture **depth**: `quick` (single video sample), `standard`
  (full playlist), `deep` (full playlist + re-pass with stricter rubric).
- **F0.4** For `stats`, `quote-mining`, and `topical-report`, capture the
  **target terms / themes / question** in `scope.json`.
- **F0.5** Emit a cost + time estimate based on playlist length × intent
  before any paid step runs; require explicit confirmation to proceed.
- **F0.6** Persist answers to `scope.json`; later phases read this file
  and adjust prompts, skip steps, or switch outputs accordingly.
- **F0.7** Capture an explicit **rights confirmation** ("I own this
  content, it is CC-licensed, or I have the creator's permission") and
  record it to `consent.json` with a timestamp. Refuse to proceed
  without it.
- **F0.8** Capture **target audience** for the output (`personal` /
  `shared`). `shared` triggers stricter Phase 3 redaction and a
  mandatory attribution header in any published artifact.

### Phase 1 — Extract (local)
- **F1.1** Accept a YouTube playlist URL (or single video URL).
- **F1.2** Honor `--mode {talking-head, screen-heavy}` and `--max-videos N`.
- **F1.3** Prefer existing captions; fall back to Whisper only when absent.
  Captions, metadata and the description come from **one** yt-dlp call per
  video (json3 track), cached as raw files beside the transcript. Whisper
  model selection honors `scope.json` language; for non-English sources
  without captions, warn the operator before falling back.
- **F1.4** For `screen-heavy`, sample frames and run OCR (`tesseract`),
  emitting an OCR sidecar.
- **F1.5** Produce one directory per video with `transcript.txt` and
  metadata; never overwrite existing artifacts.
- **F1.6** Be resumable: re-running skips videos already extracted
  (transcript + timestamped sidecar present), never re-fetching from
  YouTube. `--force` overrides.
- **F1.7** Rate-limit posture: paced requests by default; stop the run on
  the first block signature (exit code 3) rather than continuing; pass
  cookies / extractor args / raw flags through to yt-dlp.

### Phase 2 — Distill per video (Claude, map)
- **F2.1** Prompt `prompts/02_distill_video.md` takes one transcript and
  emits structured JSON of claims, heuristics, examples, and moves.
- **F2.2** One JSON file per video at `distilled/<playlist>/video_NN.json`.
- **F2.3** Every extracted item carries a `source` field with `video_id`
  and `timestamp` (MM:SS) so it can be cited later. The timestamp is read
  off inline `[MM:SS]` markers that the preprocessor renders into
  `transcript.clean.txt` from the timestamped sidecar (roughly every 30 s);
  the model is instructed never to invent one. Citations are not embedded
  in `SKILL.md` — they live in a separate sidecar (see §10).
- **F2.4** For `topical-report` intent, Phase 2 extracts only statements
  relevant to the user's question (passed via `scope.json:question`),
  not the full method.
- **F2.5** Phase 2 runs via the **SDK orchestrator** (see §7a) by
  default, with prompt caching and bounded parallelism. The
  copy-paste prompt path remains supported as a fallback.

### Phase 3 — Synthesize (Claude, reduce)
- **F3.1** Prompt `prompts/03_synthesize.md` takes the concatenated per-video
  JSONs and emits ranked recurring patterns to `synthesis.json`.
- **F3.2** Output ranks patterns by frequency and centrality, with
  references back to source videos.
- **F3.3** Phase 3 output is a **human review gate** before Phase 4.

### Phase 4 — Author SKILL.md (Claude)
- **F4.1** Prompt `prompts/04_author_skill.md` takes `synthesis.json` + a
  mode and emits a `SKILL.md` usable as a Claude Skill.
- **F4.2** Mode is one of `Teacher`, `Reviewer`, `Advisor`.
- **F4.3** `SKILL.md` includes a clear trigger description, the method
  distilled to actionable steps, and worked examples drawn from the source.
- **F4.4** `SKILL.md` is **citation-free**. Sources live in a separate
  `citations.json` / `citations.md` sidecar that maps every claim and
  example in `SKILL.md` back to `[video_NN @ MM:SS]`.
- **F4.5** For `topical-report` intent, Phase 4 instead emits
  `report.md` (and a rendered `report.pdf` via pandoc when available)
  answering the user's question, with deduped/clustered findings and a
  matching `citations.md`.

### Intent branching (Phase 0 → 2/3/4)

`scope.json:intent` selects the path:

| Intent              | Phase 1 | Phase 2          | Phase 3            | Phase 4         | Output                   |
| ------------------- | ------- | ---------------- | ------------------ | --------------- | ------------------------ |
| `method-distillation` | run   | run              | run                | run             | `SKILL.md` + `citations.*` |
| `style-clone`       | run     | run (verbatim-biased) | run           | run (`Teacher`) | `SKILL.md` + `citations.*` |
| `summary`           | run     | summary prompt   | playlist-summary   | skip            | `summary.md` + `citations.*` |
| `stats`             | run     | skip (local analyzer) | skip          | skip            | `stats.json` / `stats.md` |
| `quote-mining`      | run     | quote-extract prompt | merge + dedupe | skip            | `quotes.md` + `citations.*` |
| `topical-report`    | run     | targeted extract | dedupe + cluster   | report writer   | `report.md` + `report.pdf` + `citations.*` |

## 7a. SDK orchestrator (cross-phase infrastructure)

Phases 2–4 default to **programmatic execution** via the Anthropic Python
SDK rather than copy-paste prompts. The SDK is hidden behind a thin
adapter (`scripts/claude_client.py`) so it can be swapped for direct
HTTP, a different model provider, or a mock for tests.

- **F7a.1** A single env var (`ANTHROPIC_API_KEY`) is the only credential.
- **F7a.2** Model is configurable via `--model` flag and `scope.json`;
  defaults to a current Sonnet for cost.
- **F7a.3** Every Phase 2 call uses **prompt caching** on the
  system/instructions block so re-runs across videos hit cache.
- **F7a.4** Phase 2 runs **bounded-parallel** (default 4 in flight,
  configurable); failures retry with exponential backoff.
- **F7a.5** Phase 2 is **resumable**: completed `video_NN.json` files
  are skipped; partial runs can be resumed without re-paying for done work.
- **F7a.6** Phase 3 and Phase 4 are single SDK calls; Phase 3 uses
  prompt caching on the concatenated per-video JSON corpus so iterating
  on Phase 4 mode/prompt is cheap.
- **F7a.7** Copy-paste prompt files remain in `prompts/` and remain
  supported — users without an API key can run the manual path.

## 8. Estimated cost (to the user running it)

This is what a **self-hosting user** pays out of their own pocket. The
project itself has no revenue, no servers, and no per-user cost.

Phase 1 is free (local CPU/disk). Claude API cost is dominated by Phase 2
and scales linearly with playlist size. Estimates assume ~15 min videos
with ~2k word transcripts (~3k input tokens each, ~1k output JSON).

| Playlist size | Total in | Total out | Sonnet 5.5 (~$2/M in, $10/M out) | Opus 5.5 (~$4/M in, $20/M out) |
| ------------- | -------- | --------- | -------------------------------- | ------------------------------- |
| 10 videos     | ~45k     | ~14k      | ~$0.23                           | ~$0.46                          |
| 50 videos     | ~210k    | ~65k      | ~$1.07                           | ~$2.14                          |
| 100 videos    | ~415k    | ~130k     | ~$2.13                           | ~$4.26                          |

Cost is order-of-magnitude; real numbers depend on transcript length, model
choice, and whether Phase 3 uses **prompt caching** on the per-video JSON
corpus (recommended — meaningfully reduces Phase 3/4 input cost when iterating).

Non-default intents adjust this:
- `stats` → **$0** (no Claude calls).
- `summary` → ~50–70% of the method-distillation cost (no Phase 4, smaller Phase 3).
- `quote-mining` → ~60% (smaller Phase 2 outputs, no Phase 4).

## 9. Non-functional requirements

- **NFR1.** Local-first: Phase 1 requires no API key and no paid services.
- **NFR2.** Cost: Claude usage is bounded by playlist size; per-video
  distillation is independent and parallelizable.
- **NFR3.** Transparency: every phase writes a flat-file artifact a human
  can read.
- **NFR4.** Portability: runs on macOS and Linux with the dependencies
  listed in `README.md` and `requirements.txt`.
- **NFR5.** Idempotence: re-running a phase on existing artifacts is safe.

## 10. Artifacts (data contract)

```
distilled/<playlist>/scope.json                    # Phase 0
distilled/<playlist>/consent.json                  # Phase 0 rights confirmation
output/<playlist>/video_NN_<slug>/source.info.json             # raw yt-dlp metadata (cache)
output/<playlist>/video_NN_<slug>/source.<lang>.json3          # raw caption track (cache)
output/<playlist>/video_NN_<slug>/transcript.txt
output/<playlist>/video_NN_<slug>/transcript.timestamped.json  # segment-level start/end (captions or Whisper)
output/<playlist>/video_NN_<slug>/transcript.clean.txt         # preprocessor output, with inline [MM:SS] markers
output/<playlist>/video_NN_<slug>/preprocess.json              # what the preprocessor removed and why
output/<playlist>/video_NN_<slug>/description.txt              # YouTube description (chapter timestamps live here)
output/<playlist>/video_NN_<slug>/chapters/*.txt               # per-chapter splits when description has chapters
output/<playlist>/video_NN_<slug>/ocr.txt          # screen-heavy only
output/<playlist>/video_NN_<slug>/metadata.json
output/<playlist>/video_NN_<slug>/screenshots/*.jpg            # deictic-trigger screenshots (optional)
output/<playlist>/video_NN_<slug>/screenshots.json             # manifest: frame -> ts + trigger + context
distilled/<playlist>/video_NN.json                 # Phase 2 (method/style intents)
distilled/<playlist>/synthesis.json                # Phase 3
distilled/<playlist>/SKILL.md                      # Phase 4 (method/style intents) — citation-free
distilled/<playlist>/summary.md                    # intent=summary
distilled/<playlist>/stats.json|stats.md           # intent=stats
distilled/<playlist>/quotes.md                     # intent=quote-mining
distilled/<playlist>/report.md                     # intent=topical-report
distilled/<playlist>/report.pdf                    # intent=topical-report (pandoc, optional)
distilled/<playlist>/citations.json                # universal sidecar: claim → [video_NN @ MM:SS, ...]
distilled/<playlist>/citations.md                  # human-readable citations
distilled/<playlist>/consent.json                  # Phase 0 rights confirmation
distilled/<playlist>/score.json                    # eval output (when produced)
distilled/<playlist>/CHANGELOG.md                  # diff/versioning output
```

## 11. Dependencies

- `yt-dlp`, `ffmpeg`, `tesseract` (system).
- Python deps in `requirements.txt`.
- `faster-whisper` (preferred) or `openai-whisper` (fallback) only when
  captions are missing. The extractor picks faster-whisper first; both
  produce the same `transcript.timestamped.json` shape.
- Claude (for Phases 2–4); model choice left to the operator.

## 11a. Quality features (cross-cutting)

The following improve output quality across all intents. Compliance notes
are kept inline because several of these change the legal posture.

- **Speaker diarization** (Phase 1). When a video has multiple speakers
  (interviews, panels), diarize via `whisperx` or `pyannote` and label
  speakers as `Host`, `Guest 1`, `Guest 2`. **Compliance:** do not map
  voices to real names automatically — voiceprints are biometric data in
  several jurisdictions. Generic labels only, unless the user supplies
  names manually in `scope.json`.
- **Chapter awareness** (Phase 1). YouTube descriptions often include
  chapter timestamps. Phase 1 parses them and splits transcripts on
  chapter boundaries, dramatically improving Phase 2 quality on long
  videos. **Compliance:** chapters are creator-published metadata, no
  added risk.
- **Diff mode** (post-Phase 4). Re-running on the same playlist months
  later compares the new `synthesis.json` against the previous one and
  emits a `CHANGELOG.md` of what changed in the creator's thinking.
  **Compliance:** purely local comparison of locally-held artifacts.
- **Skill versioning** (post-Phase 4). `SKILL.md` carries a `version`
  header; every regeneration bumps it; the `CHANGELOG.md` records the
  delta. Lets the user keep an evolving skill aligned with the creator's
  evolving views.

## 11b. Eval & marketplace

A skill or report nobody trusts is worthless. The eval layer makes
trust measurable; the marketplace makes vetted outputs discoverable.

### Eval (per-output)

- **F11b.1** Hold-one-out evaluation: regenerate the skill from N-1
  videos **in a scratch root the held-out video's JSON is excluded from**;
  ask Claude (with the generated skill loaded as context) to predict the
  creator's moves/heuristics on the held-out video; score against the
  actual content using a structured rubric (overlap, novelty, factual
  accuracy). `score.json` records `leak_free`.
- **F11b.2** Emit `score.json` alongside every `SKILL.md` /
  `report.md`. Includes overall score, per-rubric breakdown, and the
  held-out video ID.
- **F11b.3** A minimum score threshold gates inclusion in the
  marketplace catalog.

### Marketplace (repo-level)

- **F11b.4** A `skills/` directory in the repo holds **community-
  contributed, vetted** skills generated from **CC-licensed or
  explicitly-permitted** content only. Every entry includes the source
  playlist URL, license, `SKILL.md`, `citations.md`, and `score.json`.
- **F11b.5** New entries arrive via PR. CI runs a sanity check: license
  declared, score above threshold, citations present, no third-party
  copyrighted transcripts checked in.
- **F11b.6** Skills targeting a specific creator without that creator's
  permission are **rejected**, even if the score is high.
- **F11b.7** A creator-takedown path is documented: any creator can
  request removal of their entry by opening an issue; maintainers
  remove within 7 days, no questions asked.

## 12. Compliance & legal

This project is distributed as **source code only** on GitHub under
Apache-2.0. The maintainers do not ingest, host, or redistribute any
third-party content. Every user runs the pipeline locally on their own
machine, and each user is solely responsible for their own compliance.

- **Scope.** Personal research, learning, and creator-authorized use only.
  Not for redistribution or commercial repackaging of source material.
- **No hosted offering.** There is no website, SaaS, or paid tier. If a
  third party builds one, they do so independently and assume their own
  legal responsibility.
- **YouTube ToS.** Downloading via `yt-dlp` falls outside YouTube's
  sanctioned playback flow. Operators must verify they are entitled to
  download the target playlist (own content, Creative Commons, explicit
  creator permission, or applicable fair-use research jurisdiction).
- **Raw artifacts stay local.** `output/` (transcripts, OCR, frames,
  deictic-trigger screenshots) must not be committed to public repos or
  redistributed. `.gitignore` enforces this by default; this rule is
  also documented in `README.md`. Screenshots in particular carry the
  same posture as transcripts — they are bitmap reproductions of
  copyrighted video frames.
- **Transformative output.** `SKILL.md` should capture **method**, not
  reproduce content. Verbatim quotes are limited to short illustrative
  excerpts with attribution. `style-clone` intent carries higher risk —
  flag explicitly in the output that it imitates style for personal use.
- **Attribution.** Every published `SKILL.md` includes a header crediting
  the creator and linking the source playlist.
- **Creator opt-out.** A documented path: if a creator objects, the
  operator deletes `output/`, `distilled/`, and any derived `SKILL.md`.
- **Copyrighted readings.** Phase 3 should flag transcripts containing
  third-party copyrighted material (audiobook readings, song lyrics,
  long quoted passages) so they are excluded from `SKILL.md` examples.
- **PII.** Where transcripts contain third-party PII (names, contact
  details mentioned on air), Phase 3 redacts before emitting `SKILL.md`.
- **Prefer official APIs where possible.** Use the YouTube Data API for
  metadata (titles, IDs, descriptions); reserve `yt-dlp` for captions
  and audio you have rights to.
- **Model provider terms.** Operators must comply with Anthropic's usage
  policy when sending transcripts to Claude.

## 13. Risks & mitigations

- **YouTube ToS / rate limits.** Mitigation: one paced yt-dlp call per
  video, a permanent raw cache so nothing is fetched twice, stop-on-block
  (F1.7), and a README section on identity / residential IP options;
  document that users are responsible for their entitlements.
- **Caption quality varies.** Mitigation: Whisper fallback; human review gate
  before synthesis.
- **Hallucinated patterns in synthesis.** Mitigation: Phase 3 requires
  source-video citations; Phase 3 is a human review gate.
- **Creator style drift across playlist.** Mitigation: synthesis ranks by
  recurrence; one-off ideas are dropped or flagged.
- **OCR noise on screen-heavy content.** Mitigation: OCR is a sidecar, not a
  transcript replacement; Phase 2 prompt instructs Claude to treat OCR as
  supporting evidence.

## 14. Success metrics

- **M1.** Time from playlist URL → working `SKILL.md` on a 10-video
  playlist: under 1 hour of wall-clock and under 15 minutes of operator
  attention.
- **M2.** ≥ 80% of patterns in `synthesis.json` survive human review without
  edits.
- **M3.** A blind reader can identify the source creator from `SKILL.md`'s
  patterns and examples.
- **M4.** Phase 1 runs end-to-end on a fresh machine following only the
  `README.md` setup steps.

## 15. Open questions

- Should Phases 2–4 ship as scripts that call the Claude API directly, or
  remain copy-paste prompts? (Today: copy-paste.)
- How should we version `SKILL.md` outputs as the upstream playlist grows?
- Do we want a `Critic` mode distinct from `Reviewer` (rebuttal vs. critique)?
- Is there value in cross-creator synthesis (merging two creators' methods)?

## 16. Milestones

- **M0 — Today.** Phase 1 script + Phase 2–4 prompts, manual orchestration.
- **M1.** Convenience target to run Phase 2 over all transcripts via API.
- **M2.** Convenience target for Phase 3 + Phase 4 with mode flag.
- **M3.** Evaluation harness: given a held-out video, score whether the
  produced `SKILL.md` recovers its patterns.
