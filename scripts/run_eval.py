"""
Eval harness: hold-one-out scoring, leak-free.

Workflow:
  1. Pick a held-out video (default: the last one numerically).
  2. Copy the OTHER N-1 per-video JSONs (and scope.json) into a scratch
     distilled root, then run Phase 3 + Phase 4 there so the Skill under
     test has never seen the held-out video.
  3. Send that SKILL.md + the held-out transcript to Claude with
     prompts/05_eval_rubric.md.
  4. Write distilled/<playlist>/score.json (with `leak_free: true`).

`--skill-path` evaluates a Skill you already have instead. That Skill was
almost certainly built from all N videos, including the held-out one, so
the score is optimistic; score.json then records `leak_free: false`.

Limitations: this is a meta-eval (Claude scoring Claude), which has known
biases. Use it as a relative signal across runs, not an absolute quality
score. Pair with manual inspection.

Usage:
    python scripts/run_eval.py --playlist <name> [--holdout video_07] [--mode Teacher]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import scope as scope_module
from accounting import CostAccumulator
from claude_client import ClaudeClient


SCRIPTS_DIR = Path(__file__).resolve().parent
P5_PROMPT = SCRIPTS_DIR.parent / "prompts" / "05_eval_rubric.md"


def video_id_of(video_dir: Path) -> str:
    """output/<pl>/video_07_some-slug -> video_07"""
    return "_".join(video_dir.name.split("_")[:2])


def stage_holdout_corpus(distilled_dir: Path, scratch_root: Path, playlist: str, holdout_id: str) -> Path:
    """Copy every per-video JSON except the held-out one (plus scope.json)
    into scratch_root/<playlist>/. Returns that directory."""
    scratch_dir = scratch_root / playlist
    if scratch_dir.exists():
        shutil.rmtree(scratch_dir)
    scratch_dir.mkdir(parents=True)
    copied = 0
    for jf in sorted(distilled_dir.glob("video_*.json")):
        if jf.stem == holdout_id:
            continue
        shutil.copy2(jf, scratch_dir / jf.name)
        copied += 1
    scope_file = distilled_dir / "scope.json"
    if scope_file.exists():
        shutil.copy2(scope_file, scratch_dir / "scope.json")
    if copied < 2:
        sys.exit(
            f"Leak-free eval needs at least 2 per-video JSONs besides the held-out "
            f"one; found {copied} under {distilled_dir}. Run Phase 2 first."
        )
    return scratch_dir


def rebuild_skill(scratch_root: Path, playlist: str, mode: str, model3: str | None, model4: str | None) -> Path:
    """Run Phase 3 + Phase 4 inside the scratch root. Returns the SKILL.md path."""
    base = [sys.executable]
    cmd3 = base + [str(SCRIPTS_DIR / "run_phase3.py"), "--playlist", playlist,
                   "--distilled-root", str(scratch_root), "--force"]
    if model3:
        cmd3 += ["--model", model3]
    print("  rebuilding synthesis without the held-out video...")
    subprocess.run(cmd3, check=True)

    cmd4 = base + [str(SCRIPTS_DIR / "run_phase4.py"), "--playlist", playlist,
                   "--distilled-root", str(scratch_root), "--mode", mode, "--force"]
    if model4:
        cmd4 += ["--model", model4]
    print("  authoring SKILL.md from the N-1 synthesis...")
    subprocess.run(cmd4, check=True)

    skill = scratch_root / playlist / "SKILL.md"
    if not skill.exists():
        sys.exit(f"Phase 4 did not produce {skill}")
    return skill


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--playlist", required=True)
    p.add_argument("--output-root", default="output")
    p.add_argument("--distilled-root", default="distilled")
    p.add_argument("--holdout", help="video_NN to hold out (default: last)")
    p.add_argument("--mode", default="Teacher", choices=["Teacher", "Reviewer", "Advisor"],
                   help="Skill mode for the rebuilt SKILL.md")
    p.add_argument("--skill-path",
                   help="Evaluate a pre-generated SKILL.md instead of rebuilding "
                        "one without the held-out video (NOT leak-free).")
    p.add_argument("--model", help="Override scope.json model for the evaluation call")
    p.add_argument("--keep-scratch", action="store_true",
                   help="Keep the scratch distilled root used for the rebuild.")
    args = p.parse_args()

    distilled_root = Path(args.distilled_root)
    distilled_dir = distilled_root / args.playlist
    if not distilled_dir.exists():
        sys.exit(f"No distilled dir: {distilled_dir}")

    # Find candidate held-out video
    playlist_dir = Path(args.output_root) / args.playlist
    video_dirs = sorted(d for d in playlist_dir.glob("video_*") if d.is_dir())
    if not video_dirs:
        sys.exit(f"No videos under {playlist_dir}")

    if args.holdout:
        matches = [d for d in video_dirs if d.name.startswith(args.holdout)]
        if not matches:
            sys.exit(f"--holdout {args.holdout!r} did not match any video dir.")
        holdout_dir = matches[0]
    else:
        holdout_dir = video_dirs[-1]
    holdout_id = video_id_of(holdout_dir)

    transcript_path = holdout_dir / "transcript.clean.txt"
    if not transcript_path.exists():
        transcript_path = holdout_dir / "transcript.txt"
    if not transcript_path.exists():
        sys.exit(f"No transcript in held-out dir {holdout_dir}")

    scope = scope_module.load(distilled_root, args.playlist)
    model = args.model or scope.model_for("phase3")

    scratch_root = distilled_root / f".eval_{args.playlist}_{holdout_id}"
    rebuild_cost = 0.0
    if args.skill_path:
        skill_path = Path(args.skill_path)
        leak_free = False
        print("  warn: --skill-path given; evaluating a Skill that may have seen the "
              "held-out video. Score is optimistic (leak_free=false).")
    else:
        stage_holdout_corpus(distilled_dir, scratch_root, args.playlist, holdout_id)
        skill_path = rebuild_skill(scratch_root, args.playlist, args.mode,
                                   scope.model_for("phase3"), scope.model_for("phase4"))
        leak_free = True
        cost_file = scratch_root / args.playlist / "cost.json"
        if cost_file.exists():
            try:
                rebuild_cost = float(json.loads(cost_file.read_text()).get("total_estimated_cost_usd", 0.0))
            except (json.JSONDecodeError, ValueError):
                rebuild_cost = 0.0
    if not skill_path.exists():
        sys.exit(f"No SKILL.md to evaluate at {skill_path}.")

    system_prompt = P5_PROMPT.read_text()
    skill_text = skill_path.read_text()
    transcript_text = transcript_path.read_text()
    user_msg = (
        f"SKILL.md:\n\n{skill_text}\n\n"
        f"---\n\n"
        f"Held-out video transcript ({holdout_dir.name}):\n\n{transcript_text}"
    )

    client = ClaudeClient(model=model, max_tokens=2048)
    accumulator = CostAccumulator(playlist=args.playlist)

    print(f"Eval: model={model}, held-out={holdout_dir.name}, leak_free={leak_free}")
    result = client.complete(system=system_prompt, user=user_msg, cache_system=False)
    accumulator.record(phase="eval", result=result)

    text = result.text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]

    try:
        score = json.loads(text)
    except json.JSONDecodeError:
        (distilled_dir / "score.raw.txt").write_text(result.text)
        sys.exit(f"Eval output did not parse. Raw at {distilled_dir / 'score.raw.txt'}.")

    score_record = {
        "playlist": args.playlist,
        "held_out_video": holdout_dir.name,
        "model": model,
        "skill_path": str(skill_path),
        "skill_mode": args.mode if leak_free else None,
        "leak_free": leak_free,
        "rebuild_cost_usd": round(rebuild_cost, 4),
        **score,
    }
    (distilled_dir / "score.json").write_text(json.dumps(score_record, indent=2))

    accumulator.write(distilled_dir / "cost.json")
    overall = score.get("scores", {}).get("overall")
    print(f"\nDone. Overall: {overall}")
    print(f"  Wrote {distilled_dir / 'score.json'}")
    print(f"  Eval cost: ${accumulator.running_total():.4f}"
          + (f" (+ ${rebuild_cost:.4f} to rebuild the N-1 Skill)" if leak_free else ""))

    if leak_free and not args.keep_scratch and scratch_root.exists():
        shutil.rmtree(scratch_root, ignore_errors=True)
    elif leak_free:
        print(f"  Scratch kept at {scratch_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
