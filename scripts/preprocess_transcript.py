"""
Phase 1.5: local transcript preprocessing.

Sits between Phase 1 (extract) and Phase 2 (distill). Reads the raw
transcript and produces a cleaned version with filler, intros/outros,
sponsor reads, and repeats removed. Optionally splits by YouTube
chapters when timestamps are available in the description.

Timestamps. When transcript.timestamped.json exists (it always does for
captions and Whisper output), the cleaned transcript is rendered from
the segments with an inline `[MM:SS]` marker roughly every
--marker-interval seconds (default 30). Phase 2 / topical extraction
read those markers to fill their `ts` fields, which is what makes the
citations sidecar point at real moments instead of guesses. Intro /
outro trimming and chapter splitting also use the real segment times in
that case; the character-proportion heuristics are only the fallback
for transcripts that have no sidecar.

Phase 2 reads transcript.clean.txt, not transcript.txt. Original is
never modified.

Usage:
    # Preprocess every video under a playlist:
    python scripts/preprocess_transcript.py --playlist <name>

    # Single video directory:
    python scripts/preprocess_transcript.py --video-dir output/<playlist>/video_07_*

    # Disable specific passes:
    python scripts/preprocess_transcript.py --playlist <name> --no-sponsor-detect

All cuts are logged to preprocess.json next to the cleaned transcript so
the user can audit what was removed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


FILLERS = {
    "um", "uh", "uhh", "umm", "er", "ah", "mm",
    "you know", "i mean", "sort of", "kind of",
    "basically", "literally", "honestly", "actually",
    "right?", "okay?", "ok?",
}

SPONSOR_MARKERS = (
    "sponsor", "sponsored by", "brought to you by",
    "today's video is brought to you", "promo code",
    "link in the description", "link in description",
    "use code", "discount code", "affiliate link",
    "this video is sponsored",
)

REPEAT_WINDOW_SECONDS = 60
REPEAT_JACCARD_THRESHOLD = 0.8

DEFAULT_MARKER_INTERVAL_SEC = 30.0
# Intro and outro trims are each capped at this share of the video's length.
DEFAULT_MAX_TRIM_FRACTION = 0.05

# Inline timestamp marker as rendered into transcript.clean.txt.
MARKER_RE = re.compile(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]")


def format_ts(seconds: float) -> str:
    """Seconds -> MM:SS (or H:MM:SS past the hour)."""
    total = int(max(0.0, seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def parse_ts(text: str) -> float | None:
    """'MM:SS' or 'H:MM:SS' -> seconds, else None."""
    m = re.fullmatch(r"\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\s*", text or "")
    if not m:
        return None
    h = int(m.group(1)) if m.group(1) else 0
    return h * 3600 + int(m.group(2)) * 60 + int(m.group(3))


@dataclass
class Cut:
    reason: str
    start: float | None
    end: float | None
    text: str


@dataclass
class PreprocessReport:
    input_path: str
    output_path: str
    original_chars: int = 0
    cleaned_chars: int = 0
    cuts: list[Cut] = field(default_factory=list)
    chapters_detected: int = 0
    timestamped: bool = False
    marker_count: int = 0

    def to_dict(self) -> dict:
        return {
            "input_path": self.input_path,
            "output_path": self.output_path,
            "original_chars": self.original_chars,
            "cleaned_chars": self.cleaned_chars,
            "reduction_pct": (
                round(100 * (1 - self.cleaned_chars / self.original_chars), 1)
                if self.original_chars
                else 0.0
            ),
            "chapters_detected": self.chapters_detected,
            "timestamped": self.timestamped,
            "marker_count": self.marker_count,
            "cuts": [
                {
                    "reason": c.reason,
                    "start": c.start,
                    "end": c.end,
                    "text": c.text[:200] + ("..." if len(c.text) > 200 else ""),
                }
                for c in self.cuts
            ],
        }


# ── Segment-aware helpers (used when transcript.timestamped.json exists) ──────

def load_segments(video_dir: Path) -> list[dict] | None:
    """Return the sidecar's segments, or None if absent/unusable."""
    p = video_dir / "transcript.timestamped.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    segs = data.get("segments") or []
    out: list[dict] = []
    for s in segs:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        try:
            start = float(s.get("start", 0.0))
            end = float(s.get("end", start))
        except (TypeError, ValueError):
            continue
        out.append({"start": start, "end": end, "text": text})
    out.sort(key=lambda s: s["start"])
    return out or None


def render_with_markers(segments: list[dict], interval_sec: float = DEFAULT_MARKER_INTERVAL_SEC) -> tuple[str, int]:
    """Join segment texts, inserting `[MM:SS]` before the first segment that
    starts at or after each interval boundary. Returns (text, marker_count).

    The first segment always gets a marker so a reader (or Claude) can
    anchor the opening."""
    if interval_sec <= 0:
        return " ".join(s["text"] for s in segments), 0
    parts: list[str] = []
    next_boundary = 0.0
    count = 0
    for seg in segments:
        if seg["start"] >= next_boundary:
            parts.append(f"[{format_ts(seg['start'])}]")
            count += 1
            # Jump to the next boundary strictly after this segment's start.
            next_boundary = (seg["start"] // interval_sec + 1) * interval_sec
        parts.append(seg["text"])
    text = re.sub(r"[ \t]+", " ", " ".join(parts)).strip()
    return text, count


def trim_intro_outro_segments(
    segments: list[dict], intro_sec: float, outro_sec: float
) -> tuple[list[dict], list[Cut]]:
    """Drop segments inside the first intro_sec / last outro_sec of speech."""
    if not segments or (intro_sec <= 0 and outro_sec <= 0):
        return segments, []
    end_of_speech = max(s["end"] for s in segments)
    cuts: list[Cut] = []
    kept: list[dict] = []
    intro_dropped: list[str] = []
    outro_dropped: list[str] = []
    for s in segments:
        if intro_sec > 0 and s["start"] < intro_sec:
            intro_dropped.append(s["text"])
        elif outro_sec > 0 and s["start"] > end_of_speech - outro_sec:
            outro_dropped.append(s["text"])
        else:
            kept.append(s)
    # Never trim a transcript down to nothing (very short clips).
    if not kept:
        return segments, []
    if intro_dropped:
        cuts.append(Cut(reason="intro", start=0.0, end=intro_sec, text=" ".join(intro_dropped)))
    if outro_dropped:
        cuts.append(Cut(reason="outro", start=max(0.0, end_of_speech - outro_sec),
                        end=end_of_speech, text=" ".join(outro_dropped)))
    return kept, cuts


def split_segments_by_chapters(
    segments: list[dict], chapters: list[tuple[float, str]]
) -> list[tuple[str, list[dict]]]:
    """Exact split: each chapter gets the segments starting in its window."""
    if len(chapters) < 2:
        return []
    out: list[tuple[str, list[dict]]] = []
    for i, (start, title) in enumerate(chapters):
        end = chapters[i + 1][0] if i + 1 < len(chapters) else float("inf")
        chunk = [s for s in segments if start <= s["start"] < end]
        out.append((title, chunk))
    return out


# ── Text-only passes (work with or without timestamps) ────────────────────────

def strip_fillers(text: str) -> tuple[str, list[Cut]]:
    cuts: list[Cut] = []
    cleaned = text
    for filler in sorted(FILLERS, key=len, reverse=True):
        pattern = re.compile(
            r"(?:^|(?<=[\s,.;:!?]))" + re.escape(filler) + r"(?=[\s,.;:!?]|$)",
            re.IGNORECASE,
        )
        matches = list(pattern.finditer(cleaned))
        if matches:
            cuts.append(Cut(reason="filler", start=None, end=None, text=f"{filler} x{len(matches)}"))
            cleaned = pattern.sub("", cleaned)
    # Collapse the whitespace those removals created.
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\s+([,.;:!?])", r"\1", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned, cuts


def trim_intro_outro(text: str, intro_sec: float, outro_sec: float) -> tuple[str, list[Cut]]:
    """Trim by character proxy when no timestamps are available.

    Assumes ~150 words/minute, ~5 chars/word -> ~750 chars/minute.
    """
    if intro_sec <= 0 and outro_sec <= 0:
        return text, []
    chars_per_sec = 12.5
    cuts: list[Cut] = []
    intro_chars = int(intro_sec * chars_per_sec)
    outro_chars = int(outro_sec * chars_per_sec)
    if intro_chars and len(text) > intro_chars:
        cuts.append(Cut(reason="intro", start=0, end=intro_sec, text=text[:intro_chars]))
        text = text[intro_chars:]
    if outro_chars and len(text) > outro_chars:
        cuts.append(Cut(reason="outro", start=None, end=None, text=text[-outro_chars:]))
        text = text[:-outro_chars]
    return text, cuts


def detect_sponsor_blocks(text: str, window_chars: int = 600) -> tuple[str, list[Cut]]:
    """Drop ~window_chars around any cluster of sponsor markers."""
    cuts: list[Cut] = []
    lowered = text.lower()
    hits = []
    for marker in SPONSOR_MARKERS:
        start = 0
        while True:
            idx = lowered.find(marker, start)
            if idx == -1:
                break
            hits.append(idx)
            start = idx + len(marker)
    if not hits:
        return text, cuts

    hits.sort()
    ranges: list[tuple[int, int]] = []
    for idx in hits:
        lo = max(0, idx - window_chars // 2)
        hi = min(len(text), idx + window_chars // 2)
        if ranges and lo <= ranges[-1][1]:
            ranges[-1] = (ranges[-1][0], max(ranges[-1][1], hi))
        else:
            ranges.append((lo, hi))

    out: list[str] = []
    cursor = 0
    for lo, hi in ranges:
        out.append(text[cursor:lo])
        cuts.append(Cut(reason="sponsor", start=None, end=None, text=text[lo:hi]))
        cursor = hi
    out.append(text[cursor:])
    return "".join(out), cuts


def _tokenize(s: str) -> set[str]:
    return set(re.findall(r"[a-z]{3,}", s.lower()))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def collapse_repeats(text: str) -> tuple[str, list[Cut]]:
    """Near-duplicate sentence collapse within a sliding window."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    keep_flags = [True] * len(sentences)
    cuts: list[Cut] = []
    tokens = [_tokenize(s) for s in sentences]
    for i in range(len(sentences)):
        if not keep_flags[i]:
            continue
        for j in range(i + 1, min(i + 10, len(sentences))):
            if not keep_flags[j]:
                continue
            if _jaccard(tokens[i], tokens[j]) >= REPEAT_JACCARD_THRESHOLD:
                # Keep the longer of the two; drop the other.
                if len(sentences[i]) >= len(sentences[j]):
                    keep_flags[j] = False
                    cuts.append(Cut(reason="repeat", start=None, end=None, text=sentences[j]))
                else:
                    keep_flags[i] = False
                    cuts.append(Cut(reason="repeat", start=None, end=None, text=sentences[i]))
                    break
    out = " ".join(s for s, keep in zip(sentences, keep_flags) if keep)
    return out, cuts


CHAPTER_LINE_RE = re.compile(r"^\s*(\d{1,2}:)?(\d{1,2}):(\d{2})\s+(.+?)\s*$", re.MULTILINE)


def parse_chapters(description_path: Path) -> list[tuple[float, str]]:
    """Extract (start_seconds, title) tuples from a video description file."""
    if not description_path.exists():
        return []
    text = description_path.read_text(errors="ignore")
    chapters: list[tuple[float, str]] = []
    for match in CHAPTER_LINE_RE.finditer(text):
        h = int(match.group(1)[:-1]) if match.group(1) else 0
        m = int(match.group(2))
        s = int(match.group(3))
        title = match.group(4).strip()
        seconds = h * 3600 + m * 60 + s
        chapters.append((seconds, title))
    chapters.sort()
    # YouTube chapter convention requires the first chapter to start at 00:00.
    if not chapters or chapters[0][0] != 0:
        return []
    return chapters


def split_by_chapters(text: str, chapters: list[tuple[float, str]]) -> list[tuple[str, str]]:
    """Crude proportional split — fallback when no segment times exist."""
    if len(chapters) < 2:
        return []
    total_chars = len(text)
    # Estimate per-chapter duration by next-start - this-start.
    durations: list[float] = []
    for i, (start, _) in enumerate(chapters):
        if i + 1 < len(chapters):
            durations.append(chapters[i + 1][0] - start)
        else:
            durations.append(max(60.0, durations[-1] if durations else 60.0))
    total_dur = sum(durations)
    cursor = 0
    out: list[tuple[str, str]] = []
    for (_, title), dur in zip(chapters, durations):
        chars = int(total_chars * dur / total_dur)
        chunk = text[cursor : cursor + chars]
        cursor += chars
        out.append((title, chunk))
    return out


def _safe_filename(title: str, idx: int) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", title.lower()).strip("_")[:50]
    return f"{idx:02d}_{slug or 'chapter'}.txt"


def _text_passes(
    text: str,
    report: PreprocessReport,
    *,
    do_sponsor: bool,
    do_filler: bool,
    do_repeats: bool,
) -> str:
    if do_sponsor:
        text, sponsor_cuts = detect_sponsor_blocks(text)
        report.cuts.extend(sponsor_cuts)
    if do_filler:
        text, filler_cuts = strip_fillers(text)
        report.cuts.extend(filler_cuts)
    if do_repeats:
        text, repeat_cuts = collapse_repeats(text)
        report.cuts.extend(repeat_cuts)
    return text


def preprocess_video_dir(
    video_dir: Path,
    *,
    intro_sec: float = 30.0,
    outro_sec: float = 30.0,
    do_filler: bool = True,
    do_sponsor: bool = True,
    do_repeats: bool = True,
    do_chapters: bool = True,
    marker_interval_sec: float = DEFAULT_MARKER_INTERVAL_SEC,
    max_trim_fraction: float = DEFAULT_MAX_TRIM_FRACTION,
) -> PreprocessReport:
    transcript_path = video_dir / "transcript.txt"
    if not transcript_path.exists():
        raise FileNotFoundError(transcript_path)

    raw = transcript_path.read_text()
    report = PreprocessReport(
        input_path=str(transcript_path),
        output_path=str(video_dir / "transcript.clean.txt"),
        original_chars=len(raw),
    )

    segments = load_segments(video_dir)
    chapters = parse_chapters(video_dir / "description.txt") if do_chapters else []
    chapter_chunks: list[tuple[str, str]] = []

    if segments:
        # ── Timestamp-aware path ──
        report.timestamped = True
        # A fixed 30 s off each end is right for a long lecture and far too
        # much for a 2-minute clip, so each trim is capped at a share of
        # the video's length.
        cap = max(s["end"] for s in segments) * max_trim_fraction
        intro_sec, outro_sec = min(intro_sec, cap), min(outro_sec, cap)
        kept, trim_cuts = trim_intro_outro_segments(segments, intro_sec, outro_sec)
        report.cuts.extend(trim_cuts)
        text, markers = render_with_markers(kept, marker_interval_sec)
        report.marker_count = markers
        text = _text_passes(text, report, do_sponsor=do_sponsor,
                            do_filler=do_filler, do_repeats=do_repeats)
        if chapters:
            for title, chunk_segs in split_segments_by_chapters(kept, chapters):
                chunk_text, _ = render_with_markers(chunk_segs, marker_interval_sec)
                if do_filler:
                    chunk_text, _ = strip_fillers(chunk_text)
                chapter_chunks.append((title, chunk_text))
    else:
        # ── Fallback: flat text, character-proportion heuristics ──
        text = raw
        text, intro_cuts = trim_intro_outro(text, intro_sec, outro_sec)
        report.cuts.extend(intro_cuts)
        text = _text_passes(text, report, do_sponsor=do_sponsor,
                            do_filler=do_filler, do_repeats=do_repeats)
        if chapters:
            chapter_chunks = split_by_chapters(text, chapters)

    report.cleaned_chars = len(text)
    (video_dir / "transcript.clean.txt").write_text(text)

    if chapter_chunks:
        chap_dir = video_dir / "chapters"
        chap_dir.mkdir(exist_ok=True)
        for idx, (title, chunk) in enumerate(chapter_chunks, start=1):
            (chap_dir / _safe_filename(title, idx)).write_text(chunk)
        report.chapters_detected = len(chapter_chunks)

    (video_dir / "preprocess.json").write_text(json.dumps(report.to_dict(), indent=2))
    return report


def iter_video_dirs(playlist_dir: Path) -> Iterable[Path]:
    return sorted(d for d in playlist_dir.glob("video_*") if d.is_dir())


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--playlist", help="Playlist directory name under output/")
    p.add_argument("--video-dir", help="Single video directory to preprocess")
    p.add_argument("--output-root", default="output")
    p.add_argument("--intro-sec", type=float, default=30.0)
    p.add_argument("--outro-sec", type=float, default=30.0)
    p.add_argument("--marker-interval", type=float, default=DEFAULT_MARKER_INTERVAL_SEC,
                   help="Seconds between inline [MM:SS] markers (0 disables). "
                        "Needs transcript.timestamped.json.")
    p.add_argument("--no-filler-strip", action="store_true")
    p.add_argument("--no-sponsor-detect", action="store_true")
    p.add_argument("--no-repeat-collapse", action="store_true")
    p.add_argument("--no-chapters", action="store_true")
    p.add_argument("--no-preprocess", action="store_true",
                   help="Copy transcript.txt to transcript.clean.txt unchanged")
    args = p.parse_args()

    if not args.playlist and not args.video_dir:
        sys.exit("--playlist or --video-dir is required")

    if args.video_dir:
        dirs = [Path(args.video_dir)]
    else:
        dirs = list(iter_video_dirs(Path(args.output_root) / args.playlist))
        if not dirs:
            sys.exit(f"No video_* dirs under {args.output_root}/{args.playlist}")

    for vd in dirs:
        if args.no_preprocess:
            raw = (vd / "transcript.txt").read_text()
            (vd / "transcript.clean.txt").write_text(raw)
            print(f"  = {vd.name}: copied unchanged")
            continue
        try:
            report = preprocess_video_dir(
                vd,
                intro_sec=args.intro_sec,
                outro_sec=args.outro_sec,
                do_filler=not args.no_filler_strip,
                do_sponsor=not args.no_sponsor_detect,
                do_repeats=not args.no_repeat_collapse,
                do_chapters=not args.no_chapters,
                marker_interval_sec=args.marker_interval,
            )
            pct = (
                round(100 * (1 - report.cleaned_chars / report.original_chars), 1)
                if report.original_chars else 0.0
            )
            ts_note = f", {report.marker_count} markers" if report.timestamped else ", no timestamps"
            print(
                f"  ✓ {vd.name}: {report.original_chars} -> {report.cleaned_chars} chars "
                f"(-{pct}%, {len(report.cuts)} cuts, {report.chapters_detected} chapters{ts_note})"
            )
        except FileNotFoundError as exc:
            print(f"  ✗ {vd.name}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
