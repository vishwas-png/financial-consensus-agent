"""LocalEmbedder batching: memory-bounded batches, order preserved, plain floats out."""

import numpy as np

from src.rag.embeddings import EMBED_BATCH_SIZE, LocalEmbedder


class RecordingModel:
    """Stands in for the ONNX model: records batch sizes, returns numpy vectors like the real one."""

    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    def __call__(self, texts: list[str]) -> list[np.ndarray]:
        self.batch_sizes.append(len(texts))
        return [np.array([float(len(text)), 1.0], dtype=np.float32) for text in texts]


def make_embedder(batch_size: int = EMBED_BATCH_SIZE) -> tuple[LocalEmbedder, RecordingModel]:
    embedder = LocalEmbedder(batch_size=batch_size)
    model = RecordingModel()
    embedder._model = model  # skip loading the real ONNX model
    return embedder, model


def test_large_input_is_embedded_in_small_batches() -> None:
    embedder, model = make_embedder()
    texts = [f"paragraph {i}" for i in range(EMBED_BATCH_SIZE * 3 + 5)]
    vectors = embedder.embed(texts)
    assert model.batch_sizes == [EMBED_BATCH_SIZE] * 3 + [5]
    assert len(vectors) == len(texts)


def test_order_is_preserved_across_batches() -> None:
    embedder, _ = make_embedder(batch_size=2)
    texts = ["a", "bbb", "cc", "dddd", "e"]
    assert [vector[0] for vector in embedder.embed(texts)] == [1.0, 3.0, 2.0, 4.0, 1.0]


def test_outputs_are_plain_python_floats() -> None:
    embedder, _ = make_embedder()
    vector = embedder.embed(["x"])[0]
    assert all(type(value) is float for value in vector)


def test_empty_input_does_not_load_the_model() -> None:
    embedder = LocalEmbedder()
    assert embedder.embed([]) == []
    assert embedder._model is None


def test_default_batch_is_small_enough_for_a_512_mb_host() -> None:
    assert EMBED_BATCH_SIZE <= 8  # see the measurements in src/rag/embeddings.py
