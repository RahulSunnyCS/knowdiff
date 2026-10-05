# knowdiff

_(formerly `youtube-skill-fetch`)_

**Turn a YouTube creator's playlist into something you can re-use.**

You give it a playlist. It watches the videos for you (locally, on your own
computer), then asks Claude to figure out the patterns — and gives you back
one of:

- a **Skill** Claude can load to think the way that creator does, or
- a **report (PDF)** answering a specific question like *"what does this
  person think about commodities?"* or *"how is this person learning AI?"*, or
- a **summary**, **word/topic stats**, or **a list of quotes** on a theme.

You decide which one you want. The downloads, transcripts, and OCR run on
your machine for free. Only the *thinking* steps use Claude.

---

## Is this for me?

You might find this useful if:

- You follow a YouTube creator and wish you could **ask Claude questions
  in their style** without manually re-watching everything.
- You want a **PDF report** of one creator's views on a specific topic,
  with citations back to the exact video and timestamp.
- You're a researcher or learner and you want to **stop re-watching the
  same recurring ideas** spread across 30 videos.
- You make videos yourself and want a Skill that helps you **work in
  your own established style**.

You probably **don't** need this if:

- You only watch one or two videos casually. Just watch them.
- You want to download videos to keep or republish. This tool is **not**
  a content downloader — the raw transcripts stay on your machine and are
  not meant to be shared.

---

## ⚠️ Read this before you use it

This is a tool, not a service. **You** are responsible for what you point
it at. Some plain-English rules:

- **Only use it on content you have the right to use.** That means: your
  own videos, Creative Commons / openly-licensed content (lots of
  conference talks fall here), or content the creator has explicitly
  permitted you to use.
- **Don't share what comes out.** Transcripts and downloaded audio stay
  on your computer. The repo's `.gitignore` already keeps them out of
  Git, but don't email them around or upload them either.
- **Credit the creator.** If you do produce a Skill or report, name the
  creator and link the playlist.
- **This is not for commercial repackaging.** Don't sell what this
  produces. The project is open source, free, and meant for personal /
  research use only.
- **No warranty.** It's free software under the Apache-2.0 license.
  We accept no liability if you misuse it.

If you're a creator and you want your content out of someone's local
copy of this tool, contact that person directly — the project itself
doesn't host any content, so there's nothing for us to take down.

Full compliance notes are in [`docs/PRD.md`](docs/PRD.md) §12.

---

## How it works (for non-technical readers)

Imagine doing this by hand:

1. **Download captions** for every video in the playlist (or transcribe
   them if captions aren't there). This is the boring mechanical part —
   it runs locally and is free.
2. **For each video, take notes** on the key ideas. We ask Claude to do
   this, producing one JSON file per video.
3. **Look across all the notes** and pull out the patterns that come up
   again and again. Claude does this once, across everything.
4. **Write up the final output** — either a Skill, a topical report (PDF),
   a summary, or stats. You pick the shape upfront.

Every step writes a file you can open in a text editor and read. Nothing
is hidden. If something looks wrong, you can stop and fix it.

---

## Outputs you can ask for

You tell the tool what you want before it starts. Options:

| Choose this           | When you want…                                                         | You get                          |
| --------------------- | ---------------------------------------------------------------------- | -------------------------------- |
| `method-distillation` | A Skill that thinks like the creator                                    | `SKILL.md`                       |
| `topical-report`      | A PDF answering "what does X think about Y?"                            | `report.md` + `report.pdf`       |
| `summary`             | A short summary of every video + the whole playlist                     | `summary.md`                     |
| `stats`               | How often a creator says a word, mentions a topic, etc. (no Claude cost) | `stats.json` / `stats.md`        |
| `quote-mining`        | A list of verbatim quotes matching themes you specify                   | `quotes.md`                      |
| `style-clone`         | A Skill that mimics the creator's *phrasing*, not just method           | `SKILL.md`                       |
| `knowledge-diff`      | Only the minutes of each video that are new to *you*                    | `watchlist.md`                   |
| `claim-cards`         | The checkable claims in each video, as quoted, deep-linked cards        | `claim_cards.md`                 |

Every output (except `stats`) also produces a separate `citations.md`
file mapping each claim back to **the exact video and timestamp** it
came from (timestamps are read off inline markers in the transcript, not
guessed). The Skill or report stays clean and readable; if you want
to verify a specific point, open the citations file.

---

## What does it cost?

The downloading part is **free** — it just uses your computer.

Claude charges by tokens (think: words it reads and writes). Rough
estimates for a **10-hour playlist** (about 40 videos × 15 minutes):

| Model         | Approximate cost per playlist |
| ------------- | ----------------------------- |
| Sonnet 5.5    | ~$1.00–1.50                   |
| Opus 5.5      | ~$2.50–3.50                   |

(Defaults use Haiku 4.5 for the per-video step, which is cheaper still.)

`stats` mode is free (no Claude calls). Other modes are cheaper than
the table above because they do less work.

The tool always **shows you the estimate and asks you to confirm**
before it starts spending. No surprises.

Full cost model: [`docs/PRD.md`](docs/PRD.md) §8.

---

## How to set it up

**On Mac:**

```
brew install yt-dlp ffmpeg tesseract
pip install -r requirements.txt
```

**On Linux:**

```
sudo apt install ffmpeg tesseract-ocr
pip install -r requirements.txt     # installs yt-dlp too; distro packages are often too old
```

Keep `yt-dlp` current (`pip install -U yt-dlp` or `brew upgrade yt-dlp`).
YouTube changes its anti-bot checks often and an old yt-dlp is the most
common cause of "Sign in to confirm you're not a bot".

If a video has no captions, the tool will transcribe it with Whisper.
Whisper is optional and installed separately. We prefer **faster-whisper**
(same model weights, ~4× faster, half the memory):

```
pip install faster-whisper      # recommended
# or, as a fallback:
pip install openai-whisper
```

faster-whisper also enables voice-activity detection by default, which
dramatically reduces the "hallucination on silence" failure mode of
Whisper on long videos with pauses.

On Apple Silicon Macs, `mlx-whisper` is another fast option (not yet
wired in — see `todo.md`).

If you want the programmatic workflow (recommended — automates Phases
2–4 instead of copy-pasting prompts), also install:

```
pip install anthropic
export ANTHROPIC_API_KEY=sk-...
```

---

## How to run it

There are two end-to-end examples below. Pick the one that matches what
you want. Both assume you've done the setup above.

### Example A — Build a Skill that thinks like a creator

**Scenario:** You follow a creator who runs a CC-licensed conference talk
playlist. You want a Claude Skill that gives advice in their style.

**Step 1: sanity-check one video.**

```
make test1 PLAYLIST="https://youtube.com/playlist?list=<id>" PLAYLIST_NAME=mycreator
```

This downloads one video's captions to
`output/mycreator/video_01_*/transcript.txt`. Open it and skim — make
sure it looks like real text from the talk.

**Step 2: extract the full playlist.**

```
make extract PLAYLIST="https://youtube.com/playlist?list=<id>" PLAYLIST_NAME=mycreator
```

When this finishes you'll have 40-ish `transcript.txt` files under
`output/mycreator/`. Each video costs exactly **one** yt-dlp call, and a
video that is already on disk is never fetched again, so if YouTube
blocks you halfway (exit code 3) you just wait and re-run the same
command. See [Rate limits](#rate-limits) below before pointing it at a
big playlist.

**Step 3: clean the transcripts.** This strips filler ("um", "you
know"), sponsor reads, intros/outros, and repeats. It runs locally and
typically removes ~30–50% of the text *before* anything goes to Claude,
which saves you ~30–50% on the next step's cost.

```
make preprocess PLAYLIST_NAME=mycreator
```

Each video now has a `transcript.clean.txt` and a `preprocess.json`
showing what was cut. If the cuts look too aggressive, re-run with
`--no-sponsor-detect` or `--intro-sec 10`.

The cleaned transcript carries an inline `[MM:SS]` marker roughly every
30 seconds (from the timestamped sidecar). That is what lets Claude put a
real timestamp on every claim it extracts, so `citations.md` points at the
actual moment in the video rather than a guess. `--marker-interval 0`
turns them off.

**Step 4: configure intent (write `scope.json`).** Until the interactive
scoper ships, drop this file at `distilled/mycreator/scope.json`:

```json
{
  "intent": "method-distillation",
  "language": "auto",
  "depth": "standard",
  "themes": [],
  "question": "",
  "target_audience": "personal",
  "models": {
    "phase2": "claude-haiku-4-5",
    "phase3": "claude-sonnet-5-5",
    "phase4": "claude-sonnet-5-5"
  }
}
```

**Step 5: distill each video (Phase 2).** This is the first step that
uses Claude. The defaults use Haiku to keep cost low.

```
python scripts/run_phase2.py --playlist mycreator
```

You'll see live progress and a running total:

```
Phase 2: model=claude-haiku-4-5, intent=method-distillation, 40 videos, concurrency=4
  ✓ video_01: ok (820 out tokens)  [running total: $0.0034]
  ✓ video_02: ok (760 out tokens)  [running total: $0.0067]
  ...
Phase 2 done. Estimated cost: $0.1240
Cost breakdown: distilled/mycreator/cost.json
```

Open `distilled/mycreator/video_01.json` to spot-check the extraction
(it uses short keys; `python scripts/expand_schema.py
distilled/mycreator/video_01.json` pretty-prints to verbose form).

**Step 6: synthesize across videos.**

```
make phase3 PLAYLIST_NAME=mycreator
```

Writes `distilled/mycreator/synthesis.json`. **Eyeball it** — this is
the human review gate. Confirm the recurring patterns look right
before paying for Phase 4.

**Step 7: author the Skill.**

```
make phase4 PLAYLIST_NAME=mycreator SKILL_MODE=Teacher
```

Writes a versioned, citation-free `SKILL.md`, updates `CHANGELOG.md`,
and regenerates `citations.md` (the sidecar that maps every claim back
to a video and timestamp). Re-running bumps the version and backs up
the previous SKILL.md.

You now have a Skill. Load it into Claude (via Claude Code, claude.ai
Projects, or the API) and it will answer in the creator's method.

**Optional Step 8: evaluate the Skill.**

```
make eval PLAYLIST_NAME=mycreator
```

Hold-one-out scoring, leak-free: the last video is withheld, a fresh
synthesis and SKILL.md are rebuilt from the other N-1 videos in a scratch
folder, and Claude is asked to predict the held-out video's content using
only that Skill. Result lands in `distilled/mycreator/score.json` with
`leak_free: true`. (Pass `--skill-path` to score a Skill you already have,
but that Skill saw the held-out video, so expect an optimistic number.)

---

### Example B — Answer a question with a topical PDF report

**Scenario:** You want to know "what does this creator think about
compound interest?" — without watching all 40 videos.

**Steps 1–3** are the same as Example A (extract + preprocess).

**Step 4: set intent to `topical-report` with your question.**
`distilled/mycreator/scope.json`:

```json
{
  "intent": "topical-report",
  "language": "auto",
  "depth": "standard",
  "themes": [],
  "question": "What does the creator say about compound interest and long-term investing?",
  "target_audience": "personal",
  "models": {
    "phase2": "claude-haiku-4-5",
    "phase3": "claude-sonnet-5-5",
    "phase4": "claude-sonnet-5-5"
  }
}
```

**Step 5: run the topical pipeline.**

```
make topical PLAYLIST_NAME=mycreator
```

This calls the targeted extraction prompt on each cleaned transcript
(only statements relevant to the question), then writes
`distilled/mycreator/report.md` plus a `citations.md` sidecar. If
`pandoc` is installed, a `report.pdf` is rendered too.

---

### Example C — Just find every quote about a topic ($0)

**Scenario:** You don't need a Skill or a report — you just want every
place the creator says "passive income" or "index funds," with
timestamps.

**Steps 1–3** same as Example A.

**Step 4: run quote-mining locally.** No Claude needed; costs nothing.

```
make quote-mine PLAYLIST_NAME=mycreator THEMES="passive income,index funds,compound interest"
```

Output: `distilled/mycreator/quotes.md` (human-readable) and
`citations.json` (machine-readable). Each quote includes the video it
came from and whether the match was exact, stemmed, or via an alias.

If you want fuzzy/paraphrase matching (e.g., "money working for you"
should also match `passive income`), add a `distilled/mycreator/themes.aliases.json`:

```json
{
  "passive income": ["money working for you", "recurring revenue"],
  "compound interest": ["compounding", "interest on interest"]
}
```

---

### Example D — Grab screenshots when the creator says "look at this"

**Scenario:** The creator points at slides, charts, or code on screen.
You want a folder of frames at exactly those moments, so you can scan
them visually instead of re-watching everything.

**Step 1: extract.** Same as Example A; produces transcripts **and**
the timestamped sidecar (`transcript.timestamped.json`) automatically.

**Step 2: capture screenshots.**

```
make screenshots PLAYLIST_NAME=mycreator
```

This:
1. Reads the timestamped transcript and scans for trigger phrases:
   `look at this`, `see here`, `as you can see`, `notice`, `the chart
   shows`, etc.
2. Clusters nearby triggers (within 10s) so you don't get 5 frames of
   the same slide.
3. Downloads the video at 720p if it's not already local (yt-dlp).
4. Uses ffmpeg to grab the frame 1.5s *after* the trigger — creators
   usually say "look at this" right before the visual appears.
5. Writes frames to `output/mycreator/video_NN_*/screenshots/` with
   filenames like `001_t0234_look_at_this.jpg` so false positives are
   obvious and easy to `rm`.

Cost: **$0** (pure local). Per-video cap is 15 screenshots by default
(`--max-shots`); customize triggers with `--triggers-file mine.txt`.

**Preview without downloading the video:**

```
python scripts/capture_screenshots.py --playlist mycreator --skip-download
```

Writes a `screenshots.json` per video listing the candidate timestamps
and the speech context around each, so you can decide whether the
detection looks right before committing to downloads.

---

### Copy-paste mode (no API key)

You can run Phases 2–4 entirely by hand if you don't want to set up
`ANTHROPIC_API_KEY`. Open the prompts in `prompts/`, paste each
transcript into Claude.ai, save the response as the right filename, and
repeat. Slower but works on a Pro/Max chat subscription. The PRD
documents both paths.

---

## Where things end up

After a full run you'll find:

```
output/mycreator/                     # raw transcripts (do not share)
  video_01_*/source.info.json         # raw yt-dlp metadata (cache; never refetched)
  video_01_*/source.en.json3          # raw caption track (cache; never refetched)
  video_01_*/metadata.json            # id, title, url, duration, caption languages
  video_01_*/description.txt          # YouTube description (chapter timestamps)
  video_01_*/transcript.txt
  video_01_*/transcript.timestamped.json  # segment-level start/end for each line
  video_01_*/transcript.clean.txt     # preprocessor output, with [MM:SS] markers
  video_01_*/preprocess.json          # what was removed and why
  video_01_*/screenshots/*.jpg        # frames at "look at this" moments (optional)
  video_01_*/screenshots.json         # manifest: frame -> ts + trigger + context

distilled/mycreator/                  # everything Claude touched
  scope.json                          # your intent + model choices
  video_01.json ... video_40.json     # Phase 2 output
  synthesis.json                      # Phase 3 output (review gate)
  SKILL.md                            # Phase 4 output (citation-free)
  citations.md                        # which video + timestamp backs each claim
  cost.json                           # exactly what you spent
```

---

## Skill modes (when you pick `method-distillation`)

- **Teacher** — the Skill applies the creator's method to make new things
  in their style.
- **Reviewer** — the Skill critiques *your* drafts the way that creator
  would.
- **Advisor** — the Skill answers "what would they recommend here?"

---

## MCP server (no API key)

`scripts/mcp_server.py` exposes knowdiff to any Claude session as an MCP
server. The server does the local, free work — yt-dlp extraction,
transcript cleaning, reading and saving files, quote-mining. The Claude
that connects does the distillation, synthesis and SKILL.md authoring
itself, using the same prompts as Phases 2–4, so this route needs **no
`ANTHROPIC_API_KEY`**: it runs on whatever Claude plan the session
already has. Results land in the same `distilled/<playlist>/` files the
API pipeline writes, so the two routes are interchangeable.

```bash
pip install mcp
```

Tools: `list_playlists`, `list_videos`, `start_extract`, `extract_status`,
`preprocess`, `get_transcript`, `get_prompt`, `get_distilled`,
`save_distilled`, `save_synthesis`, `save_skill`, `quote_mine`,
`get_diff_candidates`, `save_knowledge_diff`, `list_card_profiles`,
`get_card_instructions`, `save_claim_cards`, `get_claim_cards`,
`save_card_translation`.
`save_distilled` refuses any `ts` that is not a `[MM:SS]` marker in that
video's transcript, so invented timestamps cannot be saved.

**On the same machine (Claude Code, Claude Desktop).** The repo's
`.mcp.json` registers the server over stdio; open the repo in Claude Code
and it is picked up. For another client, the command is
`python3 scripts/mcp_server.py`.

**From the cloud (claude.ai, cloud sessions) to your laptop.** The laptop
must be awake with both the server and a tunnel running:

```bash
export KNOWDIFF_MCP_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
python3 scripts/mcp_server.py --http --allowed-host knowdiff.example.com   # binds 127.0.0.1:8770
cloudflared tunnel run knowdiff      # a tunnel routing knowdiff.example.com -> http://127.0.0.1:8770
```

Then add `https://knowdiff.example.com/<token>/mcp` as a custom connector.
For a quick try without a domain, `cloudflared tunnel --url
http://127.0.0.1:8770` prints a temporary `*.trycloudflare.com` hostname;
start the server with that hostname as `--allowed-host`.

What protects it: the server only listens on loopback, rejects requests
whose `Host` is not one you allowed, and is mounted on a path containing
the token, so the URL is the credential — treat it like a password and
rotate it by changing `KNOWDIFF_MCP_TOKEN`. Tools can only read and write
under `output/` and `distilled/`, and can only launch yt-dlp against
`https://youtube.com` / `youtu.be` links. That is a shared-secret URL, not
per-user login; put Cloudflare Access or OAuth in front if more than one
person will have the link.

---

## Knowledge diff — watch only what is new

Once a video is distilled, knowdiff can tell you which parts of it you
have not met before and link straight to them:

```
## video_02 — Welcome to Girls Who Build Cameras, Summer 2016
**Watch 6.9 of 8.0 minutes** — 8 new, 1 partly new, 1 already known
### [01:02–07:58](https://www.youtube.com/watch?v=...&t=62s)
- `01:02` A camera integrates many engineering disciplines, not just computer science
...
```

What counts as already known:

- every video you have distilled before — all other playlists, plus the
  earlier videos of the same playlist;
- `knowledge/syllabus.md`, your own list. Plain lines, bullets and
  `- [x]` items are known; `- [ ]` items are "not yet" and ignored.

Two ways to judge "already covered?":

| Judge | How | Cost | Quality |
| ----- | --- | ---- | ------- |
| Word overlap | `make knowledge-diff PLAYLIST_NAME=...` | free, offline | misses paraphrases — it will keep things on the list that you do know |
| A connected Claude | MCP tools `get_diff_candidates` → `save_knowledge_diff`, following `prompts/06_knowledge_diff.md` | your Claude plan | judges meaning, and explains each "known" with the item that covers it |

Either way the output is `distilled/<playlist>/watchlist.md` plus one
JSON per video under `distilled/<playlist>/knowledge_diff/`. A passage
runs from an item's `[MM:SS]` marker to the next item's marker (30 s to
3 min), and nearby passages are merged, so ranges are approximate to
about one marker interval.

---

## Claim cards — the checkable claims in a video

A connected Claude can pull each idea someone could act on or check out
of a video as a *claim card*: the claim, a tag, a few subject fields, the
creator's exact words and a deep link to where they said them. It works
for any subject and runs through the MCP server only (no API-key script).

1. `get_card_instructions(profile)` + `get_transcript` →
   `save_claim_cards`. A card is rejected if its quote is not in the
   transcript word for word or its timestamp is not a real marker.
   Anything the creator did not state stays `null`.
2. `get_claim_cards` groups cards by subject and tag across playlists,
   so claims several creators share come first. The same view is written
   to `distilled/claim_cards.md` (`make claim-cards` rebuilds it).

**Profiles** decide what a card looks like for a subject. They are small
JSON files under `profiles/`:

| Profile | A card is… | Fields |
| ------- | ---------- | ------ |
| `generic` (default) | a rule, technique, recommendation, prediction, comparison or warning | `topic`, `when`, `do`, `expect`, `caveats` |
| `trading` | a strategy that could be checked against market data | `instrument`, `entry`, `exit`, `sizing`, `regime`, `edge` |

To add a subject (fitness, cooking, law, interview prep…), copy
`profiles/generic.json`, rename it, and edit `card_is`, `tags`, `fields`
and `cluster_by`. It shows up in `list_card_profiles` straight away.

A profile may also define a **translation** step: turning a card into
something a tool can test. `trading` does: connect the sibling
`ai-trading-agent` repo's `option-backtesting` MCP server, follow
`get_prompt("07_trading_translate")`, check the strategy YAML with that
server's `validate_strategy`, then `save_card_translation`. Every rule
the target cannot express, and every value it needed that the creator
never gave, must be listed under `manual_review`; a translation that
silently fills in an unstated entry, exit or sizing is rejected.

Cards are creators' claims, not results. `valid` on a translation is
what the external validator reported to the translating session;
knowdiff records it and cannot re-check it.

---

## Rate limits

YouTube throttles and blocks automated caption fetching, and it blocks
cloud IP ranges (AWS, GCP, Azure, CI runners, hosted notebooks) outright.
The extractor is built to live with that rather than fight it:

- **Nothing is fetched twice.** Metadata, description and the caption
  track come from one metadata call plus one caption request per video,
  and the raw files are kept next to the transcript. Re-running only
  touches videos that are missing.
- **Only one caption track is requested.** The best track (manual before
  auto-generated) is picked from the metadata first. Asking for every
  English variant at once gets the second track an HTTP 429.
- **It is paced by default.** yt-dlp sleeps 1.5 s between requests and
  2 s before each caption download, and the extractor pauses ~2 s between
  videos. Tune with `--sleep-requests`, `--sleep-subtitles`, `--pause-sec`.
- **It stops at the first block** ("Sign in to confirm you're not a bot",
  HTTP 429) with exit code 3 instead of hammering, which only extends the
  block. Progress is saved. Wait 30–60 minutes and re-run the same
  command.

If blocks keep happening, in this order:

1. Update yt-dlp.
2. Run from a home or office connection, not a cloud box. A laptop on a
   nightly schedule is the cheapest reliable setup; sync only the small
   `output/` text files to wherever you run Phases 2–4.
3. Carry a real identity: `--cookies-from-browser chrome` (a profile
   signed in to YouTube) and/or
   `--extractor-args "youtube:player_client=tv,web_safari"`. If still
   walled, add a proof-of-origin token provider plugin for yt-dlp; see
   its wiki.
4. A rotating residential proxy via `--yt-dlp-args "--proxy http://..."`
   is the paid last resort.
5. Whisper on downloaded audio is a fallback for videos **without**
   captions, not a way around the block: audio downloads hit the same
   wall.

Cookies tie the activity to your account. The rights rules above apply
unchanged.

## Project documents

- [`docs/PRD.md`](docs/PRD.md) — product requirements: every phase, every
  output, cost model, compliance notes. Start here if you want the full picture.
- `make test` — unit tests (no network, no API key; the extractor is tested
  against a fake yt-dlp).
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — how to contribute.
- [`SECURITY.md`](SECURITY.md) — how to report a security issue privately.
- [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) — community standards.
- [`LICENSE`](LICENSE) — Apache-2.0.

---

## License

Apache-2.0 — free, open source, no warranty. See [`LICENSE`](LICENSE).

The code and prompts in this repo are Apache-2.0 licensed. Anything you
**generate** by running the tool (a Skill, a report, etc.) is yours — but
your right to use it is limited by your rights in the source content.
