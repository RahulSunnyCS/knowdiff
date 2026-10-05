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
        self.assertEqual(
            ex.pick_caption_file([Path("source.en-qlPKC2UN_YU.json3")]).name,
            "source.en-qlPKC2UN_YU.json3")
        self.assertEqual(ex.pick_caption_file([Path("source.de.json3")]).name, "source.de.json3")

    def test_choose_caption_tracks(self):
        info = {"subtitles": {"en-qlPKC2UN_YU": [], "live_chat": [], "de": []},
                "automatic_captions": {"en-en-qlPKC2UN_YU": [], "en-orig": [], "en": [], "fr": []}}
        self.assertEqual(ex.choose_caption_tracks(info),
                         ["en-qlPKC2UN_YU", "en", "en-orig", "en-en-qlPKC2UN_YU"])
        self.assertEqual(ex.choose_caption_tracks({"automatic_captions": {"en": []}}), ["en"])
        self.assertEqual(ex.choose_caption_tracks({"subtitles": {"de": []}}), [])
        self.assertEqual(ex.choose_caption_tracks({"subtitles": {"de": []}}, "de"), ["de"])

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

    def test_parse_video_selection(self):
        self.assertEqual(ex.parse_video_selection("10-12"), [10, 11, 12])
        self.assertEqual(ex.parse_video_selection("1, 3,5-6,3"), [1, 3, 5, 6])
        for bad in ("", "0", "5-2", "a", "1-"):
            with self.assertRaises(ValueError, msg=bad):
                ex.parse_video_selection(bad)

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

tpl = args[args.index("-o") + 1]
base = tpl.replace("%(ext)s", "")

if "--load-info-json" not in args:          # call 1: metadata only
    url = args[-1]
    vid = url.rsplit("/", 1)[-1]
    if vid == BLOCK_ID:
        sys.stderr.write("ERROR: [youtube] %s: Sign in to confirm you're not a bot.\n" % vid)
        sys.exit(1)
    info = {"id": vid, "title": vid.upper(), "description": "00:00 Intro\n00:30 Body\n",
            "webpage_url": url, "duration": 90,
            "subtitles": {"en-qlPKC2UN_YU": []},
            "automatic_captions": {"en": [], "en-orig": [], "de": []}}
    Path(base + "info.json").write_text(json.dumps(info))
    sys.exit(0)

# call 2: exactly one caption track, from the saved metadata
info = json.loads(Path(args[args.index("--load-info-json") + 1]).read_text())
lang = args[args.index("--sub-langs") + 1].strip("^$").replace("\\", "")
if info["id"] == os.environ.get("FAKE_YTDLP_SUB_BLOCK_ID", ""):
    sys.stderr.write("ERROR: Unable to download video subtitles for %r: "
                     "HTTP Error 429: Too Many Requests\n" % lang)
    sys.stdout.write("[download] Sleeping 2.00 seconds ...\n" * 40)
    sys.exit(1)
words = " ".join(["word%d" % i for i in range(150)])
events = [{"tStartMs": i * 1000, "dDurationMs": 900, "segs": [{"utf8": "w%d %s" % (i, words[:40])}]}
          for i in range(0, 80)]
Path(base + lang + ".json3").write_text(json.dumps({"events": events}))
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
        os.environ.pop("FAKE_YTDLP_SUB_BLOCK_ID", None)
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

    def test_one_track_per_video_and_idempotent_rerun(self):
        self.assertEqual(self._run(), 0)
        root = self.out / "demo"
        dirs = sorted(d.name for d in root.glob("video_*"))
        self.assertEqual(dirs, ["video_01_first-talk", "video_02_second-talk"])
        v1 = root / "video_01_first-talk"
        for name in ("transcript.txt", "transcript.timestamped.json", "source.info.json",
                     "source.en-qlPKC2UN_YU.json3", "description.txt", "metadata.json"):
            self.assertTrue((v1 / name).exists(), name)
        meta = json.loads((v1 / "metadata.json").read_text())
        self.assertEqual(meta["url"], "https://youtu.be/aaa111")
        side = json.loads((v1 / "transcript.timestamped.json").read_text())
        self.assertEqual(side["source"], "youtube_captions")
        self.assertEqual(len(side["segments"]), 80)
        self.assertTrue((root / "00_INDEX.md").exists())

        calls = self._calls()
        # 1 enumeration + per video: one metadata call, one caption call.
        self.assertEqual(len(calls), 5)
        per_video = [c for c in calls if "--flat-playlist" not in c]
        for c in per_video:
            self.assertIn("--skip-download", c)
            self.assertIn("--no-playlist", c)
        meta_calls = [c for c in per_video if "--write-info-json" in c]
        sub_calls = [c for c in per_video if "--load-info-json" in c]
        self.assertEqual((len(meta_calls), len(sub_calls)), (2, 2))
        for c in meta_calls:
            self.assertNotIn("--write-subs", c)
        for c in sub_calls:
            # Exactly one track, the manual one, never a multi-track pattern.
            self.assertEqual(c[c.index("--sub-langs") + 1], r"^en\-qlPKC2UN_YU$")
            self.assertIn("json3", c)
        self.assertEqual(sorted(p.name for p in v1.glob("source.*.json3")),
                         ["source.en-qlPKC2UN_YU.json3"])

        # Second run: enumeration only. Cached videos never touch yt-dlp again.
        self.assertEqual(self._run(), 0)
        self.assertEqual(len(self._calls()), 6)

        # --force refetches.
        self.assertEqual(self._run("--force"), 0)
        self.assertEqual(len(self._calls()), 6 + 5)

    def test_videos_range_keeps_playlist_numbers(self):
        self.assertEqual(self._run("--videos", "2"), 0)
        root = self.out / "demo"
        self.assertEqual(sorted(d.name for d in root.glob("video_*")), ["video_02_second-talk"])
        self.assertIn(" 2. Second Talk", (root / "00_INDEX.md").read_text())
        # A later full run finds video 2 cached and fetches only video 1.
        before = len(self._calls())
        self.assertEqual(self._run(), 0)
        self.assertEqual(len(self._calls()) - before, 3)  # enumerate + video 1 (metadata, captions)
        self.assertEqual(self._run("--videos", "0-1"), 2)
        self.assertEqual(self._run("--jobs", "0"), 2)

    def test_parallel_jobs_fetch_everything(self):
        self.assertEqual(self._run("--jobs", "2"), 0)
        root = self.out / "demo"
        self.assertTrue(ex.transcript_is_cached(root / "video_01_first-talk"))
        self.assertTrue(ex.transcript_is_cached(root / "video_02_second-talk"))
        self.assertEqual(len(self._calls()), 5)

    def test_parallel_jobs_stop_on_block(self):
        os.environ["FAKE_YTDLP_BLOCK_ID"] = "aaa111"
        self.assertEqual(self._run("--jobs", "2"), ex.EXIT_BLOCKED)

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
        self.assertEqual(len(self._calls()) - before, 3)  # enumerate + video 2 (metadata, captions)
        self.assertTrue(ex.transcript_is_cached(root / "video_02_second-talk"))

    def test_caption_429_stops_run_and_keeps_metadata(self):
        os.environ["FAKE_YTDLP_SUB_BLOCK_ID"] = "bbb222"
        self.assertEqual(self._run(), ex.EXIT_BLOCKED)
        root = self.out / "demo"
        v2 = root / "video_02_second-talk"
        self.assertTrue(ex.transcript_is_cached(root / "video_01_first-talk"))
        self.assertTrue((v2 / "source.info.json").exists())
        self.assertFalse(ex.transcript_is_cached(v2))
        # Resume reuses the saved metadata: enumerate + the caption call only.
        os.environ.pop("FAKE_YTDLP_SUB_BLOCK_ID")
        before = len(self._calls())
        self.assertEqual(self._run(), 0)
        self.assertEqual(len(self._calls()) - before, 2)
        self.assertTrue(ex.transcript_is_cached(v2))

    def test_blocked_error_carries_the_error_line(self):
        y = ex.YtDlp(sleep_requests=0, sleep_subtitles=0)
        info = self.tmp / "i.json"
        info.write_text(json.dumps({"id": "zzz"}))
        os.environ["FAKE_YTDLP_SUB_BLOCK_ID"] = "zzz"
        with self.assertRaises(ex.BlockedError) as cm:
            y.run(["--load-info-json", str(info), "--sub-langs", "^en$", "-o", str(self.tmp / "s.%(ext)s")])
        self.assertIn("429", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
