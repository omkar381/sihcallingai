"""
RAG (Retrieval-Augmented Generation) knowledge engine.

A small FAISS vector index over a curated farming knowledge base (government
schemes, mandi/selling mechanics, crop pest & disease notes, soil and weather
guidance). The chatbot retrieves the top-k relevant snippets for a farmer's
question and includes them as grounding context, instead of relying only on
the model's own knowledge.

Embeddings come from Gemini's embedding API (models/gemini-embedding-001), so no
extra API key or local model download is needed beyond GEMINI_API_KEY, which
this project already requires. The index is built once and cached to disk;
it is rebuilt automatically if the knowledge base file changes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import numpy as np

from app.config import get_settings

logger = logging.getLogger(__name__)

KB_PATH = os.path.join("data", "knowledge", "farming_kb.json")
INDEX_PATH = os.path.join("data", "knowledge", "faiss.index")
META_PATH = os.path.join("data", "knowledge", "faiss_meta.json")

_EMBED_MODEL = "models/gemini-embedding-001"

_lock = threading.Lock()
_index = None  # type: ignore[assignment]
_docs: List[Dict[str, str]] = []
_ready = False


@dataclass
class RetrievedDoc:
    id: str
    title: str
    text: str
    score: float

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "title": self.title, "text": self.text, "score": round(self.score, 4)}


def _load_kb() -> List[Dict[str, str]]:
    with open(KB_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _kb_hash(docs: List[Dict[str, str]]) -> str:
    blob = json.dumps(docs, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _embed(texts: List[str], task_type: str) -> np.ndarray:
    """Embed a batch of texts with Gemini. Returns an (n, d) float32 array, L2-normalised."""
    import google.generativeai as genai

    settings = get_settings()
    if not settings.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set; cannot build embeddings")
    genai.configure(api_key=settings.GEMINI_API_KEY)

    vectors: List[List[float]] = []
    for text in texts:
        result = genai.embed_content(model=_EMBED_MODEL, content=text, task_type=task_type)
        vectors.append(result["embedding"])

    arr = np.array(vectors, dtype="float32")
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return arr / norms


def _build_index(docs: List[Dict[str, str]]):
    import faiss

    texts = [f"{d['title']}. {d['text']}" for d in docs]
    vectors = _embed(texts, task_type="retrieval_document")
    index = faiss.IndexFlatIP(vectors.shape[1])
    index.add(vectors)

    os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
    faiss.write_index(index, INDEX_PATH)
    with open(META_PATH, "w", encoding="utf-8") as f:
        json.dump({"hash": _kb_hash(docs), "docs": docs}, f, ensure_ascii=False)

    return index


def ensure_ready() -> bool:
    """Build or load the FAISS index. Safe to call repeatedly; call once at startup to pre-warm."""
    global _index, _docs, _ready

    with _lock:
        if _ready:
            return True

        try:
            docs = _load_kb()
        except Exception as exc:
            logger.error("Could not load knowledge base %s: %s", KB_PATH, exc)
            return False

        current_hash = _kb_hash(docs)
        cached_meta = None
        if os.path.isfile(META_PATH):
            try:
                with open(META_PATH, "r", encoding="utf-8") as f:
                    cached_meta = json.load(f)
            except Exception:
                cached_meta = None

        try:
            import faiss

            if cached_meta and cached_meta.get("hash") == current_hash and os.path.isfile(INDEX_PATH):
                index = faiss.read_index(INDEX_PATH)
                logger.info("RAG index loaded from cache (%d docs)", len(docs))
            else:
                logger.info("Building RAG index for %d knowledge base entries ...", len(docs))
                index = _build_index(docs)
                logger.info("RAG index built and cached at %s", INDEX_PATH)
        except Exception as exc:
            logger.error("Could not build/load RAG index: %s", exc)
            return False

        _index = index
        _docs = docs
        _ready = True
        return True


def retrieve(query: str, k: int = 3) -> List[RetrievedDoc]:
    """Top-k knowledge base snippets relevant to `query`. Returns [] if unavailable, never raises."""
    if not query or not query.strip():
        return []

    if not ensure_ready():
        return []

    try:
        q_vec = _embed([query], task_type="retrieval_query")
    except Exception as exc:
        logger.warning("RAG query embedding failed: %s", exc)
        return []

    with _lock:
        if _index is None:
            return []
        scores, idxs = _index.search(q_vec, min(k, len(_docs)))

    results: List[RetrievedDoc] = []
    for score, idx in zip(scores[0], idxs[0]):
        if idx < 0 or idx >= len(_docs):
            continue
        doc = _docs[idx]
        results.append(RetrievedDoc(id=doc["id"], title=doc["title"], text=doc["text"], score=float(score)))
    return results


def context_block(query: str, k: int = 3, min_score: float = 0.65) -> str:
    """A ready-to-inject prompt block of the top matches, or "" if nothing relevant enough."""
    hits = [d for d in retrieve(query, k=k) if d.score >= min_score]
    if not hits:
        return ""
    lines = ["\n--- Knowledge base (for grounding, do not read file names aloud) ---"]
    for d in hits:
        lines.append(f"[{d.title}]: {d.text}")
    return "\n".join(lines)
