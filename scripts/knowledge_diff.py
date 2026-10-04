#!/usr/bin/env python3
"""
knowledge_diff.py — "which minutes of this video are new to me?"
=================================================================
Compares one video's distilled items (claims, heuristics, reasoning
patterns — each with a `[MM:SS]` timestamp) against a personal knowledge
store and writes a watch-list of only the parts that are new, with deep
links.

The knowledge store is:
    * every per-video JSON already distilled — all other playlists, plus
      the EARLIER videos of the same playlist (so a point repeated in
      video 5 counts as known because video 3 made it, not vice versa);
    * a syllabus / progress file, `knowledge/syllabus.md` by default. Any
      plain or bulleted line is something you know; `- [x]` is known,
      `- [ ]` is "not yet" and is ignored. Headings are ignored.

Who decides "already covered?":
    * `--judge lexical` (default, free, offline): word overlap. Crude —
      it misses paraphrases — but needs nothing.
    * A connected Claude, through the MCP server (`get_diff_candidates`
      -> `save_knowledge_diff`): it reads each item next to its nearest
      known items and judges meaning, not wording. See prompts/06_knowledge_diff.md.

Outputs:
    distilled/<playlist>/knowledge_diff/<video_NN>.json
    distilled/<playlist>/watchlist.md          (all diffed videos)

USAGE:
    python scripts/knowledge_diff.py --playlist mycreator
    python scripts/knowledge_diff.py --playlist mycreator --video video_07 --threshold 0.4
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from preprocess_transcript import format_ts, parse_ts

# (top-level key, text key) for the item kinds that carry a timestamp.
ITEM_KINDS = (("cc", "c"), ("h", "r"), ("rp", "p"))
KIND_LABEL = {"cc": "claim", "h": "heuristic", "rp": "pattern"}
STATUSES = ("new", "partial", "known")

DEFAULT_SYLLABUS = Path("knowledge") / "syllabus.md"
DEFAULT_THRESHOLD = 0.5      # Jaccard at or above this -> "known" (lexical judge)
PARTIAL_BAND = 0.6           # ... at or above threshold * this -> "partial"
MAX_WINDOW_SEC = 180.0       # a single item never claims more than this
MIN_WINDOW_SEC = 30.0        # ... or less than one marker interval
MERGE_GAP_SEC = 30.0         # ranges closer than this are joined

_STOPWORDS = frozenset("""
a an and are as at be been but by can do does for from had has have how i if in into is it its
just more most not of on or our so than that the their them then there these they this to up
use used using was we were what when which who will with you your should would could about
""".split())
_WORD_RE = re.compile(r"[a-z0-9]+")
_VIDEO_RE = re.compile(r"^video_(\d+)$")


# ── Items ────────────────────────────────────────────────────────────────────
def items_of(distilled: dict) -> list[dict]:
    """Timestamped items of one compact per-video JSON, in schema order."""
    items: list[dict] = []
    for kind, text_key in ITEM_KINDS:
        for idx, entry in enumerate(distilled.get(kind) or []):
            if not isinstance(entry, dict):
                continue
            text = str(entry.get(text_key) or "").strip()
            if not text:
                continue
            items.append({
                "id": f"{kind}{idx}",
                "kind": KIND_LABEL[kind],
                "text": text,
                "ts": str(entry.get("ts") or "").strip().strip("[]"),
            })
    return items


def _video_number(stem: str) -> int | None:
    m = _VIDEO_RE.match(stem)
    return int(m.group(1)) if m else None


def load_store(distilled_root: Path, playlist: str, video: str) -> list[dict]:
    """Known items: other playlists' videos + earlier videos of this playlist."""
    target_n = _video_number(video)
    store: list[dict] = []
    if not distilled_root.is_dir():
        return store
    for pdir in sorted(p for p in distilled_root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        for path in sorted(pdir.glob("video_*.json")):
            n = _video_number(path.stem)
            if n is None:
                continue
            if pdir.name == playlist and (target_n is None or n >= target_n):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            for item in items_of(data):
                store.append({"text": item["text"], "source": f"{pdir.name}/{path.stem}", "ts": item["ts"]})
    return store


_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_CHECKBOX_RE = re.compile(r"^\[( |x|X)\]\s*")


def parse_syllabus(text: str) -> list[dict]:
    """Known items from a syllabus/progress file (see module docstring)."""
    known: list[dict] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = _BULLET_RE.sub("", line)
        box = _CHECKBOX_RE.match(line)
        if box:
            if box.group(1) == " ":
                continue                      # "- [ ]" = not learned yet
            line = line[box.end():]
        line = line.strip()
        if line:
            known.append({"text": line, "source": "syllabus", "ts": ""})
    return known


def load_syllabus(path: Path) -> list[dict]:
    try:
        return parse_syllabus(path.read_text(encoding="utf-8"))
    except OSError:
        return []


# ── Lexical similarity ───────────────────────────────────────────────────────
def tokens(text: str) -> frozenset[str]:
    out = set()
    for w in _WORD_RE.findall(text.lower()):
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]                        # crude plural fold
        if len(w) > 2 and w not in _STOPWORDS:
            out.add(w)
    return frozenset(out)


def similarity(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def nearest_known(text: str, store: list[dict], k: int = 5) -> list[dict]:
    """The k known items most similar to `text`, best first (similarity > 0)."""
    mine = tokens(text)
    scored = []
    for known in store:
        if "_tokens" not in known:
            known["_tokens"] = tokens(known["text"])
        sim = similarity(mine, known["_tokens"])
        if sim > 0:
            scored.append((sim, known))
    scored.sort(key=lambda pair: -pair[0])
    return [{"text": kn["text"], "source": kn["source"], "similarity": round(sim, 2)} for sim, kn in scored[:k]]


def lexical_verdicts(items: list[dict], store: list[dict], threshold: float = DEFAULT_THRESHOLD) -> list[dict]:
    verdicts = []
    for item in items:
        best = (nearest_known(item["text"], store, k=1) or [None])[0]
        sim = best["similarity"] if best else 0.0
        status = "known" if sim >= threshold else "partial" if sim >= threshold * PARTIAL_BAND else "new"
        verdict = {"id": item["id"], "status": status}
        if best and status != "new":
            verdict["covered_by"] = f"{best['source']}: {best['text']}"
        verdicts.append(verdict)
    return verdicts


# ── Verdicts -> watch ranges ─────────────────────────────────────────────────
class DiffError(ValueError):
    """Verdicts that do not line up with the video's items."""


def check_verdicts(items: list[dict], verdicts: list[dict]) -> dict[str, dict]:
    """Exactly one valid verdict per item, or DiffError saying what is wrong."""
    by_id: dict[str, dict] = {}
    problems: list[str] = []
    ids = {i["id"] for i in items}
    for v in verdicts if isinstance(verdicts, list) else []:
        vid = v.get("id") if isinstance(v, dict) else None
        if vid not in ids:
            problems.append(f"unknown id {vid!r}")
        elif vid in by_id:
            problems.append(f"duplicate verdict for {vid}")
        elif v.get("status") not in STATUSES:
            problems.append(f"{vid}: status must be one of {', '.join(STATUSES)}")
        elif v["status"] == "known" and not str(v.get("covered_by") or "").strip():
            problems.append(f"{vid}: a known item needs covered_by (what already teaches it)")
        else:
            by_id[vid] = v
    missing = sorted(ids - set(by_id))
    if missing:
        problems.append("no verdict for: " + ", ".join(missing))
    if problems:
        raise DiffError("; ".join(problems))
    return by_id


def watch_ranges(items: list[dict], by_id: dict[str, dict], duration: float | None) -> list[dict]:
    """Merged [start, end] ranges (seconds) covering the items not yet known.

    An item's passage runs from its marker to the next item's marker,
    bounded to MIN_WINDOW_SEC..MAX_WINDOW_SEC and to the video's length.
    """
    timed = [(parse_ts(i["ts"]), i) for i in items]
    starts = sorted({t for t, _ in timed if t is not None})
    raw: list[tuple[float, float, dict]] = []
    for t, item in timed:
        if t is None or by_id[item["id"]]["status"] == "known":
            continue
        later = [s for s in starts if s > t]
        end = min(later[0] if later else t + MAX_WINDOW_SEC, t + MAX_WINDOW_SEC)
        end = max(end, t + MIN_WINDOW_SEC)
        if duration:
            end = min(end, max(duration, t))
        raw.append((t, end, item))
    raw.sort(key=lambda r: r[0])

    merged: list[dict] = []
    for start, end, item in raw:
        if merged and start <= merged[-1]["end"] + MERGE_GAP_SEC:
            merged[-1]["end"] = max(merged[-1]["end"], end)
            merged[-1]["items"].append(item["id"])
        else:
            merged.append({"start": start, "end": end, "items": [item["id"]]})
    return merged


def deep_link(url: str | None, seconds: float) -> str | None:
    if not url:
        return None
    return f"{url}{'&' if '?' in url else '?'}t={int(seconds)}s"


def build_diff(items: list[dict], verdicts: list[dict], *, video: str, title: str | None,
               url: str | None, duration: float | None, judge: str) -> dict:
    by_id = check_verdicts(items, verdicts)
    ranges = watch_ranges(items, by_id, duration)
    for r in ranges:
        r["link"] = deep_link(url, r["start"])
    counts = {s: sum(1 for v in by_id.values() if v["status"] == s) for s in STATUSES}
    watch_sec = sum(r["end"] - r["start"] for r in ranges)
    return {
        "video": video,
        "title": title,
        "url": url,
        "judge": judge,
        "duration_sec": duration,
        "watch_sec": round(watch_sec, 1),
        "counts": counts,
        "ranges": ranges,
        "items": [{**i, **{k: by_id[i["id"]][k] for k in ("status", "covered_by", "note") if k in by_id[i["id"]]}}
                  for i in items],
    }


# ── Rendering ────────────────────────────────────────────────────────────────
def _minutes(seconds: float) -> str:
    return f"{seconds / 60:.0f}" if seconds >= 600 else f"{seconds / 60:.1f}"


def render_video(diff: dict) -> str:
    title = diff.get("title") or diff["video"]
    head = f"## {diff['video']} — {title}"
    c = diff["counts"]
    total = f" of {_minutes(diff['duration_sec'])}" if diff.get("duration_sec") else ""
    lines = [head, ""]
    if not diff["ranges"]:
        lines.append(f"Nothing new: all {c['known']} items are already covered. Skip it.")
    else:
        lines.append(f"**Watch {_minutes(diff['watch_sec'])}{total} minutes** — "
                     f"{c['new']} new, {c['partial']} partly new, {c['known']} already known "
                     f"(judge: {diff['judge']}).")
    by_id = {i["id"]: i for i in diff["items"]}
    for r in diff["ranges"]:
        span = f"{format_ts(r['start'])}–{format_ts(r['end'])}"
        lines += ["", f"### [{span}]({r['link']})" if r.get("link") else f"### {span}"]
        for item_id in r["items"]:
            item = by_id[item_id]
            tag = " _(partly known)_" if item["status"] == "partial" else ""
            lines.append(f"- `{item['ts']}` {item['text']}{tag}")
    unplaced = [i for i in diff["items"] if i["status"] != "known" and parse_ts(i["ts"]) is None]
    if unplaced:
        lines += ["", "New, but without a timestamp:"] + [f"- {i['text']}" for i in unplaced]
    known = [i for i in diff["items"] if i["status"] == "known"]
    if known:
        lines += ["", "<details><summary>Already known (skipped)</summary>", ""]
        for i in known:
            why = f" — {i['covered_by']}" if i.get("covered_by") else ""
            lines.append(f"- `{i['ts'] or '--:--'}` {i['text']}{why}")
        lines += ["", "</details>"]
    return "\n".join(lines)


def render_watchlist(playlist: str, diffs: list[dict]) -> str:
    watch = sum(d["watch_sec"] for d in diffs)
    total = sum(d["duration_sec"] or 0 for d in diffs)
    summary = f"Watch **{_minutes(watch)} minutes**"
    if total:
        summary += f" of {_minutes(total)}"
    parts = [f"# Watch-list — {playlist}", "", f"{summary} across {len(diffs)} video(s).", ""]
    parts += [render_video(d) + "\n" for d in diffs]
    return "\n".join(parts)


# ── Files ────────────────────────────────────────────────────────────────────
def video_meta(output_root: Path, playlist: str, video: str) -> dict:
    """title / url / duration for a video id, from the extraction folder."""
    meta: dict = {}
    for d in sorted((output_root / playlist).glob(f"{video}_*")):
        try:
            meta = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
        if not meta.get("duration"):
            try:
                segs = json.loads((d / "transcript.timestamped.json").read_text(encoding="utf-8"))["segments"]
                meta["duration"] = max(s["end"] for s in segs)
            except (OSError, json.JSONDecodeError, KeyError, ValueError):
                pass
        break
    return meta


def load_items(distilled_root: Path, playlist: str, video: str) -> list[dict]:
    path = distilled_root / playlist / f"{video}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise DiffError(f"{video} has no readable distilled JSON ({path}): distill it first.") from e
    return items_of(data if isinstance(data, dict) else {})


def knowledge_store(distilled_root: Path, playlist: str, video: str, syllabus_path: Path) -> list[dict]:
    return load_store(distilled_root, playlist, video) + load_syllabus(syllabus_path)


def save_diff(distilled_root: Path, output_root: Path, playlist: str, video: str,
              verdicts: list[dict], judge: str) -> dict:
    """Validate verdicts, write the per-video diff and refresh watchlist.md."""
    items = load_items(distilled_root, playlist, video)
    meta = video_meta(output_root, playlist, video)
    diff = build_diff(items, verdicts, video=video, title=meta.get("title"), url=meta.get("url"),
                      duration=meta.get("duration"), judge=judge)
    ddir = distilled_root / playlist / "knowledge_diff"
    ddir.mkdir(parents=True, exist_ok=True)
    (ddir / f"{video}.json").write_text(json.dumps(diff, ensure_ascii=False, indent=2), encoding="utf-8")
    diffs = []
    for p in sorted(ddir.glob("video_*.json")):
        try:
            diffs.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    (distilled_root / playlist / "watchlist.md").write_text(render_watchlist(playlist, diffs), encoding="utf-8")
    return diff


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Watch-list of what is new in a playlist, judged by word overlap.")
    p.add_argument("--playlist", required=True)
    p.add_argument("--video", help="One video id (video_NN). Default: every distilled video.")
    p.add_argument("--output-root", default="output")
    p.add_argument("--distilled-root", default="distilled")
    p.add_argument("--syllabus", default=str(DEFAULT_SYLLABUS))
    p.add_argument("--judge", choices=["lexical"], default="lexical",
                   help="lexical = word overlap (free, offline). For a judgement of meaning, "
                        "connect Claude through the MCP server instead.")
    p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                   help="Jaccard word overlap at or above which an item counts as known.")
    args = p.parse_args(argv)

    distilled_root, output_root = Path(args.distilled_root), Path(args.output_root)
    pdir = distilled_root / args.playlist
    videos = [args.video] if args.video else sorted(
        q.stem for q in pdir.glob("video_*.json") if _video_number(q.stem) is not None)
    if not videos:
        print(f"No distilled videos under {pdir}. Distill first (Phase 2 or the MCP server).", file=sys.stderr)
        return 1
    for video in videos:
        try:
            items = load_items(distilled_root, args.playlist, video)
            store = knowledge_store(distilled_root, args.playlist, video, Path(args.syllabus))
            diff = save_diff(distilled_root, output_root, args.playlist, video,
                             lexical_verdicts(items, store, args.threshold), judge="lexical")
        except DiffError as e:
            print(f"  ✗ {video}: {e}", file=sys.stderr)
            return 1
        c = diff["counts"]
        print(f"  ✓ {video}: watch {_minutes(diff['watch_sec'])} min — "
              f"{c['new']} new, {c['partial']} partial, {c['known']} known ({len(store)} known items in store)")
    print(f"Watch-list: {pdir / 'watchlist.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
