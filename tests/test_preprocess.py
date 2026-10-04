"""Preprocessor tests: inline [MM:SS] markers, time-based trimming and
chapter splits, and the flat-text fallback."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from _helpers import spoken, write_segments

import preprocess_transcript as pp


class MarkerTests(unittest.TestCase):
    def test_format_and_parse_ts(self):
        self.assertEqual(pp.format_ts(0), "00:00")
        self.assertEqual(pp.format_ts(65.9), "01:05")
        self.assertEqual(pp.format_ts(3725), "1:02:05")
        self.assertEqual(pp.parse_ts("01:05"), 65)
        self.assertEqual(pp.parse_ts("1:02:05"), 3725)
        self.assertIsNone(pp.parse_ts("nope"))

    def test_render_with_markers_every_interval(self):
        segs = [spoken(0, "a"), spoken(10, "b"), spoken(31, "c"), spoken(45, "d"), spoken(61, "e")]
        text, n = pp.render_with_markers(segs, 30)
        self.assertEqual(text, "[00:00] a b [00:31] c d [01:01] e")
        self.assertEqual(n, 3)
        self.assertEqual(pp.MARKER_RE.findall(text), ["00:00", "00:31", "01:01"])

    def test_render_with_markers_disabled(self):
        text, n = pp.render_with_markers([spoken(0, "a"), spoken(40, "b")], 0)
        self.assertEqual((text, n), ("a b", 0))

    def test_markers_survive_filler_strip_and_repeat_collapse(self):
        text = "[00:00] um so this is, you know, the point. [00:30] The point. [01:00] Next idea."
        cleaned, _ = pp.strip_fillers(text)
        self.assertIn("[00:00]", cleaned)
        self.assertNotIn("you know", cleaned)
        collapsed, _ = pp.collapse_repeats(cleaned)
        self.assertIn("[01:00]", collapsed)


class SegmentTrimAndChapterTests(unittest.TestCase):
    def test_trim_intro_outro_segments(self):
        segs = [spoken(0, "intro"), spoken(20, "still intro"), spoken(40, "body"),
                spoken(80, "more body"), spoken(100, "outro"), spoken(110, "bye")]
        kept, cuts = pp.trim_intro_outro_segments(segs, intro_sec=30, outro_sec=20)
        self.assertEqual([s["text"] for s in kept], ["body", "more body"])
        self.assertEqual({c.reason for c in cuts}, {"intro", "outro"})
        intro = next(c for c in cuts if c.reason == "intro")
        self.assertEqual((intro.start, intro.end), (0.0, 30))

    def test_trim_never_empties_short_clips(self):
        segs = [spoken(0, "only line")]
        kept, cuts = pp.trim_intro_outro_segments(segs, 30, 30)
        self.assertEqual(kept, segs)
        self.assertEqual(cuts, [])

    def test_split_segments_by_chapters_is_exact(self):
        segs = [spoken(0, "a"), spoken(29, "b"), spoken(30, "c"), spoken(90, "d")]
        chapters = [(0, "Intro"), (30, "Body"), (90, "End")]
        out = pp.split_segments_by_chapters(segs, chapters)
        self.assertEqual([(t, [s["text"] for s in c]) for t, c in out],
                         [("Intro", ["a", "b"]), ("Body", ["c"]), ("End", ["d"])])


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.vdir = self.tmp / "video_01_demo"

    def test_timestamped_path_writes_markers_and_chapters(self):
        segs = [spoken(i * 5, f"sentence number {i} about the topic.") for i in range(0, 30)]
        write_segments(self.vdir, segs)
        (self.vdir / "description.txt").write_text("Chapters:\n00:00 Intro\n01:00 Deep dive\n")
        report = pp.preprocess_video_dir(self.vdir, intro_sec=10, outro_sec=10)
        clean = (self.vdir / "transcript.clean.txt").read_text()
        self.assertTrue(report.timestamped)
        self.assertGreaterEqual(report.marker_count, 4)
        self.assertIn("[00:10]", clean)             # first kept segment after the 10s intro trim
        self.assertNotIn("sentence number 0 ", clean)  # intro trimmed by time, not by chars
        self.assertEqual(report.chapters_detected, 2)
        chap = sorted((self.vdir / "chapters").glob("*.txt"))
        self.assertEqual(len(chap), 2)
        self.assertIn("[01:00]", chap[1].read_text())
        data = json.loads((self.vdir / "preprocess.json").read_text())
        self.assertTrue(data["timestamped"])
        self.assertEqual(data["marker_count"], report.marker_count)

    def test_flat_fallback_without_sidecar(self):
        self.vdir.mkdir(parents=True)
        (self.vdir / "transcript.txt").write_text("x " * 2000)
        report = pp.preprocess_video_dir(self.vdir, intro_sec=10, outro_sec=10)
        self.assertFalse(report.timestamped)
        self.assertEqual(report.marker_count, 0)
        self.assertNotIn("[00:", (self.vdir / "transcript.clean.txt").read_text())
        self.assertEqual({c.reason for c in report.cuts}, {"intro", "outro"})


if __name__ == "__main__":
    unittest.main()
