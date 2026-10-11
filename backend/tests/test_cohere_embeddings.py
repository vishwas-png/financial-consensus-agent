"""CohereEmbedder against a mocked HTTP transport (no network), plus provider selection."""

import json
import math
from typing import Any

import httpx
import pytest

from src.rag.embeddings import (
    CohereEmbedder,
    EmbeddingError,
    LocalEmbedder,
    collection_suffix,
    embed_query,
    make_embedder,
)
from src.rag.retriever import COLLECTION_NAME, FilingStore
from tests.fakes import BagOfWordsEmbedder


class FakeCohere:
    """Records requests; answers each with a scripted (status, headers) failure or a 200 with vectors."""

    def __init__(self, failures: list[tuple[int, dict[str, str]]] | None = None) -> None:
        self.failures = list(failures or [])
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.failures:
            status, headers = self.failures.pop(0)
            return httpx.Response(status, headers=headers, text="scripted failure")
        texts = json.loads(request.content)["texts"]
        # Unnormalised vectors that encode the text length, so order and scaling are checkable
        return httpx.Response(200, json={"embeddings": {"float": [[3.0 * len(t), 4.0 * len(t)] for t in texts]}})

    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests]


def make_cohere(fake: FakeCohere, **kwargs: Any) -> tuple[CohereEmbedder, list[float]]:
    sleeps: list[float] = []
    embedder = CohereEmbedder(api_key="test-key", http=httpx.Client(transport=httpx.MockTransport(fake.handler)),
                              sleep=sleeps.append, **kwargs)
    return embedder, sleeps


def test_documents_are_batched_in_order_and_normalised() -> None:
    fake = FakeCohere()
    embedder, _ = make_cohere(fake, batch_size=2)
    vectors = embedder.embed(["a", "bb", "ccc", "dddd", "eeeee"])

    assert [body["texts"] for body in fake.bodies()] == [["a", "bb"], ["ccc", "dddd"], ["eeeee"]]
    assert vectors == [[0.6, 0.8]] * 5  # (3n, 4n) normalised to unit length
    assert all(math.isclose(math.hypot(*v), 1.0) for v in vectors)


def test_request_shape_and_document_input_type() -> None:
    fake = FakeCohere()
    embedder, _ = make_cohere(fake)
    embedder.embed(["Services net sales grew."])

    assert str(fake.requests[0].url) == "https://api.cohere.com/v2/embed"
    assert fake.bodies()[0] == {"model": "embed-english-v3.0", "texts": ["Services net sales grew."],
                                "input_type": "search_document", "embedding_types": ["float"], "truncate": "END"}


def test_default_batch_is_the_api_maximum() -> None:
    fake = FakeCohere()
    embedder, _ = make_cohere(fake)
    embedder.embed([f"t{i}" for i in range(200)])
    assert [len(body["texts"]) for body in fake.bodies()] == [96, 96, 8]


def test_api_key_goes_in_the_auth_header_not_the_url() -> None:
    fake = FakeCohere()
    embedder, _ = make_cohere(fake)
    embedder.embed(["x"])
    assert fake.requests[0].headers["authorization"] == "Bearer test-key"
    assert "test-key" not in str(fake.requests[0].url)


def test_queries_use_the_query_input_type() -> None:
    fake = FakeCohere()
    embedder, _ = make_cohere(fake)
    assert embed_query(embedder, "liquidity and debt") == [0.6, 0.8]
    assert fake.bodies()[0]["input_type"] == "search_query"


def test_rate_limit_is_retried_respecting_retry_after() -> None:
    fake = FakeCohere(failures=[(429, {"retry-after": "7"}), (503, {})])
    embedder, sleeps = make_cohere(fake)
    assert embedder.embed(["x"]) == [[0.6, 0.8]]
    assert len(fake.requests) == 3
    assert sleeps == [7.0, 4.0]  # server's hint first, then exponential backoff (2 ** attempt)


def test_retry_wait_is_capped() -> None:
    fake = FakeCohere(failures=[(429, {"retry-after": "3600"})])
    embedder, sleeps = make_cohere(fake)
    embedder.embed(["x"])
    assert sleeps == [60.0]


def test_gives_up_after_max_attempts() -> None:
    fake = FakeCohere(failures=[(429, {})] * 3)
    embedder, sleeps = make_cohere(fake, max_attempts=3)
    with pytest.raises(EmbeddingError, match="after 3 attempts"):
        embedder.embed(["x"])
    assert len(sleeps) == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_non_retryable_errors_fail_immediately(status: int) -> None:
    fake = FakeCohere(failures=[(status, {})])
    embedder, sleeps = make_cohere(fake)
    with pytest.raises(EmbeddingError, match=f"HTTP {status}"):
        embedder.embed(["x"])
    assert len(fake.requests) == 1 and sleeps == []


def test_wrong_number_of_embeddings_is_an_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": {"float": [[1.0]]}})

    embedder = CohereEmbedder(api_key="k", http=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(EmbeddingError, match="1 embeddings for 2 texts"):
        embedder.embed(["a", "b"])


@pytest.mark.parametrize("key", [None, "", "  \n"])
def test_missing_key_is_a_clear_error(key: str | None) -> None:
    with pytest.raises(EmbeddingError, match="COHERE_API_KEY"):
        CohereEmbedder(api_key=key)


# ---------- Provider selection and separate collections ----------

def test_make_embedder_selects_the_provider() -> None:
    assert isinstance(make_embedder("local"), LocalEmbedder)
    with pytest.raises(EmbeddingError, match="Unknown EMBEDDING_PROVIDER"):
        make_embedder("gemini")


def test_each_model_gets_its_own_collection() -> None:
    import chromadb

    client = chromadb.EphemeralClient()
    local_store = FilingStore(client=client, embedder=BagOfWordsEmbedder())
    cohere_store = FilingStore(client=client, embedder=CohereEmbedder(api_key="k"))
    assert collection_suffix(BagOfWordsEmbedder()) == ""
    assert local_store.collection.name == COLLECTION_NAME
    assert cohere_store.collection.name == COLLECTION_NAME + "_cohere_en3"


# ---------- Live (opt-in: pytest -m live; needs COHERE_API_KEY in backend/.env) ----------

@pytest.mark.live
def test_live_cohere_embeddings() -> None:
    embedder = CohereEmbedder()
    docs = embedder.embed(["Apple's Services net sales increased.", "Interest rate risk on fixed-income holdings."])
    query = embed_query(embedder, "How did services revenue change?")
    assert [len(v) for v in docs + [query]] == [1024, 1024, 1024]
    similarity = [sum(a * b for a, b in zip(query, doc)) for doc in docs]
    assert similarity[0] > similarity[1]  # the revenue query is closer to the revenue sentence
