"""Retriever tests on an in-memory ChromaDB with a fake embedder (no downloads, no network)."""

import uuid

import chromadb
import pytest

from src.rag.ingest import Chunk, FilingInfo, build_chunks
from src.rag.retriever import FilingStore, get_filing_chunks, retrieve_for_agents
from tests.fakes import BagOfWordsEmbedder
from tests.test_ingest import CURRENCY, SUPPLY, ten_k_text

AAPL = FilingInfo("AAPL", "10-K", 2025, "2025-10-31", "acc-aapl-2025", "edgar")
AAPL_OLD = FilingInfo("AAPL", "10-K", 2024, "2024-11-01", "acc-aapl-2024", "edgar")
MSFT = FilingInfo("MSFT", "10-K", 2025, "2025-07-30", "acc-msft-2025", "edgar")


@pytest.fixture
def store() -> FilingStore:
    # Ephemeral clients share memory inside one process, so each test gets its own collection
    return FilingStore(client=chromadb.EphemeralClient(), embedder=BagOfWordsEmbedder(),
                       collection_name=f"test_{uuid.uuid4().hex}")


def chunk(info: FilingInfo, section: str, index: int, text: str) -> Chunk:
    return Chunk(
        id=f"{info.ticker}-10K-{info.fiscal_year}-{section}-{index:03d}",
        text=text,
        metadata={"ticker": info.ticker, "form_type": "10-K", "fiscal_year": info.fiscal_year,
                  "filing_date": info.filing_date, "accession": info.accession, "source": "edgar",
                  "section": section, "section_title": "Risk Factors" if section == "1A" else "Other",
                  "chunk_index": index},
    )


# ---------- Store basics ----------

def test_upsert_is_idempotent(store: FilingStore) -> None:
    chunks = build_chunks(ten_k_text(), AAPL, store.embedder)
    store.add_chunks(chunks)
    store.add_chunks(chunks)
    assert store.collection.count() == len(chunks)


def test_latest_filing_picks_newest_year(store: FilingStore) -> None:
    store.add_chunks([chunk(AAPL_OLD, "1A", 0, SUPPLY), chunk(AAPL, "1A", 0, SUPPLY)])
    latest = store.latest_filing("AAPL")
    assert latest is not None and latest["accession"] == "acc-aapl-2025"
    assert store.latest_filing("MSFT") is None


# ---------- Metadata pre-filtering ----------

def test_query_never_returns_other_tickers(store: FilingStore) -> None:
    # MSFT has the SAME risk text; without the ticker filter it would rank just as high
    store.add_chunks([chunk(AAPL, "1A", 0, SUPPLY), chunk(MSFT, "1A", 0, SUPPLY), chunk(MSFT, "1A", 1, CURRENCY)])
    hits = store.query("supply chain suppliers components", "AAPL", "acc-aapl-2025", ["1A"], n_results=5)
    assert [h.id for h in hits] == ["AAPL-10K-2025-1A-000"]


def test_query_restricted_to_requested_sections(store: FilingStore) -> None:
    store.add_chunks([chunk(AAPL, "1A", 0, CURRENCY), chunk(AAPL, "7A", 0, CURRENCY)])
    hits = store.query("currency exchange rates", "AAPL", "acc-aapl-2025", ["7A"], n_results=5)
    assert [h.metadata["section"] for h in hits] == ["7A"]


def test_query_restricted_to_one_filing(store: FilingStore) -> None:
    store.add_chunks([chunk(AAPL_OLD, "1A", 0, SUPPLY), chunk(AAPL, "1A", 0, SUPPLY)])
    hits = store.query("suppliers", "AAPL", "acc-aapl-2024", ["1A"], n_results=5)
    assert [h.metadata["fiscal_year"] for h in hits] == [2024]


def test_most_relevant_chunk_ranks_first(store: FilingStore) -> None:
    store.add_chunks([chunk(AAPL, "1A", 0, CURRENCY), chunk(AAPL, "1A", 1, SUPPLY)])
    hits = store.query("single-source suppliers and manufacturing partners", "AAPL", "acc-aapl-2025", ["1A"], 2)
    assert hits[0].id == "AAPL-10K-2025-1A-001"
    assert hits[0].distance < hits[1].distance


# ---------- Agent retrieval ----------

def test_retrieve_for_agents_dedupes_caps_and_formats(store: FilingStore) -> None:
    store.add_chunks(build_chunks(ten_k_text(), AAPL, store.embedder))
    filing = store.latest_filing("AAPL")
    assert filing is not None
    results = retrieve_for_agents("AAPL", store, filing, top_k=3)
    assert len(results) <= 3
    assert len({r.id for r in results}) == len(results)
    assert all(r.metadata["ticker"] == "AAPL" for r in results)
    text = results[0].for_prompt()
    assert text.startswith(f"[{results[0].id}] (Item ")


def test_retrieve_for_agents_covers_topics_before_filling(store: FilingStore) -> None:
    # Many near-identical risk chunks would win a pure distance sort; coverage-first keeps other sections
    risk = [chunk(AAPL, "1A", i, SUPPLY.replace("delay", f"delay {i}")) for i in range(6)]
    mdna = chunk(AAPL, "7", 0, "Net sales and gross margin increased, driven by Services revenue and segment growth.")
    market = chunk(AAPL, "7A", 0, CURRENCY)
    store.add_chunks(risk + [mdna, market])
    results = retrieve_for_agents("AAPL", store, store.latest_filing("AAPL"), top_k=3)  # type: ignore[arg-type]
    assert {r.metadata["section"] for r in results} == {"1A", "7", "7A"}


def test_get_filing_chunks_uses_indexed_filing_without_edgar(store: FilingStore) -> None:
    store.add_chunks(build_chunks(ten_k_text(), AAPL, store.embedder))

    class ExplodingEdgar:
        def latest_filing(self, *args: object) -> None:
            raise AssertionError("EDGAR must not be called when a filing is already indexed")

    chunks = get_filing_chunks("AAPL", store=store, edgar=ExplodingEdgar(), top_k=4)  # type: ignore[arg-type]
    assert 0 < len(chunks) <= 4
    assert all(c.startswith("[AAPL-10K-2025-") for c in chunks)


def test_get_filing_chunks_ingests_from_edgar_on_first_use(store: FilingStore, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from src.rag.edgar import FilingRef

    html_path = tmp_path / "aapl.htm"
    html_path.write_text("<html><body>" + "".join(f"<p>{p}</p>" for p in ten_k_text().split("\n\n")) + "</body></html>",
                         encoding="utf-8")

    class StubEdgar:
        calls = 0

        def latest_filing(self, ticker: str, form_type: str = "10-K") -> FilingRef:
            StubEdgar.calls += 1
            return FilingRef("AAPL", 320193, "acc-aapl-2025", "10-K", "2025-10-31", "2025-09-27", "aapl.htm")

        def download(self, ref: FilingRef):  # type: ignore[no-untyped-def]
            return html_path

    chunks = get_filing_chunks("AAPL", store=store, edgar=StubEdgar(), top_k=8)  # type: ignore[arg-type]
    assert chunks and StubEdgar.calls == 1
    assert store.latest_filing("AAPL")["accession"] == "acc-aapl-2025"  # type: ignore[index]
    # Second call uses the index, no new EDGAR lookup
    get_filing_chunks("AAPL", store=store, edgar=StubEdgar(), top_k=8)  # type: ignore[arg-type]
    assert StubEdgar.calls == 1


# ---------- Live (opt-in: pytest -m live) ----------

@pytest.mark.live
def test_live_aapl_risk_query_returns_only_risk_factors(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from src.rag.edgar import EdgarClient
    from src.rag.retriever import ingest_latest_from_edgar

    live_store = FilingStore(client=chromadb.PersistentClient(path=str(tmp_path / "chroma")))
    filing = ingest_latest_from_edgar("AAPL", live_store, EdgarClient(cache_dir=tmp_path / "filings"))
    hits = live_store.query("supply chain and manufacturing risks", "AAPL", filing["accession"], ["1A"], 5)
    assert len(hits) == 5
    assert all(h.metadata["section"] == "1A" and h.metadata["ticker"] == "AAPL" for h in hits)
    assert any("supplier" in h.text.lower() or "supply" in h.text.lower() for h in hits)
