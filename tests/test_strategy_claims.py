"""strategy-claims: card checks (quote + timestamp grounding, nulls),
clustering, and the translation guard. No network, no model."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import mcp_server as ms
import strategy_claims as sc

TRANSCRIPT = ("[00:30] Today we talk about Nifty. [01:00] I sell the at the money straddle at nine twenty "
              "every Thursday, and I keep a stop loss of thirty percent on each leg. [01:30] It works "
              "best when VIX is below fifteen. [02:00] Sometimes I just buy calls when the market feels strong.")


def card(**over) -> dict:
    base = {"hypothesis": "Sell the ATM straddle at 09:20 on expiry day", "structure": "short_straddle",
            "instrument": "NIFTY weekly options", "entry": "09:20 every Thursday",
            "exit": "30% stop loss on each leg", "sizing": None, "regime": "VIX below 15", "edge": None,
            "quote": "I sell the at the money straddle at nine twenty every Thursday", "ts": "01:00"}
    return {**base, **over}


class CheckTests(unittest.TestCase):
    def test_good_card_is_stamped_and_nulls_kept(self):
        out = sc.check_cards([card(sizing="  ")], TRANSCRIPT)
        self.assertEqual(out[0]["id"], "c0")
        self.assertIsNone(out[0]["sizing"])
        self.assertIsNone(out[0]["edge"])
        self.assertEqual(sc.check_cards([], TRANSCRIPT), [])

    def test_quote_matching_ignores_markers_case_and_punctuation(self):
        sc.check_cards([card(quote="every Thursday, and I keep a STOP LOSS of thirty percent on each leg. It works best")], TRANSCRIPT)

    def test_every_problem_is_reported(self):
        bad = [card(quote="I sell the straddle at nine fifteen every single day"),
               card(ts="01:10"), card(structure="straddle"), card(hypothesis=""),
               card(quote="too short"), card(exit=30), "junk"]
        with self.assertRaises(sc.CardError) as cm:
            sc.check_cards(bad, TRANSCRIPT)
        msg = str(cm.exception)
        for needle in ("card 0: quote is not in the transcript", "card 1: ts '01:10'", "card 2: structure",
                       "card 3: hypothesis", "card 4: quote must be at least", "card 5: exit must be text",
                       "card 6: must be an object"):
            self.assertIn(needle, msg)
        with self.assertRaises(sc.CardError):
            sc.check_cards({"cards": []}, TRANSCRIPT)


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self._old = os.environ.get("KNOWDIFF_ROOT")
        os.environ["KNOWDIFF_ROOT"] = str(self.root)
        for playlist, uploader in (("alpha", "Trader A"), ("beta", "Trader B")):
            vdir = self.root / "output" / playlist / "video_01_talk"
            vdir.mkdir(parents=True)
            (vdir / "transcript.clean.txt").write_text(TRANSCRIPT)
            (vdir / "metadata.json").write_text(json.dumps(
                {"title": "Talk", "url": "https://youtu.be/x", "uploader": uploader}))

    def tearDown(self):
        if self._old is None:
            os.environ.pop("KNOWDIFF_ROOT", None)
        else:
            os.environ["KNOWDIFF_ROOT"] = self._old

    def test_save_cluster_translate(self):
        with self.assertRaises(ms.ToolError):
            ms.save_strategy_cards("alpha", "video_01", [card(ts="09:99")])
        self.assertFalse((self.root / "distilled" / "alpha").exists())

        buy = card(hypothesis="Buy calls on strength", structure="option_buy", instrument=None, entry=None,
                   exit=None, regime=None, quote="Sometimes I just buy calls when the market feels strong", ts="02:00")
        self.assertEqual(ms.save_strategy_cards("alpha", "video_01", [card(), buy])["card_ids"], ["c0", "c1"])
        ms.save_strategy_cards("beta", "video_01", [card(instrument="Nifty  weekly options")])

        groups = ms.get_strategy_cards()
        self.assertEqual((groups[0]["instrument"], groups[0]["structure"]), ("NIFTYWEEKLYOPTIONS", "short_straddle"))
        self.assertEqual(groups[0]["creators"], ["Trader A", "Trader B"])
        self.assertEqual((groups[1]["instrument"], len(groups[1]["cards"])), ("UNSPECIFIED", 1))
        self.assertEqual(len(ms.get_strategy_cards("beta")), 1)
        self.assertEqual(groups[0]["cards"][0]["link"], "https://youtu.be/x?t=60s")

        # sizing was not stated: a YAML translation must own up to whatever it used.
        with self.assertRaises(ms.ToolError) as cm:
            ms.save_strategy_translation("alpha", "video_01", "c0", True, [], strategy_yaml="strategy: {}")
        self.assertIn("sizing", str(cm.exception))
        with self.assertRaises(ms.ToolError):   # valid but with errors
            ms.save_strategy_translation("alpha", "video_01", "c0", True, ["sizing: not stated, used 1 lot"],
                                         strategy_yaml="strategy: {}", validation_errors=["bad"])
        with self.assertRaises(ms.ToolError):   # nothing translated, nothing explained
            ms.save_strategy_translation("alpha", "video_01", "c1", False, [])
        with self.assertRaises(ms.ToolError):
            ms.save_strategy_translation("alpha", "video_01", "c9", False, ["x"])

        ok = ms.save_strategy_translation("alpha", "video_01", "c0", True,
                                          ["sizing: not stated, used 1 lot", "regime: VIX filter has no DSL feature"],
                                          strategy_yaml="strategy: {}")
        self.assertTrue(ok["translation"]["valid"])
        ms.save_strategy_translation("alpha", "video_01", "c1", False,
                                     ["entry is discretionary ('feels strong'); nothing to backtest"])

        md = (self.root / "distilled" / "strategy_cards.md").read_text()
        self.assertIn("3 card(s) from 2 video(s), in 2 cluster(s)", md)
        self.assertIn("2 creator(s): Trader A, Trader B", md)
        self.assertIn("translated, validates; manual review: sizing: not stated, used 1 lot", md)
        self.assertIn("could not be translated; manual review: entry is discretionary", md)
        self.assertIn("not translated yet", md)
        self.assertIn("- **sizing:** _not stated_", md)
        self.assertEqual(sc.main(["--distilled-root", str(self.root / "distilled")]), 0)


if __name__ == "__main__":
    unittest.main()
