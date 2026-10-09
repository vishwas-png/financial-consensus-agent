"""Text embeddings used for both semantic chunking and vector search.

We compute embeddings ourselves and hand them to ChromaDB, so the chunker and the
store always use the same model, and tests can swap in a tiny fake embedder.
"""

from typing import Protocol


class Embedder(Protocol):
    """Anything that turns a list of texts into a list of vectors."""

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class LocalEmbedder:
    """all-MiniLM-L6-v2 run locally via ONNX (ChromaDB's default model).

    No API key and no cost. The model (~80 MB) downloads once on first use
    to ~/.cache/chroma. Loaded lazily so importing this module stays fast.
    """

    def __init__(self) -> None:
        self._model = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self._model is None:
            from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

            self._model = DefaultEmbeddingFunction()
        return [[float(x) for x in vector] for vector in self._model(texts)]
