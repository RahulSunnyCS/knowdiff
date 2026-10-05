"""claim cards: profiles, card checks (quote + timestamp grounding, nulls),
clustering across creators, and the translation guard. No network, no model."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import _helpers  # noqa: F401  (sys.path setup)

import claim_cards as cc
import mcp_server as ms

COOKING = ("[00:30] Welcome back to the kitchen. [01:00] Always salt the pasta water until it tastes like "
           "the sea, because the pasta absorbs it while it cooks. [01:30] If the sauce splits, add a splash "
           "of pasta water and whisk. [02:00] Thanks for watching.")
TRADING = ("[00:30] Today we talk about Nifty. [01:00] I sell the at the money straddle at nine twenty "
           "every Thursday, and I keep a stop loss of thirty percent on each leg. [01:30] It works "
           "best when VIX is below fifteen. [02:00] Sometimes I just buy calls when the market feels strong.")
GENERIC = cc.load_profile("generic")
TRADE = cc.load_profile("trading")


def salt(**over) -> dict:
    base = {"claim": "Salt pasta water heavily", "tag": "rule", "topic": "Pasta water", "when": None,
            "do": "salt until it tastes like the sea", "expect": "seasoned pasta", "caveats": None,
            "quote": "Always salt the pasta water until it tastes like the sea", "ts": "01:00"}
    return {**base, **over}


def straddle(**over) -> dict:
    base = {"claim": "Sell the ATM straddle at 09:20 on expiry day", "tag": "short_straddle",
            "instrument": "NIFTY weekly options", "entry": "09:20 every Thursday",
            "exit": "30% stop loss on each leg", "sizing": None, "regime": "VIX below 15", "edge": None,
            "quote": "I sell the at the money straddle at nine twenty every Thursday", "ts": "01:00"}
    return {**base, **over}


class ProfileTests(unittest.TestCase):
    def test_profiles_load_and_render(self):
        self.assertEqual(cc.profile_names(), ["generic", "trading"])
        self.assertNotIn("translation", GENERIC)
        self.assertEqual(TRADE["translation"]["must_state"], ["entry", "exit", "sizing"])
        text = cc.instructions(GENERIC)
        self.assertIn("## Profile: generic", text)
        self.assertIn("- `caveats`", text)
        for bad in ("../generic", "nope", ""):
            with self.assertRaises(cc.CardError):
                cc.load_profile(bad)


class CheckTests(unittest.TestCase):
    def test_good_card_is_stamped_and_nulls_kept(self):
        out = cc.check_cards([salt(expect="  ")], COOKING, GENERIC)
        self.assertEqual(out[0]["id"], "c0")
        self.assertIsNone(out[0]["expect"])
        self.assertIsNone(out[0]["when"])
        self.assertEqual(cc.check_cards([], COOKING, GENERIC), [])

    def test_quote_matching_ignores_markers_case_and_punctuation(self):
        cc.check_cards([salt(quote="while it cooks. If the SAUCE splits, add a splash of pasta water")], COOKING, GENERIC)

    def test_every_problem_is_reported(self):
        bad = [salt(quote="Always salt the pasta water until it tastes like soup"),
               salt(ts="01:10"), salt(tag="short_straddle"), salt(claim=""),
               salt(quote="too short"), salt(do=30), "junk", salt(entry="09:20")]
        with self.assertRaises(cc.CardError) as cm:
            cc.check_cards(bad, COOKING, GENERIC)
        msg = str(cm.exception)
        for needle in ("card 0: quote is not in the transcript", "card 1: ts '01:10'", "card 2: tag must be",
                       "card 3: claim", "card 4: quote must be at least", "card 5: do must be text",
                       "card 6: must be an object", "card 7: fields not in the generic profile: entry"):
            self.assertIn(needle, msg)
        with self.assertRaises(cc.CardError):
            cc.check_cards({"cards": []}, COOKING, GENERIC)


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self._old = os.environ.get("KNOWDIFF_ROOT")
        os.environ["KNOWDIFF_ROOT"] = str(self.root)
        for playlist, uploader, text in (("chef-a", "Chef A", COOKING), ("chef-b", "Chef B", COOKING),
                                         ("trader", "Trader T", TRADING)):
            vdir = self.root / "output" / playlist / "video_01_talk"
            vdir.mkdir(parents=True)
            (vdir / "transcript.clean.txt").write_text(text)
            (vdir / "metadata.json").write_text(json.dumps(
                {"title": "Talk", "url": "https://youtu.be/x", "uploader": uploader}))

    def tearDown(self):
        if self._old is None:
            os.environ.pop("KNOWDIFF_ROOT", None)
        else:
            os.environ["KNOWDIFF_ROOT"] = self._old

    def test_generic_cards_cluster_across_creators(self):
        self.assertEqual([p["profile"] for p in ms.list_card_profiles()], ["generic", "trading"])
        self.assertIn("## Profile: generic", ms.get_card_instructions())
        self.assertIn("`instrument`", ms.get_card_instructions("trading"))

        with self.assertRaises(ms.ToolError):
            ms.save_claim_cards("chef-a", "video_01", [salt(ts="09:99")])
        self.assertFalse((self.root / "distilled" / "chef-a").exists())

        split = salt(claim="Fix a split sauce with pasta water", tag="technique", topic="sauce",
                     when="the sauce splits", do="add a splash of pasta water and whisk", expect=None,
                     quote="If the sauce splits, add a splash of pasta water and whisk", ts="01:30")
        self.assertEqual(ms.save_claim_cards("chef-a", "video_01", [salt(), split])["card_ids"], ["c0", "c1"])
        ms.save_claim_cards("chef-b", "video_01", [salt(topic="pasta  water!")])   # default profile, same subject

        groups = ms.get_claim_cards()
        self.assertEqual((groups[0]["profile"], groups[0]["subject"], groups[0]["tag"]), ("generic", "pasta water", "rule"))
        self.assertEqual(groups[0]["creators"], ["Chef A", "Chef B"])
        self.assertEqual(groups[0]["cards"][0]["link"], "https://youtu.be/x?t=60s")
        self.assertEqual(len(ms.get_claim_cards("chef-b")), 1)
        self.assertEqual(ms.get_claim_cards(profile="trading"), [])

        # The generic profile has nothing to translate into.
        with self.assertRaises(ms.ToolError) as cm:
            ms.save_card_translation("chef-a", "video_01", "c0", "generic", False, ["x"])
        self.assertIn("no translation step", str(cm.exception))

        md = (self.root / "distilled" / "claim_cards.md").read_text()
        self.assertIn("3 card(s) from 2 video(s), in 2 cluster(s)", md)
        self.assertIn("## [generic] pasta water — rule (2 card(s), 2 creator(s): Chef A, Chef B)", md)
        self.assertIn("- **when:** _not stated_", md)
        self.assertNotIn("testable form", md)

    def test_trading_profile_translation_guard(self):
        buy = straddle(claim="Buy calls on strength", tag="option_buy", instrument=None, entry=None,
                       exit=None, regime=None, quote="Sometimes I just buy calls when the market feels strong", ts="02:00")
        ms.save_claim_cards("trader", "video_01", [straddle(), buy], "trading")
        with self.assertRaises(ms.ToolError):   # trading fields are not generic fields
            ms.save_claim_cards("trader", "video_01", [straddle()])

        def translate(card_id, valid, review, **kw):
            return ms.save_card_translation("trader", "video_01", card_id, "trading", valid, review, **kw)

        # sizing was not stated: a translation must own up to whatever it used.
        with self.assertRaises(ms.ToolError) as cm:
            translate("c0", True, [], artifact="strategy: {}")
        self.assertIn("sizing", str(cm.exception))
        with self.assertRaises(ms.ToolError):   # valid but with errors
            translate("c0", True, ["sizing: not stated, used 1 lot"], artifact="strategy: {}", validation_errors=["bad"])
        with self.assertRaises(ms.ToolError):   # nothing translated, nothing explained
            translate("c1", False, [])
        with self.assertRaises(ms.ToolError):
            translate("c9", False, ["x"])

        ok = translate("c0", True, ["sizing: not stated, used 1 lot", "regime: VIX filter has no DSL feature"],
                       artifact="strategy: {}")
        self.assertTrue(ok["translation"]["valid"])
        self.assertEqual(ok["translation"]["artifact_kind"], "option-backtesting strategy YAML")
        translate("c1", False, ["entry is discretionary ('feels strong'); nothing to backtest"])

        md = (self.root / "distilled" / "claim_cards.md").read_text()
        self.assertIn("## [trading] nifty weekly options — short_straddle", md)
        self.assertIn("translated, validates; manual review: sizing: not stated, used 1 lot", md)
        self.assertIn("could not be translated; manual review: entry is discretionary", md)
        self.assertIn("- **sizing:** _not stated_", md)
        self.assertEqual(cc.main(["--distilled-root", str(self.root / "distilled")]), 0)


if __name__ == "__main__":
    unittest.main()
