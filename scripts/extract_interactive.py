#!/usr/bin/env python3
"""
Interactive front-end for extract_playlist.py.

When a value comes in via environment variable (PLAYLIST, PLAYLIST_NAME,
MODE, VIDEOS, JOBS, OUT) we use it without prompting. Anything missing
gets asked for, with a sensible default shown in [brackets] — just hit
Enter to accept.

Invoked by `make extract`. For non-interactive runs (CI, scripts),
either pass every variable on the make command line or invoke
scripts/extract_playlist.py directly.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path


def _is_tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _ask(prompt: str, default: str = "", *, allow_empty: bool = True) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        try:
            raw = input(f"{prompt}{suffix}: ").strip()
        except EOFError:
            raw = ""
        value = raw or default
        if value or allow_empty:
            return value
        print("  (required — please enter a value)")


def _ask_choice(prompt: str, choices: list[str], default: str) -> str:
    rendered = "/".join(c if c != default else c.upper() for c in choices)
    while True:
        raw = _ask(f"{prompt} ({rendered})", default)
        if raw in choices:
            return raw
        print(f"  choose one of: {', '.join(choices)}")


def _ask_int(prompt: str, default: int, *, minimum: int = 1) -> int:
    while True:
        raw = _ask(prompt, str(default))
        try:
            n = int(raw)
        except ValueError:
            print("  not a number, try again")
            continue
        if n < minimum:
            print(f"  must be >= {minimum}")
            continue
        return n


_RANGE_RE = re.compile(r"^\s*\d+(\s*-\s*\d+)?(\s*,\s*\d+(\s*-\s*\d+)?)*\s*$")


def _ask_videos() -> str:
    print()
    print("Which videos do you want?")
    print("  1) all videos in the playlist")
    print("  2) first N (e.g. first 10)")
    print("  3) a range (e.g. 10-25) or pick list (e.g. 1,3,5-7)")
    while True:
        pick = _ask("Choose 1/2/3", "1")
        if pick == "1":
            return ""
        if pick == "2":
            n = _ask_int("How many videos from the start", 10)
            return f"1-{n}"
        if pick == "3":
            while True:
                spec = _ask("Range or list", "10-25", allow_empty=False)
                if _RANGE_RE.match(spec):
                    return spec
                print("  format: '10-25', '5', or '1,3,5-7'")
        print("  pick 1, 2, or 3")


def _ask_playlist_name(url: str) -> str:
    default = "playlist"
    if url:
        m = re.search(r"list=([A-Za-z0-9_-]+)", url)
        if m:
            default = m.group(1)[:24].lower()
    return _ask("Output folder name under output/", default, allow_empty=False)


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    extractor = repo_root / "scripts" / "extract_playlist.py"

    env = os.environ
    playlist = env.get("PLAYLIST", "").strip()
    playlist_name = env.get("PLAYLIST_NAME", "").strip()
    mode = env.get("MODE", "").strip()
    videos = env.get("VIDEOS", "").strip()
    jobs_raw = env.get("JOBS", "").strip()
    out = env.get("OUT", "").strip() or "output"

    interactive = _is_tty()

    if not interactive:
        # No TTY → behave like the old non-interactive target. Fail loudly
        # if PLAYLIST wasn't supplied, so we don't silently extract the
        # demo default.
        if not playlist:
            print("ERROR: no TTY and PLAYLIST is not set. "
                  "Pass PLAYLIST=... on the make command line, or run "
                  "make extract from a terminal.", file=sys.stderr)
            return 2
        mode = mode or "talking-head"
        jobs = int(jobs_raw or "1")
    else:
        print("== Interactive extract ==  (Enter to accept defaults)")
        if not playlist:
            playlist = _ask("Playlist URL", allow_empty=False)
        else:
            print(f"Playlist: {playlist}")
        if not playlist_name:
            playlist_name = _ask_playlist_name(playlist)
        if not mode:
            mode = _ask_choice("Mode", ["talking-head", "screen-heavy"], "talking-head")
        if not videos:
            videos = _ask_videos()
        if not jobs_raw:
            print()
            print("Parallel workers — captions-first is IO-bound (4-8 is fine);")
            print("keep at 1 for screen-heavy or --force-whisper on a small machine.")
            jobs = _ask_int("Jobs", 4)
        else:
            jobs = int(jobs_raw)

    cmd = [
        sys.executable, str(extractor), playlist,
        "--mode", mode,
        "--out", out,
        "--jobs", str(jobs),
        "--playlist-name", playlist_name or "playlist",
    ]
    if videos:
        cmd += ["--videos", videos]

    # Forward network / rate-limit knobs from the environment (set via
    # `make extract COOKIES_FROM=chrome SLEEP=2 ...`) so the interactive path
    # can get past YouTube 429 / bot-check limits too.
    for env_key, flag in (
        ("COOKIES_FROM", "--cookies-from-browser"),
        ("COOKIES", "--cookies"),
        ("PROXY", "--proxy"),
        ("SLEEP", "--sleep-requests"),
        ("SLEEP_MIN", "--sleep-interval"),
        ("SLEEP_MAX", "--max-sleep-interval"),
        ("RETRIES", "--retries"),
        ("LIMIT_RATE", "--limit-rate"),
    ):
        val = env.get(env_key, "").strip()
        if val:
            cmd += [flag, val]

    print()
    print("Running:", " ".join(shlex.quote(c) for c in cmd))
    print()
    return subprocess.call(cmd, cwd=str(repo_root))


if __name__ == "__main__":
    raise SystemExit(main())
