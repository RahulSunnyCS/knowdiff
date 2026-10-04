"""Smaller units: quote-mining timestamps, pricing table coverage, scope
defaults, and the leak-free eval staging."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import _helpers  # noqa: F401

import pricing
import quote_mine as qm
import run_eval
import scope


class QuoteMineTests(unittest.TestCase):
    def test_nearest_marker_and_strip(self):
        sentences = ["[00:00] Intro words.", "Still intro.", "[00:30] Index funds are boring.",
                     "They work though.", "[01:00] Next."]
        self.assertEqual(qm.nearest_marker_before(sentences, 3), "00:30")
        self.assertEqual(qm.nearest_marker_before(sentences, 1), "00:00")
        self.assertEqual(qm.nearest_marker_before(["no markers here."], 0), "")
        self.assertEqual(qm.window(sentences, 2, before=1, after=1),
                         "Still intro. Index funds are boring. They work though.")

    def test_hits_still_match_through_markers(self):
        sentences = ["[00:30] I like index funds."]
        hits = qm.find_hits(sentences, "index funds", [])
        self.assertEqual(hits[0][2], "exact")


class PricingAndScopeTests(unittest.TestCase):
    def test_default_models_have_explicit_prices(self):
        for phase, model in scope.DEFAULT_MODELS.items():
            self.assertIn(model, pricing.PRICES, f"{phase} default {model} missing from PRICES")

    def test_cost_math(self):
        cost = pricing.cost_usd("claude-haiku-4-5", input_tokens=1_000_000, output_tokens=0)
        self.assertAlmostEqual(cost, 1.00)
        cost = pricing.cost_usd("claude-sonnet-5-5", input_tokens=0, output_tokens=100_000,
                                cache_read_tokens=1_000_000)
        self.assertAlmostEqual(cost, 1.00 + 0.20)

    def test_scope_round_trip_keeps_models(self):
        tmp = Path(tempfile.mkdtemp())
        s = scope.Scope(intent="topical-report", question="q?",
                        models={"phase2": "claude-haiku-4-5"})
        scope.save(tmp, "pl", s)
        loaded = scope.load(tmp, "pl")
        self.assertEqual(loaded.model_for("phase2"), "claude-haiku-4-5")
        self.assertEqual(loaded.model_for("phase3"), scope.DEFAULT_MODELS["phase3"])


class EvalStagingTests(unittest.TestCase):
    def test_holdout_is_excluded_and_scope_copied(self):
        tmp = Path(tempfile.mkdtemp())
        distilled = tmp / "distilled" / "pl"
        distilled.mkdir(parents=True)
        for i in range(1, 5):
            (distilled / f"video_{i:02d}.json").write_text(json.dumps({"t": f"v{i}"}))
        (distilled / "synthesis.json").write_text("{}")   # must NOT be copied
        (distilled / "scope.json").write_text(json.dumps({"intent": "method-distillation"}))

        scratch_root = tmp / "scratch"
        out = run_eval.stage_holdout_corpus(distilled, scratch_root, "pl", "video_03")
        names = sorted(p.name for p in out.iterdir())
        self.assertEqual(names, ["scope.json", "video_01.json", "video_02.json", "video_04.json"])

    def test_video_id_of(self):
        self.assertEqual(run_eval.video_id_of(Path("output/pl/video_07_some-slug")), "video_07")


if __name__ == "__main__":
    unittest.main()
