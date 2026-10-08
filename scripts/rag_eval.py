"""
RAG eval harness (intent=qa): score retrieval-augmented answers.

For each question in distilled/<playlist>/qa_eval.jsonl:
  1. retrieve top-k excerpts from the index (rag_index.py output),
  2. generate a grounded answer (prompts/06_rag_answer.md),
  3. have Claude judge it (prompts/07_rag_eval.md) on faithfulness,
     answer-relevance, and context-relevance (0.0-1.0 each).

Aggregates the per-question scores into distilled/<playlist>/rag_score.json.

Like run_eval.py this is a meta-eval (Claude grading Claude) — treat it as a
relative signal across runs, not an absolute truth, and spot-check by hand.

Usage:
    python scripts/rag_eval.py --playlist <name> [--top-k 6]
    python scripts/rag_eval.py --playlist <name> --sample   # scaffold qa_eval.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import scope as scope_module
from accounting import CostAccumulator
from claude_client import ClaudeClient
from rag_ask import build_context, load_index, retrieve


ANSWER_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "06_rag_answer.md"
JUDGE_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "07_rag_eval.md"

_METRICS = ("faithfulness", "answer_relevance", "context_relevance")


def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    return text.strip()


def write_sample(path: Path) -> None:
    sample = [
        {"question": "What is the creator's main argument in this playlist?"},
        {"question": "What does the creator say about <a specific topic they cover>?"},
        {"question": "What is the airspeed velocity of an unladen swallow?"},
    ]
    path.write_text("\n".join(json.dumps(s) for s in sample) + "\n")
    print(f"Wrote a starter eval set to {path}.")
    print("Edit it: keep questions the playlist DOES cover, plus a few it does "
          "NOT (to test honest refusal), then re-run without --sample.")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--playlist", required=True)
    p.add_argument("--distilled-root", default="distilled")
    p.add_argument("--top-k", type=int, default=6)
    p.add_argument("--answer-model", help="Claude model for answers "
                                          "(default: scope.json models.phase3).")
    p.add_argument("--judge-model", help="Claude model for judging "
                                         "(default: scope.json models.phase3).")
    p.add_argument("--sample", action="store_true",
                   help="Write a starter qa_eval.jsonl and exit.")
    args = p.parse_args()

    distilled_root = Path(args.distilled_root)
    distilled_dir = distilled_root / args.playlist
    eval_path = distilled_dir / "qa_eval.jsonl"

    if args.sample:
        distilled_dir.mkdir(parents=True, exist_ok=True)
        write_sample(eval_path)
        return 0

    if not eval_path.exists():
        sys.exit(f"No {eval_path}. Scaffold one with:  "
                 f"python scripts/rag_eval.py --playlist {args.playlist} --sample")

    questions = [json.loads(l)["question"]
                 for l in eval_path.read_text().splitlines() if l.strip()]
    if not questions:
        sys.exit(f"{eval_path} has no questions.")

    meta, chunks, vectors = load_index(distilled_dir)
    scope = scope_module.load(distilled_root, args.playlist)
    answer_model = args.answer_model or scope.model_for("phase3")
    judge_model = args.judge_model or scope.model_for("phase3")

    answer_client = ClaudeClient(model=answer_model, max_tokens=1024)
    judge_client = ClaudeClient(model=judge_model, max_tokens=512)
    answer_system = ANSWER_PROMPT.read_text()
    judge_system = JUDGE_PROMPT.read_text()
    acc = CostAccumulator(playlist=args.playlist)

    print(f"RAG eval: {len(questions)} questions, top_k={args.top_k}, "
          f"answer={answer_model}, judge={judge_model}")

    per_question = []
    for i, q in enumerate(questions, 1):
        hits = retrieve(q, meta, chunks, vectors, args.top_k, None, None)
        context = build_context(hits)

        ans = answer_client.complete(
            system=answer_system,
            user=f"Question: {q}\n\nRetrieved transcript excerpts "
                 f"(cite these exactly):\n\n{context}",
            cache_system=False,
        )
        acc.record(phase="rag-eval", result=ans)

        judged = judge_client.complete(
            system=judge_system,
            user=f"QUESTION:\n{q}\n\nCONTEXT:\n{context}\n\nANSWER:\n{ans.text.strip()}",
            cache_system=False,
        )
        acc.record(phase="rag-eval", result=judged)

        try:
            scores = json.loads(_strip_fence(judged.text))
        except json.JSONDecodeError:
            scores = {m: None for m in _METRICS}
            scores["notes"] = "judge output did not parse"

        per_question.append({"question": q, **scores})
        shown = " ".join(
            f"{m[:4]}={scores.get(m)}" for m in _METRICS
        )
        print(f"  [{i}/{len(questions)}] {shown}  [total ${acc.running_total():.4f}]")

    # Aggregate (mean of non-null per metric).
    means = {}
    for m in _METRICS:
        vals = [pq[m] for pq in per_question if isinstance(pq.get(m), (int, float))]
        means[m] = round(sum(vals) / len(vals), 3) if vals else None
    scored = [v for v in means.values() if v is not None]
    means["overall"] = round(sum(scored) / len(scored), 3) if scored else None

    record = {
        "playlist": args.playlist,
        "n_questions": len(questions),
        "top_k": args.top_k,
        "answer_model": answer_model,
        "judge_model": judge_model,
        "means": means,
        "per_question": per_question,
    }
    (distilled_dir / "rag_score.json").write_text(json.dumps(record, indent=2))
    acc.write(distilled_dir / "cost.json")

    print(f"\nDone. Means: {means}")
    print(f"  Wrote {distilled_dir / 'rag_score.json'}")
    print(f"  Eval cost: ${acc.running_total():.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
