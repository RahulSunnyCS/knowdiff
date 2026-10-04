"""Extractor tests: json3 parsing, caption selection, block detection,
idempotent re-runs and stop-on-block — all against a fake yt-dlp on PATH.
No network, no real yt-dlp."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

import _helpers  # noqa: F401  (sys.path setup)

import extract_playlist as ex


JSON3_FIXTURE = {
    "wireMagic": "pb3",
    "events": [
        {"tStartMs": 0, "dDurationMs": 2000, "id": 1, "wpWinPosId": 1},        # window record, no segs
        {"tStartMs": 120, "dDurationMs": 2500, "segs": [{"utf8": "Hello "}, {"utf8": "world"}]},
        {"tStartMs": 2620, "dDurationMs": 100, "segs": [{"utf8": "\n"}]},      # newline-only event
        {"tStartMs": 3000, "dDurationMs": 1800, "segs": [{"utf8": "[Music]"}]},  # noise
        {"tStartMs": 5000, "dDurationMs": 3000, "segs": [{"utf8": "second"}, {"utf8": " line\n"}]},
    ],
}


class Json3Tests(unittest.TestCase):
    def test_parse_json3_keeps_speech_only(self):
        segs = ex.parse_json3(json.dumps(JSON3_FIXTURE))
        self.assertEqual([s["text"] for s in segs], ["Hello world", "second line"])
        self.assertAlmostEqual(segs[0]["start"], 0.12)
        self.assertAlmostEqual(segs[0]["end"], 2.62)
        self.assertAlmostEqual(segs[1]["start"], 5.0)

    def test_pick_caption_file_prefers_exact_then_regional(self):
        files = [Path("source.en-orig.json3"), Path("source.en-US.json3"), Path("source.de.json3")]
        self.assertEqual(ex.pick_caption_file(files).name, "source.en-US.json3")
        files.append(Path("source.en.json3"))
        self.assertEqual(ex.pick_caption_file(files).name, "source.en.json3")
        self.assertIsNone(ex.pick_caption_file([]))
        self.assertEqual(ex.pick_caption_file([Path("source.de.json3")]).name, "source.de.json3")

    def test_looks_blocked(self):
        self.assertTrue(ex.looks_blocked("ERROR: Sign in to confirm you're not a bot"))
        self.assertTrue(ex.looks_blocked("HTTP Error 429: Too Many Requests"))
        self.assertFalse(ex.looks_blocked("ERROR: Video unavailable"))
        self.assertFalse(ex.looks_blocked(""))

    def test_ytdlp_base_flags(self):
        y = ex.YtDlp(sleep_requests=1.5, sleep_subtitles=0, cookies_from_browser="chrome",
                     extractor_args="youtube:player_client=tv", extra_args=["--proxy", "x"])
        base = y.base()
        self.assertIn("--sleep-requests", base)
        self.assertNotIn("--sleep-subtitles", base)
        self.assertEqual(base[base.index("--cookies-from-browser") + 1], "chrome")
        self.assertEqual(base[base.index("--extractor-args") + 1], "youtube:player_client=tv")
        self.assertEqual(base[-2:], ["--proxy", "x"])

    def test_resolve_playlist_name(self):
        self.assertEqual(ex.resolve_playlist_name("https://x", False, "My Creator!"), "my-creator")
        self.assertEqual(ex.resolve_playlist_name("https://x", False, None), "playlist")
        self.assertEqual(ex.resolve_playlist_name("/tmp/Talk One.mp4", True, None), "talk-one")


FAKE_YTDLP = r'''#!/usr/bin/env python3
"""Fake yt-dlp: enumerates two videos, writes info.json + json3 per video,
logs every invocation, and optionally blocks on a given video id."""
import json, os, sys
from pathlib import Path

LOG = Path(os.environ["FAKE_YTDLP_LOG"])
BLOCK_ID = os.environ.get("FAKE_YTDLP_BLOCK_ID", "")
args = sys.argv[1:]
with LOG.open("a") as f:
    f.write(json.dumps(args) + "\n")

if "--flat-playlist" in args:
    for vid, title in (("aaa111", "First Talk"), ("bbb222", "Second Talk")):
        print(json.dumps({"id": vid, "title": title, "url": f"https://youtu.be/{vid}"}))
    sys.exit(0)

url = args[-1]
vid = url.rsplit("/", 1)[-1]
if vid == BLOCK_ID:
    sys.stderr.write("ERROR: [youtube] %s: Sign in to confirm you're not a bot.\n" % vid)
    sys.exit(1)

tpl = args[args.index("-o") + 1]
base = tpl.replace("%(ext)s", "")
info = {"id": vid, "title": vid.upper(), "description": "00:00 Intro\n00:30 Body\n",
        "webpage_url": url, "duration": 90, "subtitles": {"en": []}, "automatic_captions": {}}
Path(base + "info.json").write_text(json.dumps(info))
words = " ".join(["word%d" % i for i in range(150)])
events = [{"tStartMs": i * 1000, "dDurationMs": 900, "segs": [{"utf8": "w%d %s" % (i, words[:40])}]}
          for i in range(0, 80)]
Path(base + "en.json3").write_text(json.dumps({"events": events}))
'''


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        bindir = self.tmp / "bin"
        bindir.mkdir()
        fake = bindir / "yt-dlp"
        fake.write_text(FAKE_YTDLP)
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.log = self.tmp / "calls.log"
        self._old_env = dict(os.environ)
        os.environ["PATH"] = f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}"
        os.environ["FAKE_YTDLP_LOG"] = str(self.log)
        os.environ.pop("FAKE_YTDLP_BLOCK_ID", None)
        self.out = self.tmp / "out"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._old_env)

    def _calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines() if line.strip()]

    def _run(self, *extra: str) -> int:
        return ex.main([
            "https://www.youtube.com/playlist?list=abc", "--playlist-name", "demo",
            "--out", str(self.out), "--pause-sec", "0",
            "--sleep-requests", "0", "--sleep-subtitles", "0", *extra,
        ])

    def test_single_call_per_video_and_idempotent_rerun(self):
        self.assertEqual(self._run(), 0)
        root = self.out / "demo"
        dirs = sorted(d.name for d in root.glob("video_*"))
        self.assertEqual(dirs, ["video_01_first-talk", "video_02_second-talk"])
        v1 = root / "video_01_first-talk"
        for name in ("transcript.txt", "transcript.timestamped.json", "source.info.json",
                     "source.en.json3", "description.txt", "metadata.json"):
            self.assertTrue((v1 / name).exists(), name)
        meta = json.loads((v1 / "metadata.json").read_text())
        self.assertEqual(meta["url"], "https://youtu.be/aaa111")
        side = json.loads((v1 / "transcript.timestamped.json").read_text())
        self.assertEqual(side["source"], "youtube_captions")
        self.assertEqual(len(side["segments"]), 80)
        self.assertTrue((root / "00_INDEX.md").exists())

        calls = self._calls()
        # 1 enumeration + exactly 1 call per video, nothing else.
        self.assertEqual(len(calls), 3)
        per_video = [c for c in calls if "--flat-playlist" not in c]
        for c in per_video:
            self.assertIn("--skip-download", c)
            self.assertIn("--write-subs", c)
            self.assertIn("--write-auto-subs", c)
            self.assertIn("json3", c)
            self.assertIn("--no-playlist", c)

        # Second run: enumeration only. Cached videos never touch yt-dlp again.
        self.assertEqual(self._run(), 0)
        self.assertEqual(len(self._calls()), 4)

        # --force refetches.
        self.assertEqual(self._run("--force"), 0)
        self.assertEqual(len(self._calls()), 4 + 3)

    def test_stops_on_block_and_keeps_progress(self):
        os.environ["FAKE_YTDLP_BLOCK_ID"] = "bbb222"
        self.assertEqual(self._run(), ex.EXIT_BLOCKED)
        root = self.out / "demo"
        self.assertTrue(ex.transcript_is_cached(root / "video_01_first-talk"))
        self.assertFalse(ex.transcript_is_cached(root / "video_02_second-talk"))
        # Resuming after the block fetches only the missing video.
        os.environ.pop("FAKE_YTDLP_BLOCK_ID")
        before = len(self._calls())
        self.assertEqual(self._run(), 0)
        self.assertEqual(len(self._calls()) - before, 2)  # enumerate + video 2
        self.assertTrue(ex.transcript_is_cached(root / "video_02_second-talk"))


if __name__ == "__main__":
    unittest.main()
