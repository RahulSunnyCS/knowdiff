"""Shared test helpers. Tests import the scripts as top-level modules the
same way the scripts import each other (`import scope`), so the scripts
directory goes on sys.path."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def write_segments(video_dir: Path, segments: list[dict], source: str = "youtube_captions") -> None:
    import json
    video_dir.mkdir(parents=True, exist_ok=True)
    (video_dir / "transcript.txt").write_text(" ".join(s["text"] for s in segments))
    (video_dir / "transcript.timestamped.json").write_text(json.dumps({
        "source": source, "language": "en", "segments": segments,
    }))


def spoken(start: float, text: str, dur: float = 4.0) -> dict:
    return {"start": start, "end": start + dur, "text": text}
