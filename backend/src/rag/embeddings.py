"""Text embeddings used for both semantic chunking and vector search.

We compute embeddings ourselves and hand them to ChromaDB, so the chunker and the
store always use the same model, and tests can swap in a tiny fake embedder.
"""

import logging
import math
import time
from collections.abc import Callable
from typing import Any, Protocol

import httpx

from src.config import COHERE_API_KEY, COHERE_EMBEDDING_MODEL, EMBEDDING_PROVIDER
from src.observability import current_metrics

logger = logging.getLogger(__name__)

# Texts sent to the ONNX model per call. Peak memory grows with the batch (measured, 300
# paragraphs, app loaded): batch 8 -> ~375 MB, 16 -> ~440 MB, 32 -> ~575 MB. Passing a whole
# 10-K section at once used the library's internal batch of 32 and would exceed a 512 MB
# server during first-time ingestion. Small batches cost ~30% speed, not quality.
EMBED_BATCH_SIZE = 8


class Embedder(Protocol):
    """Anything that turns a list of texts into a list of vectors."""

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class EmbeddingError(Exception):
    """The embedding service failed (after retries) or returned something unusable."""


def embed_query(embedder: Embedder, text: str) -> list[float]:
    """Embed a search query. Uses the embedder's query mode when it has one (Cohere does)."""
    query_method = getattr(embedder, "embed_query", None)
    return query_method(text) if query_method else embedder.embed([text])[0]


def collection_suffix(embedder: Embedder) -> str:
    """Vectors from different models can't share a collection (different sizes and spaces)."""
    return getattr(embedder, "COLLECTION_SUFFIX", "")


class LocalEmbedder:
    """all-MiniLM-L6-v2 run locally via ONNX (ChromaDB's default model).

    No API key and no cost. The model (~80 MB) is baked into the Docker image, or
    downloaded once on first local use to ~/.cache/chroma. Loaded lazily so importing
    this module stays fast.
    """

    def __init__(self, batch_size: int = EMBED_BATCH_SIZE) -> None:
        self._model = None
        self.batch_size = batch_size

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self._model is None:
            from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

            self._model = DefaultEmbeddingFunction()
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start:start + self.batch_size]
            vectors.extend([float(x) for x in vector] for vector in self._model(batch))
        _record_embedding(len(texts))
        return vectors


# ---------- Hosted embeddings (deployed) ----------

COHERE_EMBED_URL = "https://api.cohere.com/v2/embed"
COHERE_BATCH_SIZE = 96           # the API's maximum texts per request
MAX_RETRY_WAIT = 60.0            # seconds; per-minute rate limits reset within a minute
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class CohereEmbedder:
    """Cohere `embed-english-v3.0` over HTTPS: no local model, almost no CPU or memory.

    Documents and queries use different input types (search_document / search_query),
    which the model is trained for. Vectors are normalised so cosine distance behaves the
    same as with the local model. Inject `http` and `sleep` in tests.
    """

    COLLECTION_SUFFIX = "_cohere_en3"

    def __init__(
        self,
        api_key: str | None = COHERE_API_KEY,
        model: str = COHERE_EMBEDDING_MODEL,
        batch_size: int = COHERE_BATCH_SIZE,
        max_attempts: int = 6,           # backoff 2+4+8+16+32s spans a full per-minute window
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        api_key = (api_key or "").strip()
        if not api_key:
            raise EmbeddingError("COHERE_API_KEY is not set (needed when EMBEDDING_PROVIDER=cohere).")
        self._api_key = api_key
        self.model = model
        self.batch_size = batch_size
        self.max_attempts = max_attempts
        self._http = http or httpx.Client(timeout=60.0)
        self._sleep = sleep

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, "search_document")

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "search_query")[0]

    def _embed(self, texts: list[str], input_type: str) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            vectors.extend(self._embed_batch(texts[start:start + self.batch_size], input_type))
        return vectors

    def _embed_batch(self, texts: list[str], input_type: str) -> list[list[float]]:
        body = {"model": self.model, "texts": texts, "input_type": input_type,
                "embedding_types": ["float"], "truncate": "END"}
        data = self._post_with_retry(body)
        _record_embedding(len(texts))  # one API call
        embeddings = (data.get("embeddings") or {}).get("float") or []
        if len(embeddings) != len(texts):
            raise EmbeddingError(f"Cohere returned {len(embeddings)} embeddings for {len(texts)} texts")
        return [_normalize([float(x) for x in vector]) for vector in embeddings]

    def _post_with_retry(self, body: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self._http.post(COHERE_EMBED_URL, json=body,
                                           headers={"Authorization": f"Bearer {self._api_key}"})
            except httpx.TransportError as error:
                problem = type(error).__name__
                retry_after = None
            else:
                if response.status_code == 200:
                    return response.json()
                if response.status_code not in RETRYABLE_STATUS:
                    raise EmbeddingError(f"Cohere embeddings HTTP {response.status_code}: {response.text[:300]}")
                problem = f"HTTP {response.status_code}"
                retry_after = _retry_after_seconds(response)
            if attempt == self.max_attempts:
                raise EmbeddingError(f"Cohere embeddings failed after {attempt} attempts ({problem})")
            delay = min(retry_after if retry_after is not None else 2.0 ** attempt, MAX_RETRY_WAIT)
            logger.warning("Cohere embeddings %s on attempt %d/%d; retrying in %.0fs",
                           problem, attempt, self.max_attempts, delay)
            self._sleep(delay)
        raise AssertionError("unreachable")


def _record_embedding(texts: int) -> None:
    metrics = current_metrics()
    if metrics is not None:
        metrics.add_embedding_call(texts)


def _retry_after_seconds(response: httpx.Response) -> float | None:
    try:
        return float(response.headers["retry-after"])
    except (KeyError, ValueError):
        return None


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector] if norm else vector


def make_embedder(provider: str = EMBEDDING_PROVIDER) -> Embedder:
    """The embedder chosen by EMBEDDING_PROVIDER ("local" or "cohere")."""
    if provider == "cohere":
        return CohereEmbedder()
    if provider == "local":
        return LocalEmbedder()
    raise EmbeddingError(f"Unknown EMBEDDING_PROVIDER {provider!r} (use 'local' or 'cohere')")
