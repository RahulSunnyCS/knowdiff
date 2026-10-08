"""knowledge-diff: store building, syllabus parsing, verdict checking,
watch ranges and the rendered watch-list. No network, no model."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import _helpers
from _helpers import spoken

import knowledge_diff as kd
import mcp_server as ms


def write_distilled(root: Path, playlist: str, video: str, data: dict) -> None:
    d = root / "distilled" / playlist
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{video}.json").write_text(json.dumps(data))


TARGET = {
    "t": "Talk three",
    "cc": [
        {"c": "A sensor converts photons into electrical charge", "ev": "e", "ts": "01:00"},
        {"c": "Aperture controls how much light reaches the sensor", "ev": "e", "ts": "01:30"},
        {"c": "Space cameras need radiation hardened electronics", "ev": "e", "ts": "08:00"},
    ],
    "h": [{"r": "Always calibrate the detector before a flight", "why": "w", "ts": "08:30"}],
    "rp": [{"p": "Start from the photon budget and work backwards", "ex": "x", "ts": ""}],
    "v": [{"term": "quantum efficiency", "m": "share of photons detected"}],
}


class PureTests(unittest.TestCase):
    def test_items_of_skips_untimed_kinds_and_blanks(self):
        items = kd.items_of({**TARGET, "cc": TARGET["cc"] + [{"c": " ", "ts": "02:00"}, "junk"]})
        self.assertEqual([i["id"] for i in items], ["cc0", "cc1", "cc2", "h0", "rp0"])
        self.assertEqual(items[3]["kind"], "heuristic")

    def test_parse_syllabus_checkboxes(self):
        text = "# Optics\n- [x] Aperture and light\n- [ ] Radiation hardening\n* plain bullet\n1. numbered\nfree line\n\n"
        self.assertEqual([k["text"] for k in kd.parse_syllabus(text)],
                         ["Aperture and light", "plain bullet", "numbered", "free line"])

    def test_similarity_and_lexical_verdicts(self):
        store = [{"text": "The aperture controls how much light reaches a sensor", "source": "p/video_01", "ts": ""}]
        items = kd.items_of(TARGET)
        verdicts = {v["id"]: v for v in kd.lexical_verdicts(items, store)}
        self.assertEqual(verdicts["cc1"]["status"], "known")
        self.assertIn("p/video_01", verdicts["cc1"]["covered_by"])
        self.assertEqual(verdicts["cc2"]["status"], "new")
        self.assertNotIn("covered_by", verdicts["cc2"])

    def test_check_verdicts_reports_every_problem(self):
        items = kd.items_of(TARGET)
        with self.assertRaises(kd.DiffError) as cm:
            kd.check_verdicts(items, [
                {"id": "cc0", "status": "new"}, {"id": "cc0", "status": "new"},
                {"id": "zz9", "status": "new"}, {"id": "cc1", "status": "seen"},
                {"id": "cc2", "status": "known"},
            ])
        msg = str(cm.exception)
        for needle in ("duplicate verdict for cc0", "unknown id 'zz9'", "cc1: status", "cc2: a known item", "h0", "rp0"):
            self.assertIn(needle, msg)

    def test_watch_ranges_merge_and_bounds(self):
        items = kd.items_of(TARGET)
        by_id = {i["id"]: {"id": i["id"], "status": "new"} for i in items}
        by_id["cc1"] = {"id": "cc1", "status": "known", "covered_by": "x"}
        ranges = kd.watch_ranges(items, by_id, duration=520.0)
        # cc0 runs 01:00 -> next item 01:30; cc2 08:00 -> 08:30 merges with h0, which is cut at the video's end.
        self.assertEqual([(r["start"], r["end"], r["items"]) for r in ranges],
                         [(60.0, 90.0, ["cc0"]), (480.0, 520.0, ["cc2", "h0"])])
        # With no later item and no duration, a passage is capped at MAX_WINDOW_SEC.
        solo = kd.watch_ranges(items[:1], {"cc0": {"status": "new"}}, None)
        self.assertEqual(solo[0]["end"] - solo[0]["start"], kd.MAX_WINDOW_SEC)

    def test_deep_link(self):
        self.assertEqual(kd.deep_link("https://www.youtube.com/watch?v=a", 61.9), "https://www.youtube.com/watch?v=a&t=61s")
        self.assertEqual(kd.deep_link("https://youtu.be/a", 5), "https://youtu.be/a?t=5s")
        self.assertIsNone(kd.deep_link(None, 5))


class StoreAndFilesTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self._old = os.environ.get("KNOWDIFF_ROOT")
        os.environ["KNOWDIFF_ROOT"] = str(self.root)
        self._cwd = os.getcwd()
        os.chdir(self.root)
        vdir = self.root / "output" / "cams" / "video_03_talk-three"
        _helpers.write_segments(vdir, [spoken(i * 10.0, f"line {i}") for i in range(60)])
        (vdir / "metadata.json").write_text(json.dumps(
            {"title": "Talk three", "url": "https://www.youtube.com/watch?v=abc", "duration": 600}))
        write_distilled(self.root, "cams", "video_03", TARGET)
        write_distilled(self.root, "cams", "video_01", {"cc": [{"c": "A sensor converts photons into electrical charge", "ts": "00:30"}]})
        write_distilled(self.root, "cams", "video_05", {"cc": [{"c": "Space cameras need radiation hardened electronics", "ts": "00:30"}]})
        write_distilled(self.root, "other", "video_09", {"h": [{"r": "Always calibrate the detector before a flight", "ts": "03:00"}]})
        (self.root / "distilled" / "cams" / "synthesis.json").write_text("{}")
        (self.root / "knowledge").mkdir()
        (self.root / "knowledge" / "syllabus.md").write_text("- [x] Aperture controls how much light reaches the sensor\n- [ ] photon budget\n")

    def tearDown(self):
        os.chdir(self._cwd)
        if self._old is None:
            os.environ.pop("KNOWDIFF_ROOT", None)
        else:
            os.environ["KNOWDIFF_ROOT"] = self._old

    def test_store_is_other_playlists_plus_earlier_videos(self):
        store = kd.load_store(self.root / "distilled", "cams", "video_03")
        self.assertEqual(sorted(k["source"] for k in store), ["cams/video_01", "other/video_09"])

    def test_cli_writes_diff_and_watchlist(self):
        self.assertEqual(kd.main(["--playlist", "cams", "--video", "video_03"]), 0)
        diff = json.loads((self.root / "distilled" / "cams" / "knowledge_diff" / "video_03.json").read_text())
        status = {i["id"]: i["status"] for i in diff["items"]}
        # video_01 (earlier), other/video_09 and the checked syllabus line are known;
        # video_05 (later) and the unchecked syllabus line are not.
        self.assertEqual(status, {"cc0": "known", "cc1": "known", "cc2": "new", "h0": "known", "rp0": "new"})
        self.assertEqual(diff["counts"], {"new": 2, "partial": 0, "known": 3})
        self.assertEqual(diff["ranges"][0]["link"], "https://www.youtube.com/watch?v=abc&t=480s")
        md = (self.root / "distilled" / "cams" / "watchlist.md").read_text()
        self.assertIn("**Watch 0.5 of 10 minutes**", md)
        self.assertIn("[08:00–08:30](https://www.youtube.com/watch?v=abc&t=480s)", md)
        self.assertIn("New, but without a timestamp:", md)
        self.assertIn("Already known (skipped)", md)

    def test_mcp_candidates_then_save(self):
        cand = ms.get_diff_candidates("cams", "video_03")
        self.assertEqual(cand["known_items_in_store"], 3)
        self.assertIn("[x] Aperture", cand["syllabus"])
        first = cand["items"][0]
        self.assertEqual(first["nearest_known"][0]["source"], "cams/video_01")
        verdicts = [{"id": i["id"], "status": "new"} for i in cand["items"]]
        with self.assertRaises(ms.ToolError):
            ms.save_knowledge_diff("cams", "video_03", verdicts[:-1])
        verdicts[0] = {"id": "cc0", "status": "known", "covered_by": "cams/video_01: same claim"}
        md = ms.save_knowledge_diff("cams", "video_03_talk-three", verdicts)
        self.assertIn("judge: claude", md)
        self.assertIn("cams/video_01: same claim", md)
        self.assertTrue((self.root / "distilled" / "cams" / "watchlist.md").exists())
        with self.assertRaises(ms.ToolError):
            ms.get_diff_candidates("cams", "video_99")


if __name__ == "__main__":
    unittest.main()
