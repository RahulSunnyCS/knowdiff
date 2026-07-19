"""
RAG indexer (intent=qa): build a retrieval index for a playlist.

Chunks each video over its `transcript.timestamped.json` segments so every
chunk carries a real `[video_NN @ MM:SS]` span — the timestamped citation is
the whole point, so we chunk the timestamped source, not the flat clean text.
Falls back to `transcript.clean.txt` (no timestamps) when the sidecar is absent.

Writes, under distilled/<playlist>/:
    chunks.jsonl        one JSON per chunk: {id, video, start, end, text}
    rag_index.npz       float32 matrix `vectors` [N, dim], row i ↔ chunk i
    rag_index.meta.json {provider, model, embedder, dim, count, ids, chunk_*}

The embedder identity is recorded so rag_ask.py refuses to query an index built
with a different embedder (you can't compare local vs API vectors).

Usage:
    python scripts/rag_index.py --playlist <name> [--provider local] [--model ...]
                                [--chunk-size 220] [--overlap 40]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import scope as scope_module
from embeddings import get_embedder


def _mmss(value) -> str:
    if value is None:
        return "??:??"
    try:
        secs = float(value)
    except (TypeError, ValueError):
        return "??:??"
    return f"{int(secs // 60):02d}:{int(secs % 60):02d}"


def _video_id(video_dir: Path) -> str:
    # "video_03_some-slug" -> "video_03" (matches run_phase2 / citations).
    return "_".join(video_dir.name.split("_")[:2])


def chunk_video(
    video_dir: Path, chunk_size: int, overlap: int
) -> list[dict]:
    """Return chunks for one video, each with precise start/end when available.

    Strategy: flatten timestamped segments into (word, segment_index) pairs,
    then slide a window of `chunk_size` words stepping by (chunk_size-overlap).
    A window's start/end come from the first/last segment it touches.
    """
    vid = _video_id(video_dir)
    ts_path = video_dir / "transcript.timestamped.json"

    if ts_path.exists():
        try:
            segments = json.loads(ts_path.read_text()).get("segments", [])
        except json.JSONDecodeError:
            segments = []
    else:
        segments = []

    words: list[str] = []
    owner: list[int] = []  # segment index each word belongs to
    if segments:
        for si, seg in enumerate(segments):
            for w in (seg.get("text") or "").split():
                words.append(w)
                owner.append(si)
    else:
        # No timestamps — fall back to cleaned (or raw) flat text.
        flat = video_dir / "transcript.clean.txt"
        if not flat.exists():
            flat = video_dir / "transcript.txt"
        if not flat.exists():
            return []
        for w in flat.read_text().split():
            words.append(w)
            owner.append(-1)

    if not words:
        return []

    step = max(1, chunk_size - overlap)
    chunks: list[dict] = []
    n = len(words)
    for start_i in range(0, n, step):
        window = words[start_i:start_i + chunk_size]
        if not window:
            break
        owners = owner[start_i:start_i + chunk_size]
        real = [o for o in owners if o >= 0]
        if real and segments:
            start_ts = segments[real[0]].get("start")
            end_ts = segments[real[-1]].get("end")
        else:
            start_ts = end_ts = None
        chunks.append({
            "id": f"{vid}#{len(chunks):04d}",
            "video": vid,
            "start": start_ts,
            "end": end_ts,
            "text": " ".join(window),
        })
        if start_i + chunk_size >= n:
            break
    return chunks


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--playlist", required=True)
    p.add_argument("--output-root", default="output")
    p.add_argument("--distilled-root", default="distilled")
    p.add_argument("--provider", help="Override scope.json embeddings.provider "
                                       "(local|hash|voyage|openai)")
    p.add_argument("--model", help="Override scope.json embeddings.model")
    p.add_argument("--chunk-size", type=int, default=220,
                   help="Words per chunk (default 220 ≈ 60-90s of speech).")
    p.add_argument("--overlap", type=int, default=40,
                   help="Word overlap between consecutive chunks.")
    args = p.parse_args()

    if args.overlap >= args.chunk_size:
        sys.exit("--overlap must be smaller than --chunk-size")

    distilled_root = Path(args.distilled_root)
    playlist_dir = Path(args.output_root) / args.playlist
    distilled_dir = distilled_root / args.playlist
    if not playlist_dir.exists():
        sys.exit(f"No extracted playlist at {playlist_dir}. Run extract first.")
    distilled_dir.mkdir(parents=True, exist_ok=True)

    scope = scope_module.load(distilled_root, args.playlist)
    provider = args.provider or scope.embedding_provider()
    # `hash` ignores the model name; blank it so meta/output aren't misleading.
    model = "" if provider == "hash" else (args.model or scope.embedding_model())

    video_dirs = sorted(d for d in playlist_dir.glob("video_*") if d.is_dir())
    if not video_dirs:
        sys.exit(f"No videos under {playlist_dir}")

    chunks: list[dict] = []
    no_ts = 0
    for vd in video_dirs:
        vchunks = chunk_video(vd, args.chunk_size, args.overlap)
        if vchunks and vchunks[0]["start"] is None:
            no_ts += 1
        chunks.extend(vchunks)
    if not chunks:
        sys.exit("No transcript text found to index.")

    print(f"Indexing {len(chunks)} chunks from {len(video_dirs)} videos "
          f"(provider={provider}, model={model or 'default'})")
    if no_ts:
        print(f"  ! {no_ts} video(s) had no transcript.timestamped.json — "
              f"their chunks will cite '??:??'. Re-extract to get timestamps.")

    emb = get_embedder(provider, model)
    vectors = emb.embed_documents([c["text"] for c in chunks])

    import numpy as np  # lazy: only the RAG path needs numpy

    mat = np.asarray(vectors, dtype="float32")
    # Normalize so retrieval can use a plain dot product as cosine similarity.
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    mat = mat / norms

    (distilled_dir / "chunks.jsonl").write_text(
        "\n".join(json.dumps(c, ensure_ascii=False) for c in chunks) + "\n",
        encoding="utf-8",
    )
    np.savez_compressed(distilled_dir / "rag_index.npz", vectors=mat)
    meta = {
        "playlist": args.playlist,
        "provider": provider,
        "model": model or "",
        "embedder": emb.name,
        "dim": emb.dim,
        "count": len(chunks),
        "chunk_size": args.chunk_size,
        "overlap": args.overlap,
        "ids": [c["id"] for c in chunks],
    }
    (distilled_dir / "rag_index.meta.json").write_text(
        json.dumps(meta, indent=2)
    )

    print(f"✓ Wrote {distilled_dir / 'rag_index.npz'} "
          f"({len(chunks)}×{emb.dim}, embedder={emb.name})")
    print(f"  {distilled_dir / 'chunks.jsonl'}")
    print(f"  {distilled_dir / 'rag_index.meta.json'}")
    print(f"\nAsk questions with:  make ask PLAYLIST_NAME={args.playlist} "
          f"Q=\"your question\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
