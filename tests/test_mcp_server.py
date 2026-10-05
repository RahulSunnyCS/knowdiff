"""MCP server tool functions — exercised directly, without the `mcp`
package, a network, or yt-dlp."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import _helpers
from _helpers import spoken

import mcp_server as ms


class McpToolTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self._old = os.environ.get("KNOWDIFF_ROOT")
        os.environ["KNOWDIFF_ROOT"] = str(self.root)
        self.vdir = self.root / "output" / "demo" / "video_01_first-talk"
        _helpers.write_segments(self.vdir, [spoken(i * 10.0, f"sentence number {i} about cameras") for i in range(30)])
        (self.vdir / "metadata.json").write_text(json.dumps(
            {"title": "First Talk", "url": "https://youtu.be/aaa111", "duration": 300}))

    def tearDown(self):
        if self._old is None:
            os.environ.pop("KNOWDIFF_ROOT", None)
        else:
            os.environ["KNOWDIFF_ROOT"] = self._old

    def test_flow_preprocess_read_save(self):
        self.assertEqual(ms.list_playlists()[0]["preprocessed"], 0)
        rows = ms.preprocess("demo")
        self.assertTrue(rows[0]["timestamped"])
        self.assertGreater(rows[0]["markers"], 0)

        first = ms.get_transcript("demo", "video_01", max_chars=200)
        self.assertTrue(first["has_markers"])
        self.assertEqual(first["next_offset"], 200)
        rest = ms.get_transcript("demo", "video_01_first-talk", offset=200, max_chars=ms.MAX_TRANSCRIPT_CHARS)
        self.assertIsNone(rest["next_offset"])
        full = first["text"] + rest["text"]
        marker = ms.pre.MARKER_RE.findall(full)[0]

        good = {"t": "First Talk", "cc": [{"c": "x", "ev": "y", "ts": marker}], "h": [{"r": "r", "why": "w", "ts": ""}]}
        saved = ms.save_distilled("demo", "video_01", good)
        self.assertEqual(saved["saved"], "distilled/demo/video_01.json")
        self.assertTrue(saved["timestamps_checked"])
        self.assertEqual(ms.get_distilled("demo")["video_01"], good)

        video = ms.list_videos("demo")[0]
        self.assertEqual((video["video"], video["title"], video["distilled"]), ("video_01", "First Talk", True))

        ms.save_synthesis("demo", {"core": []})
        ms.save_skill("demo", "# one")
        ms.save_skill("demo", "# two")
        dist = self.root / "distilled" / "demo"
        self.assertEqual((dist / "SKILL.prev.md").read_text(), "# one")
        state = ms.list_playlists()[0]
        self.assertEqual((state["distilled"], state["has_synthesis"], state["has_skill"]), (1, True, True))

    def test_invented_timestamp_is_rejected_and_nothing_written(self):
        ms.preprocess("demo")
        with self.assertRaises(ms.ToolError) as cm:
            ms.save_distilled("demo", "video_01", {"cc": [{"c": "x", "ts": "00:47"}], "rp": [{"p": "p", "ts": "99:59"}]})
        self.assertIn("00:47", str(cm.exception))
        self.assertIn("99:59", str(cm.exception))
        self.assertFalse((self.root / "distilled" / "demo" / "video_01.json").exists())

    def test_unmarked_transcript_skips_the_timestamp_check(self):
        saved = ms.save_distilled("demo", "video_01", {"cc": [{"c": "x", "ts": "00:47"}]})
        self.assertFalse(saved["timestamps_checked"])

    def test_names_cannot_escape_the_data_root(self):
        for bad in ("../demo", "demo/..", "/etc", "", ".jobs", "a b"):
            with self.assertRaises(ms.ToolError, msg=bad):
                ms.list_videos(bad)
        with self.assertRaises(ms.ToolError):
            ms.get_transcript("demo", "../../etc/passwd")
        with self.assertRaises(ms.ToolError):
            ms.get_prompt("../README")
        self.assertTrue(ms.get_prompt("02_distill_video").startswith("#"))

    def test_only_https_youtube_urls_are_extracted(self):
        for bad in ("--exec=rm", "file:///etc/passwd", "http://youtube.com/watch?v=x",
                    "https://evil.example/watch?v=x", "https://youtube.com.evil.example/x", "/tmp/a.mp4"):
            with self.assertRaises(ms.ToolError, msg=bad):
                ms.start_extract(bad, "demo")
        with self.assertRaises(ms.ToolError):
            ms.start_extract("https://youtu.be/aaa111", "../x")
        self.assertEqual(ms.extract_status("fresh")["state"], "never started")

    def test_http_token_must_be_long_and_url_safe(self):
        for bad in ("", "short", "x" * 30 + "/"):
            with self.assertRaises(ms.ToolError):
                ms.http_path(bad)
        self.assertEqual(ms.http_path("a" * 32), "/" + "a" * 32 + "/mcp")


if __name__ == "__main__":
    unittest.main()
