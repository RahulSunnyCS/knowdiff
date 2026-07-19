"""
Embeddings gateway — the single seam between the RAG pipeline and whatever
turns text into vectors.

Why an adapter (mirrors claude_client.py):
- rag_index.py / rag_ask.py should not know whether vectors come from a local
  model, a hosted API, or a deterministic baseline. This file is the one place
  that changes when you swap providers.

Providers:
    local   fastembed (preferred) or sentence-transformers. Runs offline, $0,
            no API key. DEFAULT — keeps the project's local-first promise.
    hash    Zero-dependency hashing vectorizer. Not semantic; a fast baseline
            for tests, CI, and fully-offline smoke runs. Deterministic.
    voyage  Voyage AI API (Anthropic's recommended embeddings partner).
            Needs VOYAGE_API_KEY.
    openai  OpenAI embeddings API. Needs OPENAI_API_KEY.

All heavy imports are lazy (inside methods) so importing this module never
requires numpy / fastembed / an SDK — the same guard extract_playlist.py uses
for Whisper.

Usage:
    from embeddings import get_embedder
    emb = get_embedder("local", "BAAI/bge-small-en-v1.5")
    doc_vecs = emb.embed_documents(["...", "..."])   # list[list[float]]
    q_vec    = emb.embed_query("a question")          # list[float]
    print(emb.name, emb.dim)
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from abc import ABC, abstractmethod

# Query vs document matters for asymmetric retrieval models (e.g. bge, voyage).
# bge-* recommends this instruction on the *query* side only.
_BGE_QUERY_INSTRUCTION = (
    "Represent this sentence for searching relevant passages: "
)
_TOKEN_RE = re.compile(r"[a-z0-9]+")


class Embedder(ABC):
    """Common interface. Vectors are plain Python float lists so this module
    stays numpy-free; callers turn them into arrays for the math."""

    name: str
    dim: int

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        ...

    def embed_query(self, text: str) -> list[float]:
        # Default: treat a query like a document. Asymmetric providers override.
        return self.embed_documents([text])[0]


# ── hash: zero-dependency baseline ────────────────────────────────────────────
class HashEmbedder(Embedder):
    """Deterministic hashing vectorizer (a real, if unsophisticated, technique).

    Bag-of-words hashed into `dim` buckets with the signed-hash trick, then
    L2-normalized. No dependencies, no model download, fully offline. Useful as
    a retrieval baseline and for tests; NOT semantic, so real use should prefer
    `local` or an API provider.
    """

    def __init__(self, dim: int = 512) -> None:
        self.dim = dim
        self.name = f"hash:d{dim}"

    def _vec(self, text: str) -> list[float]:
        buckets = [0.0] * self.dim
        for tok in _TOKEN_RE.findall(text.lower()):
            h = int.from_bytes(hashlib.md5(tok.encode()).digest()[:8], "big")
            idx = h % self.dim
            sign = 1.0 if (h >> 63) & 1 else -1.0
            buckets[idx] += sign
        norm = math.sqrt(sum(v * v for v in buckets)) or 1.0
        return [v / norm for v in buckets]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]


# ── local: fastembed / sentence-transformers ──────────────────────────────────
class LocalEmbedder(Embedder):
    """Local model via fastembed (preferred) or sentence-transformers.

    Both run on CPU, offline, no API key. fastembed is lighter (ONNX) and
    exposes an explicit query-embedding path; sentence-transformers is the
    fallback with a manual bge query instruction.
    """

    def __init__(self, model: str = "BAAI/bge-small-en-v1.5") -> None:
        self.model = model
        self._backend = None
        self._impl = None
        # --- fastembed ---
        try:
            from fastembed import TextEmbedding

            self._impl = TextEmbedding(model_name=model)
            self._backend = "fastembed"
        except ImportError:
            pass
        except Exception:
            # Model name unknown to fastembed etc.; try sentence-transformers.
            self._impl = None

        if self._impl is None:
            # --- sentence-transformers ---
            try:
                from sentence_transformers import SentenceTransformer

                self._impl = SentenceTransformer(model)
                self._backend = "sentence-transformers"
            except ImportError as exc:
                raise RuntimeError(
                    "Local embeddings need a backend. Install one of:\n"
                    "  pip install fastembed              # recommended\n"
                    "  pip install sentence-transformers  # alternate\n"
                    "or use provider 'hash' (zero-dep baseline) / an API "
                    "provider (voyage, openai)."
                ) from exc

        self.name = f"local:{self._backend}:{model}"
        self.dim = len(self.embed_query("dimension probe"))

    def _is_bge(self) -> bool:
        return "bge" in self.model.lower()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self._backend == "fastembed":
            return [list(map(float, v)) for v in self._impl.embed(texts)]
        # sentence-transformers
        vecs = self._impl.encode(texts, normalize_embeddings=True)
        return [list(map(float, v)) for v in vecs]

    def embed_query(self, text: str) -> list[float]:
        if self._backend == "fastembed":
            # fastembed has a dedicated query path for supported models.
            try:
                return [float(x) for x in next(iter(self._impl.query_embed([text])))]
            except Exception:
                return self.embed_documents([text])[0]
        # sentence-transformers: prepend the bge query instruction if relevant.
        q = (_BGE_QUERY_INSTRUCTION + text) if self._is_bge() else text
        vec = self._impl.encode([q], normalize_embeddings=True)[0]
        return [float(x) for x in vec]


# ── voyage: API (Anthropic's embeddings partner) ──────────────────────────────
class VoyageEmbedder(Embedder):
    def __init__(self, model: str = "voyage-3-lite") -> None:
        try:
            import voyageai
        except ImportError as exc:
            raise RuntimeError(
                "provider 'voyage' needs the SDK: pip install voyageai"
            ) from exc
        if not os.environ.get("VOYAGE_API_KEY"):
            raise RuntimeError("VOYAGE_API_KEY is not set.")
        self._client = voyageai.Client()
        self.model = model
        self.name = f"voyage:{model}"
        self.dim = len(self.embed_query("dimension probe"))

    def _embed(self, texts: list[str], input_type: str) -> list[list[float]]:
        resp = self._client.embed(texts, model=self.model, input_type=input_type)
        return [list(map(float, v)) for v in resp.embeddings]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, "document")

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "query")[0]


# ── openai: API (alternate) ───────────────────────────────────────────────────
class OpenAIEmbedder(Embedder):
    def __init__(self, model: str = "text-embedding-3-small") -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "provider 'openai' needs the SDK: pip install openai"
            ) from exc
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is not set.")
        self._client = OpenAI()
        self.model = model
        self.name = f"openai:{model}"
        self.dim = len(self.embed_query("dimension probe"))

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.embeddings.create(model=self.model, input=texts)
        return [list(map(float, d.embedding)) for d in resp.data]


# ── factory ───────────────────────────────────────────────────────────────────
_DEFAULT_MODELS = {
    "local": "BAAI/bge-small-en-v1.5",
    "voyage": "voyage-3-lite",
    "openai": "text-embedding-3-small",
    "hash": "",  # dim is fixed, model name ignored
}


def get_embedder(provider: str, model: str | None = None) -> Embedder:
    """Construct an Embedder for `provider`. `model` overrides the provider
    default; ignored for `hash`."""
    provider = (provider or "local").lower()
    model = model or _DEFAULT_MODELS.get(provider)
    if provider == "hash":
        return HashEmbedder()
    if provider == "local":
        return LocalEmbedder(model)
    if provider == "voyage":
        return VoyageEmbedder(model)
    if provider == "openai":
        return OpenAIEmbedder(model)
    raise ValueError(
        f"unknown embeddings provider {provider!r}; "
        f"expected one of: local, hash, voyage, openai"
    )
