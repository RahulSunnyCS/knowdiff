# Targeted topical extraction (Phase 2, topical-report intent)

You will receive:
1. A user QUESTION (e.g. "what does the creator think about commodities?").
2. ONE video's transcript from the creator's playlist.

Extract ONLY statements relevant to the question. Skip everything else.
If the video doesn't discuss the topic at all, return empty lists.

Return ONLY a single JSON object with this compact shape:

```
{
  "vid": "<video id>",
  "stmts": [
    {
      "s": "<the creator's statement, paraphrased tight>",
      "q": "<short verbatim quote anchoring it>",
      "ts": "MM:SS",
      "stance": "asserts | qualifies | rejects | wonders",
      "facet": "<one-line topical sub-area, e.g. 'inflation hedge', 'storage cost'>"
    }
  ],
  "rel_score": 0-1.0
}
```

Rules:
- `rel_score` is your honest estimate of how much of this video addresses the
  question. 0.0 if not at all; 1.0 if the entire video is about it.
- `stance` distinguishes the creator's posture so the synthesis pass can
  separate "they believe X" from "they reject X".
- `facet` is a short label to enable clustering across videos.
- `ts`: the transcript carries inline `[MM:SS]` markers roughly every 30
  seconds. Use the nearest marker at or before the statement (without the
  brackets). If there are no markers, use `""`. Never invent a timestamp.
- Do NOT extract general method/heuristics here (that's the method-distillation
  prompt's job). Stick to topical content.
- If the transcript truly has nothing relevant, return empty `stmts` and
  `rel_score: 0.0`. Do not pad.
