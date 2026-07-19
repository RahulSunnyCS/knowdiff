"""
RAG answerer (intent=qa): ask a question, get an answer grounded in the
playlist with [video_NN @ MM:SS] citations.

Flow:
    1. Load the index built by rag_index.py (vectors + chunks + meta).
    2. Embed the question with the SAME embedder the index was built with
       (refuses to run on a mismatch — you can't compare local vs API vectors).
    3. Cosine top-k retrieval (vectors are pre-normalized, so a dot product).
    4. --retrieve-only: print the excerpts + citations and stop (no Claude,
       $0 — use it to inspect retrieval before paying).
       Otherwise: send excerpts + question to Claude with prompts/06_rag_answer.md
       and print a grounded answer. The prompt makes Claude say "The playlist
       doesn't cover this" rather than fabricate.

Usage:
    python scripts/rag_ask.py --playlist <name> --question "..." [--top-k 6]
    python scripts/rag_ask.py --playlist <name> --question "..." --retrieve-only
    python scripts/rag_ask.py --playlist <name>            # REPL (Ctrl-D to quit)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import scope as scope_module
from embeddings import get_embedder


PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "06_rag_answer.md"


def _mmss(value) -> str:
    if value is None:
        return "??:??"
    try:
        secs = float(value)
    except (TypeError, ValueError):
        return "??:??"
    return f"{int(secs // 60):02d}:{int(secs % 60):02d}"


def _cite(chunk: dict) -> str:
    return f"[{chunk['video']} @ {_mmss(chunk.get('start'))}]"


def load_index(distilled_dir: Path):
    meta_path = distilled_dir / "rag_index.meta.json"
    npz_path = distilled_dir / "rag_index.npz"
    chunks_path = distilled_dir / "chunks.jsonl"
    for pth in (meta_path, npz_path, chunks_path):
        if not pth.exists():
            sys.exit(f"Missing {pth.name}. Build the index first: "
                     f"make index PLAYLIST_NAME=<name>")
    meta = json.loads(meta_path.read_text())
    chunks = [json.loads(line) for line in chunks_path.read_text().splitlines() if line]

    import numpy as np  # lazy: only the RAG path needs numpy
    vectors = np.load(npz_path)["vectors"]
    if vectors.shape[0] != len(chunks):
        sys.exit("Index/chunks length mismatch — rebuild the index.")
    return meta, chunks, vectors


def retrieve(question, meta, chunks, vectors, top_k, provider_override, model_override):
    provider = provider_override or meta.get("provider", "local")
    model = model_override or (meta.get("model") or None)
    emb = get_embedder(provider, model)
    if emb.name != meta.get("embedder"):
        sys.exit(
            f"Embedder mismatch: index was built with {meta.get('embedder')!r} "
            f"but this query would use {emb.name!r}. Query and index must match "
            f"— rebuild the index or pass the matching --provider/--model."
        )

    import numpy as np
    q = np.asarray(emb.embed_query(question), dtype="float32")
    n = np.linalg.norm(q) or 1.0
    q = q / n
    scores = vectors @ q  # cosine (both normalized)
    k = min(top_k, len(chunks))
    top = np.argsort(-scores)[:k]
    return [(chunks[i], float(scores[i])) for i in top]


def build_context(hits) -> str:
    blocks = []
    for chunk, _score in hits:
        blocks.append(f"{_cite(chunk)}\n{chunk['text']}")
    return "\n\n---\n\n".join(blocks)


def answer_one(question, meta, chunks, vectors, args, distilled_dir):
    hits = retrieve(question, meta, chunks, vectors, args.top_k,
                    args.provider, args.model)

    if args.retrieve_only:
        print(f"\nTop {len(hits)} excerpts for: {question!r}\n")
        for chunk, score in hits:
            print(f"  {score:+.3f}  {_cite(chunk)}")
            snippet = chunk["text"][:200].replace("\n", " ")
            print(f"         {snippet}{'…' if len(chunk['text']) > 200 else ''}\n")
        return 0

    # Grounded generation.
    from accounting import CostAccumulator
    from claude_client import ClaudeClient

    scope = scope_module.load(Path(args.distilled_root), args.playlist)
    model = args.answer_model or scope.model_for("phase3")
    system_prompt = PROMPT_PATH.read_text()
    user_msg = (
        f"Question: {question}\n\n"
        f"Retrieved transcript excerpts (cite these exactly):\n\n"
        f"{build_context(hits)}"
    )

    client = ClaudeClient(model=model, max_tokens=1024)
    acc = CostAccumulator(playlist=args.playlist)
    result = client.complete(system=system_prompt, user=user_msg, cache_system=False)
    acc.record(phase="qa", result=result)
    acc.write(distilled_dir / "cost.json")

    print("\n" + result.text.strip() + "\n")
    print(f"— {len(hits)} excerpts retrieved · model={model} · "
          f"this answer ≈ ${acc.running_total():.4f}")

    # Audit trail: what was asked, answered, and retrieved.
    (distilled_dir / "qa_last.json").write_text(json.dumps({
        "question": question,
        "answer": result.text.strip(),
        "model": model,
        "retrieved": [{"id": c["id"], "cite": _cite(c), "score": s} for c, s in hits],
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--playlist", required=True)
    p.add_argument("--distilled-root", default="distilled")
    p.add_argument("--question", "--q", dest="question",
                   help="The question. Omit for an interactive REPL.")
    p.add_argument("--top-k", type=int, default=6)
    p.add_argument("--retrieve-only", action="store_true",
                   help="Print retrieved excerpts and exit — no Claude call, $0.")
    p.add_argument("--provider", help="Override the index's embeddings provider "
                                      "(must still match the index's embedder).")
    p.add_argument("--model", help="Override the index's embeddings model.")
    p.add_argument("--answer-model", help="Claude model for the answer "
                                          "(default: scope.json models.phase3).")
    args = p.parse_args()

    distilled_dir = Path(args.distilled_root) / args.playlist
    meta, chunks, vectors = load_index(distilled_dir)

    if args.question:
        return answer_one(args.question, meta, chunks, vectors, args, distilled_dir)

    # REPL
    print(f"Ask about '{args.playlist}' ({meta['count']} chunks, "
          f"{meta['embedder']}). Ctrl-D to quit.")
    while True:
        try:
            q = input("\n? ").strip()
        except EOFError:
            print()
            return 0
        if q:
            answer_one(q, meta, chunks, vectors, args, distilled_dir)


if __name__ == "__main__":
    sys.exit(main())
