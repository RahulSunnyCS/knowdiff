"""
Quote-mining intent: local-first quote search across a playlist's
transcripts. Zero Claude calls by default.

Reads themes from distilled/<playlist>/scope.json (or --themes flag).
Optionally reads alias map from distilled/<playlist>/themes.aliases.json.

Passes (all local):
  1. exact substring (case-insensitive)
  2. Porter-stemmed match
  3. alias-list match (manual phrase synonyms)

Output: distilled/<playlist>/quotes.md + citations.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import scope as scope_module

# Inline `[MM:SS]` markers that preprocess_transcript.py writes into
# transcript.clean.txt. They give every hit a timestamp and are stripped
# from the quote text itself.
MARKER_RE = re.compile(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]")


def strip_markers(text: str) -> str:
    return re.sub(r"\s+", " ", MARKER_RE.sub("", text)).strip()


def nearest_marker_before(sentences: list[str], idx: int) -> str:
    """Timestamp of the last marker at or before sentence idx, else ''."""
    for i in range(min(idx, len(sentences) - 1), -1, -1):
        found = MARKER_RE.findall(sentences[i])
        if found:
            return found[-1]
    return ""


def porter_stem(word: str) -> str:
    """Tiny stemmer — handles the common English suffixes that matter for
    phrase matching. Not a full Porter implementation; just enough to
    catch the cases the substring matcher misses."""
    w = word.lower()
    for suffix in ("ing", "edly", "ed", "ies", "es", "ly", "s"):
        if len(w) > len(suffix) + 2 and w.endswith(suffix):
            return w[: -len(suffix)]
    return w


def stem_phrase(phrase: str) -> str:
    return " ".join(porter_stem(t) for t in re.findall(r"[a-zA-Z]+", phrase))


def load_aliases(distilled_dir: Path) -> dict[str, list[str]]:
    p = distilled_dir / "themes.aliases.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text())


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def window(sentences: list[str], idx: int, before: int = 2, after: int = 2) -> str:
    lo = max(0, idx - before)
    hi = min(len(sentences), idx + after + 1)
    return strip_markers(" ".join(sentences[lo:hi]))


def find_hits(
    sentences: list[str],
    theme: str,
    aliases: list[str],
) -> list[tuple[int, str, str]]:
    """Return (sentence_idx, hit_text, match_kind)."""
    hits: list[tuple[int, str, str]] = []
    needles = [theme] + aliases
    stemmed_needles = {stem_phrase(n): n for n in needles if n}

    for i, sent in enumerate(sentences):
        lowered = sent.lower()
        matched_kind = None
        for n in needles:
            if n and n.lower() in lowered:
                matched_kind = "exact"
                break
        if not matched_kind:
            stemmed_sent = stem_phrase(sent)
            for sn in stemmed_needles:
                if sn and sn in stemmed_sent:
                    matched_kind = "stem"
                    break
        if matched_kind:
            hits.append((i, sent, matched_kind))
    return hits


def dedupe_hits(hits_by_video: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Collapse near-identical quotes within a single video."""
    out: dict[str, list[dict]] = {}
    for vid, items in hits_by_video.items():
        seen: list[set[str]] = []
        kept: list[dict] = []
        for item in items:
            tokens = set(re.findall(r"[a-z]{3,}", item["quote"].lower()))
            dup = False
            for s in seen:
                if tokens and len(tokens & s) / max(1, len(tokens | s)) > 0.8:
                    dup = True
                    break
            if not dup:
                seen.append(tokens)
                kept.append(item)
        out[vid] = kept
    return out


def render_markdown(playlist: str, themes: list[str], hits_by_theme: dict) -> str:
    lines = [f"# Quote mining — {playlist}\n"]
    for theme in themes:
        per_video = hits_by_theme.get(theme, {})
        total = sum(len(v) for v in per_video.values())
        lines.append(f"\n## Theme: `{theme}` — {total} quotes\n")
        if total == 0:
            lines.append("_No matches found._\n")
            continue
        for vid in sorted(per_video):
            lines.append(f"\n### {vid}\n")
            for hit in per_video[vid]:
                ts = f" `@ {hit['ts']}`" if hit.get("ts") else ""
                lines.append(f"- _[{hit['match']}]_{ts} {hit['quote']}\n")
    return "".join(lines)


def render_citations(hits_by_theme: dict) -> dict:
    citations: dict = defaultdict(list)
    for theme, per_video in hits_by_theme.items():
        for vid, hits in per_video.items():
            for hit in hits:
                citations[theme].append({
                    "video": vid,
                    "timestamp": hit.get("ts", ""),
                    "match_kind": hit["match"],
                    "quote": hit["quote"],
                })
    return dict(citations)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--playlist", required=True)
    p.add_argument("--output-root", default="output")
    p.add_argument("--distilled-root", default="distilled")
    p.add_argument("--themes", help="Comma-separated themes (overrides scope.json)")
    args = p.parse_args()

    distilled_root = Path(args.distilled_root)
    distilled_dir = distilled_root / args.playlist
    distilled_dir.mkdir(parents=True, exist_ok=True)

    if args.themes:
        themes = [t.strip() for t in args.themes.split(",") if t.strip()]
    else:
        scope = scope_module.load(distilled_root, args.playlist)
        if scope.intent != "quote-mining":
            print(f"warning: scope.intent={scope.intent} (expected quote-mining)")
        themes = scope.themes

    if not themes:
        sys.exit("No themes provided. Set scope.themes or pass --themes a,b,c")

    aliases_map = load_aliases(distilled_dir)
    playlist_dir = Path(args.output_root) / args.playlist
    video_dirs = sorted(d for d in playlist_dir.glob("video_*") if d.is_dir())
    if not video_dirs:
        sys.exit(f"No videos under {playlist_dir}")

    hits_by_theme: dict[str, dict[str, list[dict]]] = {t: {} for t in themes}

    for vd in video_dirs:
        vid = "_".join(vd.name.split("_")[:2])
        transcript_path = vd / "transcript.clean.txt"
        if not transcript_path.exists():
            transcript_path = vd / "transcript.txt"
        if not transcript_path.exists():
            continue
        sentences = split_sentences(transcript_path.read_text())
        for theme in themes:
            aliases = aliases_map.get(theme, [])
            raw_hits = find_hits(sentences, theme, aliases)
            if raw_hits:
                hits_by_theme[theme][vid] = [
                    {
                        "quote": window(sentences, i),
                        "match": kind,
                        "ts": nearest_marker_before(sentences, i),
                    }
                    for i, _sent, kind in raw_hits
                ]

    for theme in themes:
        hits_by_theme[theme] = dedupe_hits(hits_by_theme[theme])

    md = render_markdown(args.playlist, themes, hits_by_theme)
    (distilled_dir / "quotes.md").write_text(md)
    (distilled_dir / "citations.json").write_text(
        json.dumps(render_citations(hits_by_theme), indent=2)
    )

    total = sum(
        sum(len(v) for v in per_video.values())
        for per_video in hits_by_theme.values()
    )
    print(f"Quote mining done. {total} quotes across {len(themes)} themes. $0 spent.")
    print(f"  {distilled_dir / 'quotes.md'}")
    print(f"  {distilled_dir / 'citations.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
