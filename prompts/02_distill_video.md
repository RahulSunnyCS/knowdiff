# Per-video distillation prompt (Phase 2, "map")

You will receive one video's transcript from a creator's playlist. Your job
is to extract HOW the creator thinks about their domain — not just what they
said in this video.

Return ONLY a single JSON object with this **compact** shape (no prose, no
markdown fence, no comments). Use the short keys exactly as shown:

```
{
  "t": "<video_title>",
  "cc": [
    {"c": "<core claim>", "ev": "<short quote or paraphrase>", "ts": "MM:SS"}
  ],
  "h": [
    {"r": "if X then Y / always Z / never Q", "why": "<rationale>", "ts": "MM:SS"}
  ],
  "rp": [
    {"p": "<reasoning pattern>", "ex": "<example>", "ts": "MM:SS"}
  ],
  "emph": ["..."],
  "dis": ["..."],
  "v": [
    {"term": "...", "m": "<meaning in their usage>"}
  ]
}
```

Key legend (also in `prompts/schema.md`):
- `t` = video title
- `cc` = core claims; each has `c` (claim), `ev` (evidence), `ts` (timestamp)
- `h` = heuristics; each has `r` (rule), `why` (rationale), `ts` (timestamp)
- `rp` = reasoning patterns; each has `p` (pattern), `ex` (example), `ts`
- `emph` = what they emphasize
- `dis` = what they dismiss
- `v` = vocabulary; each has `term` and `m` (meaning in their usage)

Rules:
- Extract the IMPLICIT method, not surface facts. "Hook the viewer in 5s"
  is a heuristic; "this video got 2M views" is not.
- Prefer concrete, transferable rules over abstract platitudes.
- 3–8 items per list is the sweet spot. If a category truly has nothing,
  return an empty list.
- `ev`/`ex` must be grounded in the transcript — short quote or tight
  paraphrase. No invention.
- `ts`: the transcript carries inline `[MM:SS]` markers roughly every
  30 seconds. Set `ts` to the nearest marker at or before the passage the
  item comes from (copy it without the brackets). If the transcript has
  no markers, use `""`. Never invent a timestamp.
- Use the short keys exactly. Do NOT expand them to verbose names.
- If the transcript is too short or off-topic, return the JSON with empty
  lists rather than hallucinating.
