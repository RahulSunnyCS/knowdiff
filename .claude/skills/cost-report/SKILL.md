---
name: cost-report
description: Print a per-phase token usage and cost breakdown for a youtube-skill-fetch playlist by reading `distilled/<playlist>/cost.json`. Use when the user asks "how much did that cost", "show token usage", "break down cost per phase", or "what did Phase 3 spend". Free, local — no API calls.
---

# cost-report

Pretty-prints token usage and estimated cost per phase from the
`cost.json` that the orchestrators (`run_phase2.py`, `run_phase3.py`,
`run_phase4.py`, `run_topical.py`, `run_summary.py`) write.

## When to use

Any time the user wants visibility into spend or token volume after one
or more phases have run. Typical asks:

- "How much did that playlist cost?"
- "Show me the token usage for each phase."
- "What did Phase 3 spend on Sonnet?"
- "Compare cost across `mycreator` and `othercreator`."

## Inputs

- `PLAYLIST_NAME` (required, one or more) — directory name(s) under
  `distilled/`. Pass multiple to compare.

## How to run

```
python3 .claude/skills/cost-report/report.py <PLAYLIST_NAME> [PLAYLIST_NAME ...]
```

Optional flags:

- `--json` — emit the structured report as JSON instead of a table.

## Output

For each playlist, prints a table:

```
Playlist: mycreator
  Phase     Model                          Calls    Input     Output    Cache R    Cache W    Cost
  phase2    claude-haiku-4-5-20251001      40       820,000   33,000    0          0          $0.985
  phase3    claude-sonnet-4-6              1        45,000    7,200     12,000     45,000     $0.358
  phase4    claude-sonnet-4-6              1        9,800     3,400     0          9,800      $0.118
  --------------------------------------------------------------------------------------------------
  TOTAL                                    42       874,800   43,600    12,000     54,800     $1.461
```

If `cost.json` is missing, surface that and suggest the user run a
Claude phase (e.g., `make phase2`).

## Notes

- Costs come from `scripts/pricing.py`; treat them as estimates, not
  invoices. The Anthropic console is the source of truth for billing.
- `stats` and `quote-mine` intents are local-only and never write
  `cost.json` — that's expected, not a bug.
