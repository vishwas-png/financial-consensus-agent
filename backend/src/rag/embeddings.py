"""Text embeddings used for both semantic chunking and vector search.

We compute embeddings ourselves and hand them to ChromaDB, so the chunker and the
store always use the same model, and tests can swap in a tiny fake embedder.
"""

from typing import Protocol

# Texts sent to the ONNX model per call. Peak memory grows with the batch (measured, 300
# paragraphs, app loaded): batch 8 -> ~375 MB, 16 -> ~440 MB, 32 -> ~575 MB. Passing a whole
# 10-K section at once used the library's internal batch of 32 and would exceed a 512 MB
# server during first-time ingestion. Small batches cost ~30% speed, not quality.
EMBED_BATCH_SIZE = 8


class Embedder(Protocol):
    """Anything that turns a list of texts into a list of vectors."""

    def embed(self, texts: list[str]) -> list[list[float]]: ...


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
        return vectors
