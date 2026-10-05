---
name: distill-playlist
description: Run Phase 2 (per-video distillation), Phase 3 (cross-video synthesis), and Phase 4 (SKILL.md authoring) end-to-end for a youtube-skill-fetch playlist. Use when the user asks to "distill", "run phases 2-4", "build the skill", or wants to go from preprocessed transcripts to a finished SKILL.md in one shot. Requires that extraction + preprocessing have already produced `output/<playlist>/video_*/transcript.clean.txt` and a `distilled/<playlist>/scope.json`.
---

# distill-playlist

Single-command driver for Phases 2-4 of the youtube-skill-fetch pipeline.
Wraps `run_phase2.py`, `run_phase3.py`, and `run_phase4.py`, stopping on
the first failure so the user can review before paying for later phases.

## When to use

The user has already run extraction + preprocessing and now wants to
turn the cleaned transcripts into a finished `SKILL.md`. Typical asks:

- "Distill the `mycreator` playlist."
- "Run phases 2-4 for `mycreator` in Reviewer mode."
- "Build the skill from the preprocessed transcripts."

## Inputs

- `PLAYLIST_NAME` (required) — directory name under `output/` and `distilled/`.
- `SKILL_MODE` (optional, default `Teacher`) — one of `Teacher`, `Reviewer`, `Advisor`.
- `OUT` (optional, default `output`) — extraction output root.

## How to run

Use the helper script bundled with this skill. It chains the three
phases and aborts on non-zero exit, so the user can inspect
`synthesis.json` after Phase 3 if a phase fails partway.

```
bash .claude/skills/distill-playlist/run.sh <PLAYLIST_NAME> [SKILL_MODE] [OUT]
```

Examples:

```
bash .claude/skills/distill-playlist/run.sh mycreator
bash .claude/skills/distill-playlist/run.sh mycreator Reviewer
bash .claude/skills/distill-playlist/run.sh mycreator Advisor output
```

## Preflight

Before invoking the script, confirm:

1. `distilled/<PLAYLIST_NAME>/scope.json` exists. If not, suggest
   `make scope PLAYLIST_NAME=<name>`.
2. At least one `output/<PLAYLIST_NAME>/video_*/transcript.clean.txt`
   exists. If not, suggest `make preprocess PLAYLIST_NAME=<name>`.
3. `ANTHROPIC_API_KEY` is set in the environment.

If any precondition is missing, surface the gap to the user instead of
running the script blind.

## After the run

- Show the user the final cost (last line of `distilled/<name>/cost.json`'s
  `total_estimated_cost_usd`).
- Point at the artifacts: `synthesis.json`, `SKILL.md`, `citations.md`,
  `CHANGELOG.md`.
- If the user wants a per-phase breakdown, invoke the `cost-report` skill.
