"""ChromaDB store for filing chunks + metadata-pre-filtered retrieval for the agents.

Pre-filtering: every query carries a `where` filter (ticker, filing, section), so
vector search only ranks chunks from the right company, the right 10-K and the
right part of it. Without it, a "supply chain risk" query for AAPL could return
another company's risk factors that happen to be worded similarly.

Usage (manual check):  python -m src.rag.retriever AAPL "supply chain risk"
"""

import logging
import sys
from dataclasses import dataclass
from typing import Any

import chromadb

from src.config import CHROMA_DIR, TOP_K_CHUNKS
from src.rag.edgar import EdgarClient
from src.rag.embeddings import Embedder, LocalEmbedder
from src.rag.ingest import Chunk, FilingInfo, build_chunks, extract_text_from_html

logger = logging.getLogger(__name__)

COLLECTION_NAME = "sec_filings"

# What the agents need from a 10-K: (section to search, what to search for).
# Each topic is a separate pre-filtered query so every area is represented.
RETRIEVAL_TOPICS: list[tuple[str, str]] = [
    ("1A", "most material risks to the business: competition, supply chain, regulation, demand"),
    ("1", "business overview, main products and services, segments and sources of revenue"),
    ("7", "net sales, revenue and gross margin trends by segment and product, and their drivers"),
    ("7", "liquidity, cash flow, debt, share repurchases, dividends and capital resources"),
    ("7A", "interest rate risk and foreign currency exchange rate risk"),
    ("3", "material legal proceedings, lawsuits and regulatory investigations"),
]
RESULTS_PER_TOPIC = 2


@dataclass(frozen=True)
class RetrievedChunk:
    id: str
    text: str
    metadata: dict[str, Any]
    distance: float

    def for_prompt(self) -> str:
        """How agents see a chunk: id first, so they can cite it exactly."""
        return f"[{self.id}] (Item {self.metadata['section']} - {self.metadata['section_title']}) {self.text}"


class FilingStore:
    """Thin wrapper around one ChromaDB collection. Inject `client` and `embedder` in tests."""

    def __init__(
        self,
        client: Any = None,
        embedder: Embedder | None = None,
        collection_name: str = COLLECTION_NAME,
    ) -> None:
        self.client = client or chromadb.PersistentClient(path=str(CHROMA_DIR))
        self.embedder = embedder or LocalEmbedder()
        # Cosine distance suits sentence embeddings. We pass embeddings ourselves,
        # so Chroma's built-in embedding function is not used.
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
            embedding_function=None,
        )

    def add_chunks(self, chunks: list[Chunk]) -> int:
        """Upsert chunks (same ids overwrite, so re-ingesting never duplicates)."""
        if not chunks:
            return 0
        self.collection.upsert(
            ids=[c.id for c in chunks],
            documents=[c.text for c in chunks],
            metadatas=[c.metadata for c in chunks],
            embeddings=self.embedder.embed([c.text for c in chunks]),
        )
        return len(chunks)

    def latest_filing(self, ticker: str, form_type: str = "10-K") -> dict[str, Any] | None:
        """Metadata of the newest indexed filing for this ticker, or None if nothing is indexed."""
        found = self.collection.get(
            where={"$and": [{"ticker": ticker}, {"form_type": form_type}]},
            include=["metadatas"],
        )
        metadatas = found["metadatas"] or []
        if not metadatas:
            return None
        return max(metadatas, key=lambda m: (m["fiscal_year"], m["filing_date"]))

    def query(self, text: str, ticker: str, accession: str, sections: list[str], n_results: int) -> list[RetrievedChunk]:
        """Vector search restricted (pre-filtered) to one filing of one ticker, in the given sections."""
        where = {"$and": [
            {"ticker": ticker},
            {"accession": accession},
            {"section": {"$in": sections}},
        ]}
        result = self.collection.query(
            query_embeddings=self.embedder.embed([text]),
            n_results=n_results,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        return [
            RetrievedChunk(id=chunk_id, text=document, metadata=metadata, distance=distance)
            for chunk_id, document, metadata, distance in zip(
                result["ids"][0], result["documents"][0], result["metadatas"][0], result["distances"][0]
            )
        ]


def ingest_latest_from_edgar(ticker: str, store: FilingStore, edgar: EdgarClient, form_type: str = "10-K") -> dict[str, Any]:
    """Download the latest filing from EDGAR, chunk it, and index it. Returns its metadata."""
    ref = edgar.latest_filing(ticker, form_type)
    html = edgar.download(ref).read_text(encoding="utf-8", errors="replace")
    info = FilingInfo(ticker=ref.ticker, form_type=ref.form_type, fiscal_year=ref.fiscal_year,
                      filing_date=ref.filing_date, accession=ref.accession, source="edgar")
    count = store.add_chunks(build_chunks(extract_text_from_html(html), info, store.embedder))
    logger.info("Indexed %d chunks from %s %s (%s)", count, ticker, form_type, ref.accession)
    return store.latest_filing(ticker, form_type) or {}


def retrieve_for_agents(ticker: str, store: FilingStore, filing: dict[str, Any], top_k: int = TOP_K_CHUNKS) -> list[RetrievedChunk]:
    """Run every RETRIEVAL_TOPIC query against one filing and pick up to top_k distinct chunks.

    Coverage first: each topic's best hit is taken (in topic order), then remaining slots are
    filled with the closest leftover hits. Distances from different queries aren't directly
    comparable, so a pure "sort by distance" could fill every slot from one topic.
    """
    per_topic = [
        store.query(topic, ticker, filing["accession"], [section], RESULTS_PER_TOPIC)
        for section, topic in RETRIEVAL_TOPICS
    ]
    selected: dict[str, RetrievedChunk] = {}
    for hits in per_topic:
        for chunk in hits:
            if chunk.id not in selected:
                selected[chunk.id] = chunk
                break
    leftovers = sorted(
        (chunk for hits in per_topic for chunk in hits if chunk.id not in selected),
        key=lambda c: c.distance,
    )
    for chunk in leftovers:
        if chunk.id not in selected:
            selected[chunk.id] = chunk
    return list(selected.values())[:top_k]


def get_filing_chunks(
    ticker: str,
    store: FilingStore | None = None,
    edgar: EdgarClient | None = None,
    top_k: int = TOP_K_CHUNKS,
) -> list[str]:
    """Filing excerpts for the agents, formatted with citable ids.

    Uses the newest filing already indexed (EDGAR download or a manually ingested PDF).
    If nothing is indexed yet, downloads the latest 10-K from EDGAR first (lazy ingestion).
    Raises FilingError subclasses if EDGAR has no filing / can't be reached.
    """
    store = store or FilingStore()
    filing = store.latest_filing(ticker)
    if filing is None:
        filing = ingest_latest_from_edgar(ticker, store, edgar or EdgarClient())
    if not filing:
        return []
    return [chunk.for_prompt() for chunk in retrieve_for_agents(ticker, store, filing, top_k)]


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print('Usage: python -m src.rag.retriever <TICKER> "<query>"')
        sys.exit(2)
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ticker_arg, query_arg = sys.argv[1].upper(), sys.argv[2]
    cli_store = FilingStore()
    latest = cli_store.latest_filing(ticker_arg) or ingest_latest_from_edgar(ticker_arg, cli_store, EdgarClient())
    print(f"Filing: {latest['accession']} FY{latest['fiscal_year']} ({latest['source']})\n")
    sections = sorted({s for s, _ in RETRIEVAL_TOPICS})
    for hit in cli_store.query(query_arg, ticker_arg, latest["accession"], sections, 5):
        print(f"{hit.distance:.3f}  {hit.for_prompt()[:300]}\n")
