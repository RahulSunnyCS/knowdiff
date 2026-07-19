# youtube-skill-fetch

**Turn a YouTube creator's playlist into a reusable asset — a Claude Skill,
a research report, a summary, or a set of cited quotes.**

You point it at a playlist. It watches the videos for you (locally, on your
own machine — free), then asks Claude to find the patterns and hands you back
something you can actually re-use.

---

## Part 1 — What this project does (the business view)

### The problem it solves

Creators pack real expertise into long-form video, but that knowledge is
locked inside hours of audio and slides. If you want to *work in a creator's
style* — write like them, decide like them, or just find what they said about
one topic — your only option today is to re-watch everything and take notes by
hand. That's slow, inconsistent, and doesn't scale past a handful of videos.

Feeding raw transcripts to an AI at question-time doesn't fix it either: it's
expensive, and it loses the cross-video patterns that make a creator's approach
distinctive.

### What it produces

You choose the shape of the output **before** the run starts. Six options:

| Choose this           | When you want…                                                          | You get                     |
| --------------------- | ----------------------------------------------------------------------- | --------------------------- |
| `method-distillation` | A Skill that thinks like the creator                                    | `SKILL.md`                  |
| `topical-report`      | A PDF answering "what does X think about Y?"                            | `report.md` + `report.pdf`  |
| `summary`             | A short summary of every video + the whole playlist                     | `summary.md`                |
| `stats`               | How often a creator says a word / mentions a topic (no Claude cost)     | `stats.json` / `stats.md`   |
| `quote-mining`        | A list of verbatim quotes matching themes you specify (no Claude cost)  | `quotes.md`                 |
| `style-clone`         | A Skill that mimics the creator's *phrasing*, not just their method      | `SKILL.md`                  |

Every output (except `stats`) also produces a `citations.md` sidecar mapping
each claim back to **the exact video and timestamp** it came from. The Skill or
report stays clean and readable; open the citations file when you want to
verify a point.

### Who it's for

- **Practitioners** who want to draft work in a respected creator's method.
- **Learners** who want their own drafts critiqued the way that creator would.
- **Operators** who want a "what would X do here?" oracle while deciding.
- **Researchers** tired of re-watching the same recurring ideas across 30 videos.
- **Creators** who want a Skill that helps them keep working in their own style.

You probably **don't** need this if you only watch a video or two casually, or
if what you actually want is to download videos to keep or republish — this is
**not** a content downloader.

### What it costs

The mechanical work — downloading, transcribing, OCR, stats, quote-mining —
runs **locally and free** on your own machine. Only the *thinking* steps call
Claude, and the tool always **shows you an estimate and asks you to confirm**
before it spends anything.

Rough Claude cost for a **10-hour playlist** (~40 × 15-min videos):

| Model      | Approx. cost per playlist |
| ---------- | ------------------------- |
| Sonnet 4.6 | ~$1.50–2.00               |
| Opus 4.7   | ~$6–8                     |

`stats` and `quote-mining` are **$0** (no Claude). Summaries and reports are
cheaper than a full distillation because they do less work. Full cost model:
[`docs/PRD.md`](docs/PRD.md) §8.

### How it's distributed

Open-source **source code on GitHub** under Apache-2.0. There is no hosted
service, paid tier, or SaaS — you clone the repo and run it locally against
playlists you're entitled to use.

### ⚠️ Before you use it — your responsibility

This is a tool, not a service. **You** are responsible for what you point it at:

- **Only use it on content you have the right to use** — your own videos,
  Creative Commons / openly-licensed content, or content the creator has
  explicitly permitted.
- **Don't share the raw output.** Transcripts and downloaded audio stay on your
  computer; `.gitignore` keeps them out of Git. Don't email or upload them.
- **Credit the creator.** If you produce a Skill or report, name the creator and
  link the playlist.
- **Not for commercial repackaging.** Don't sell what this produces. It's for
  personal / research use.
- **No warranty.** Free software under Apache-2.0; no liability for misuse.

Full compliance notes: [`docs/PRD.md`](docs/PRD.md) §12.

---

## Part 2 — How to implement it (step by step)

The pipeline runs in phases. The **local** phases (extract, preprocess,
screenshots, stats, quotes) are free and need no API key. The **Claude** phases
(distill → synthesize → author) are where the value is produced.

```
Phase 0   scope        pick the intent + models          (local, interactive)
Phase 1   extract      download captions / transcribe     (local, free)
   ↓      preprocess   strip filler, sponsors, repeats    (local, free)
Phase 2   distill      per-video notes                    (Claude)
Phase 3   synthesize   patterns across all videos ← review gate (Claude)
Phase 4   author       write SKILL.md / report            (Claude)
```

Every step writes a plain file you can open and read. Nothing is hidden; if
something looks wrong, stop and fix it before paying for the next step.

### Step 0 — Install the dependencies

**macOS:**

```bash
brew install yt-dlp ffmpeg tesseract
pip install -r requirements.txt
```

**Linux:**

```bash
sudo apt install yt-dlp ffmpeg tesseract-ocr
pip install -r requirements.txt
```

**If a video has no captions,** the tool transcribes it with Whisper. Install
it separately — `faster-whisper` is preferred (same weights, ~4× faster, half
the memory, and voice-activity detection on by default, which avoids Whisper's
"hallucinate on silence" failure on long videos):

```bash
pip install faster-whisper      # recommended
# or, as a fallback:
pip install openai-whisper
```

On Apple Silicon, `mlx-whisper` is auto-preferred when installed.

### Step 1 — Choose how you'll drive the Claude phases

There are two paths for Phases 2–4. The local phases are identical on both.

| Path                            | Setup                                                        | When to use                                                     |
| ------------------------------- | ----------------------------------------------------------- | -------------------------------------------------------------- |
| **A. API key** (automated)      | `pip install anthropic && export ANTHROPIC_API_KEY=sk-...`  | Run the pipeline end-to-end, one command per phase.            |
| **B. Claude Code / Claude Pro** | Paste prompts from `prompts/` into Claude Code or claude.ai | You already pay for Pro/Max and want zero per-token spend.     |

Steps 2–4 below use **Path A**. Path B is covered at the end of this section.

### Step 2 — Sanity-check one video

```bash
make test1 PLAYLIST="https://youtube.com/playlist?list=<id>" PLAYLIST_NAME=mycreator
```

Downloads one video's captions to `output/mycreator/video_01_*/transcript.txt`.
Open it and skim — confirm it's real text from the talk before running the lot.

### Step 3 — Extract the full playlist

```bash
make extract PLAYLIST="https://youtube.com/playlist?list=<id>" PLAYLIST_NAME=mycreator
```

You'll end up with ~40 `transcript.txt` files under `output/mycreator/`.
`make extract` is interactive (prompts for URL, range, jobs); use
`make extract-batch` for a non-interactive run.

Only want a slice, or want it faster?

```bash
make extract-batch PLAYLIST="..." VIDEOS="1-10"      # first ten (1-based)
make extract-batch PLAYLIST="..." VIDEOS="1,3,5-7"   # cherry-pick
make extract-batch PLAYLIST="..." JOBS=4             # 4 videos in parallel
```

`JOBS>1` is safe on the captions-first path (IO-bound). Keep `JOBS=1` for
`--force-whisper` or `MODE=screen-heavy` unless you have spare CPU.

#### Hitting a download / rate limit? (`429`, "confirm you're not a bot")

YouTube throttles anonymous, high-rate access. If extraction starts failing
with `HTTP Error 429`, a bot-check prompt, or captions coming back empty on
videos that clearly have them, work through these in order:

1. **Update yt-dlp** — `pip install -U yt-dlp` (or `brew upgrade yt-dlp`).
   An outdated yt-dlp is the most common cause of sudden "limit" errors.
2. **Go serial** — drop `JOBS` back to `1`; parallelism is the fastest way to
   get flagged.
3. **Authenticate with cookies** — the single most effective fix for bot
   checks. Pass a browser you're logged into YouTube on:
   ```bash
   make extract-batch PLAYLIST="..." PLAYLIST_NAME=mycreator COOKIES_FROM=chrome
   ```
   (or `COOKIES=cookies.txt` for a Netscape-format export.)
4. **Throttle** — space requests out and cap bandwidth:
   ```bash
   make extract-batch PLAYLIST="..." SLEEP=2 LIMIT_RATE=2M COOKIES_FROM=chrome
   ```
5. **Rotate off a blocked IP** — route yt-dlp *and* the caption fetcher through
   a proxy: `PROXY=http://user:pass@host:port`.

All of these vars work on `make extract`, `make extract-batch`, and `make
test1`. Retries are on by default (`RETRIES=10`) to ride out transient blocks.
Full flag reference: `python scripts/extract_playlist.py --help` (the
**network / rate-limit** group).

> If it's Whisper transcription that's slow rather than a network limit, that's
> a different bottleneck: use a smaller model (`--whisper-model tiny`), install
> `faster-whisper`, and stay on the captions path so audio never downloads.

### Step 4 — Clean the transcripts

```bash
make preprocess PLAYLIST_NAME=mycreator
```

Strips filler ("um", "you know"), sponsor reads, intros/outros, and repeats —
locally. It typically removes 30–50% of the text **before** anything reaches
Claude, cutting the next step's cost by roughly the same amount. Each video gets
a `transcript.clean.txt` and a `preprocess.json` showing what was cut. If the
cuts look too aggressive, re-run with `--no-sponsor-detect` or `--intro-sec 10`.

### Step 5 — Set the intent (`scope.json`)

Tell the pipeline what to produce. Drop this at
`distilled/mycreator/scope.json` (or run `make scope PLAYLIST_NAME=mycreator`
for the interactive scoper):

```json
{
  "intent": "method-distillation",
  "language": "auto",
  "depth": "standard",
  "themes": [],
  "question": "",
  "target_audience": "personal",
  "models": {
    "phase2": "claude-haiku-4-5-20251001",
    "phase3": "claude-sonnet-4-6",
    "phase4": "claude-sonnet-4-6"
  }
}
```

Change `intent` to any of the six options from Part 1. For `topical-report`,
also fill in `question`; for `quote-mining` / `stats`, fill in `themes`.

### Step 6 — Distill each video (Phase 2)

The first step that uses Claude. Defaults to Haiku to keep cost low:

```bash
make phase2 PLAYLIST_NAME=mycreator
```

You'll see live progress and a running total:

```
Phase 2: model=claude-haiku-4-5-20251001, intent=method-distillation, 40 videos, concurrency=4
  ✓ video_01: ok (820 out tokens)  [running total: $0.0034]
  ✓ video_02: ok (760 out tokens)  [running total: $0.0067]
  ...
Phase 2 done. Estimated cost: $0.1240
```

Spot-check `distilled/mycreator/video_01.json` (short keys;
`python scripts/expand_schema.py distilled/mycreator/video_01.json`
pretty-prints it). Phase 2 is resumable — completed videos are skipped on re-run.

### Step 7 — Synthesize across videos (Phase 3 — the review gate)

```bash
make phase3 PLAYLIST_NAME=mycreator
```

Writes `distilled/mycreator/synthesis.json`. **Eyeball it.** This is the human
review gate — confirm the recurring patterns look right before paying for Phase 4.

### Step 8 — Author the output (Phase 4)

```bash
make phase4 PLAYLIST_NAME=mycreator SKILL_MODE=Teacher
```

Writes a versioned, citation-free `SKILL.md`, updates `CHANGELOG.md`, and
regenerates `citations.md`. Re-running bumps the version and backs up the old
`SKILL.md`. Load the result into Claude (Claude Code, claude.ai Projects, or the
API) and it answers in the creator's method.

**Skill modes** (for `method-distillation`):

- **Teacher** — applies the creator's method to make new things in their style.
- **Reviewer** — critiques *your* drafts the way that creator would.
- **Advisor** — answers "what would they recommend here?"

### Step 9 (optional) — Evaluate the Skill

```bash
make eval PLAYLIST_NAME=mycreator
```

Hold-one-out scoring: the last video is withheld and Claude is asked to predict
its content from the `SKILL.md` alone. Result lands in
`distilled/mycreator/score.json`.

---

### Other intents (same first four steps)

The extract + preprocess steps (2–4) are identical. Only the intent and the
final command differ:

**Topical report** — set `intent: "topical-report"` with a `question`, then:

```bash
make topical PLAYLIST_NAME=mycreator
```

Extracts only question-relevant statements per video, writes `report.md` +
`citations.md`, and renders `report.pdf` when `pandoc` is installed.

**Summary:**

```bash
make summary PLAYLIST_NAME=mycreator
```

**Quote-mining ($0, no Claude)** — every place a theme is mentioned, with timestamps:

```bash
make quote-mine PLAYLIST_NAME=mycreator THEMES="passive income,index funds,compound interest"
```

For fuzzy/paraphrase matching, add `distilled/mycreator/themes.aliases.json`:

```json
{
  "passive income": ["money working for you", "recurring revenue"],
  "compound interest": ["compounding", "interest on interest"]
}
```

**Stats ($0, no Claude):**

```bash
make stats PLAYLIST_NAME=mycreator THEMES="ai,markets,inflation"
```

**Screenshots at "look at this" moments ($0)** — grabs a frame 1.5s after each
deictic trigger (`look at this`, `as you can see`, `the chart shows`, …),
clustered so you don't get five frames of one slide:

```bash
make screenshots PLAYLIST_NAME=mycreator
# preview candidate timestamps without downloading video:
python scripts/capture_screenshots.py --playlist mycreator --skip-download
```

---

### Path B — running the Claude phases without an API key

If you pay for **Claude Pro/Max** or use **Claude Code**, run Phases 2–4 through
the chat/agent interface — no `ANTHROPIC_API_KEY`, no per-token spend. Trade-off:
one transcript at a time instead of batch-parallel.

Steps 2–4 (extract + preprocess) are identical. Then, instead of `make phaseN`:

- **Phase 2:** paste `prompts/02_distill_video.md` + each `transcript.clean.txt`
  into Claude; save each JSON response to `distilled/mycreator/video_NN.json`.
  In Claude Code you can automate it:

  > Read `prompts/02_distill_video.md`, then for each
  > `output/mycreator/video_*/transcript.clean.txt` run that prompt and write the
  > JSON to `distilled/mycreator/video_NN.json`. Skip any that already exist.

- **Phase 3:** paste `prompts/03_synthesize.md` + all `video_*.json` into one
  conversation; save the response as `distilled/mycreator/synthesis.json` and
  eyeball it.
- **Phase 4:** paste `prompts/04_author_skill.md` + `synthesis.json`; save the
  result as `distilled/mycreator/SKILL.md`, then regenerate citations locally:

  ```bash
  make citations PLAYLIST_NAME=mycreator
  ```

Topical reports use `prompts/02_topical_extract.md` + `prompts/04_topical_report.md`;
summaries use `prompts/02_summary.md` + `prompts/03_summary_rollup.md`. You end up
with the same files as Path A.

> **Note:** `make eval` still needs an API key (it grades programmatically).
> Skip it on Path B or grade by hand with `prompts/05_eval_rubric.md`.

---

## Where things end up

```
output/mycreator/                     # raw transcripts (do not share)
  video_01_*/transcript.txt
  video_01_*/transcript.timestamped.json   # segment-level start/end per line
  video_01_*/transcript.clean.txt          # preprocessor output
  video_01_*/preprocess.json               # what was removed and why
  video_01_*/screenshots/*.jpg             # frames at "look at this" (optional)
  video_01_*/screenshots.json              # frame → ts + trigger + context

distilled/mycreator/                  # everything Claude touched
  scope.json                          # your intent + model choices
  video_01.json ... video_40.json     # Phase 2 output
  synthesis.json                      # Phase 3 output (review gate)
  SKILL.md                            # Phase 4 output (citation-free)
  citations.md                        # which video + timestamp backs each claim
  cost.json                           # exactly what you spent
```

---

## Project documents

- [`docs/PRD.md`](docs/PRD.md) — product requirements: every phase, output, cost
  model, and compliance note. Start here for the full picture.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — how to contribute.
- [`SECURITY.md`](SECURITY.md) — how to report a security issue privately.
- [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) — community standards.
- [`LICENSE`](LICENSE) — Apache-2.0.

---

## License

Apache-2.0 — free, open source, no warranty. See [`LICENSE`](LICENSE).

The code and prompts in this repo are Apache-2.0 licensed. Anything you
**generate** by running the tool (a Skill, a report, etc.) is yours — but your
right to use it is limited by your rights in the source content.
