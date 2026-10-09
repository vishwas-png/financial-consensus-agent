"""Ingestion tests: text extraction, 10-K sections, semantic chunking, ids and metadata."""

from pathlib import Path

from fpdf import FPDF

from src.rag.ingest import (
    MAX_CHUNK_CHARS,
    FilingInfo,
    build_chunks,
    chunk_id,
    extract_text_from_html,
    extract_text_from_pdf,
    semantic_chunk,
    split_into_sections,
    split_paragraphs,
)
from tests.fakes import BagOfWordsEmbedder

INFO = FilingInfo(ticker="AAPL", form_type="10-K", fiscal_year=2025, filing_date="2025-10-31",
                  accession="0000320193-25-000079", source="edgar")

SUPPLY = ("The Company depends on outsourcing partners and single-source suppliers for components; "
          "supply chain disruptions at manufacturing partners could delay shipments of products.")
CURRENCY = ("Foreign currency exchange rates and interest rates fluctuate; the Company hedges "
            "currency exposure with forward contracts, but rates could still reduce reported revenue.")


def ten_k_text() -> str:
    """A tiny 10-K: table of contents first (headings repeated), then the real sections."""
    return "\n\n".join([
        "TABLE OF CONTENTS",
        "Item 1.\nBusiness", "Item 1A.\nRisk Factors", "Item 2.\nProperties", "Item 7.\nManagement's Discussion",
        "PART I",
        "Item 1. Business",
        "The Company designs, manufactures and markets smartphones, personal computers, tablets, "
        "wearables and accessories, and sells a variety of related services worldwide.",
        "Item 1A. Risk Factors",
        SUPPLY, SUPPLY.replace("delay", "interrupt"), SUPPLY.replace("components", "parts and components"),
        CURRENCY, CURRENCY.replace("reduce", "lower"), CURRENCY.replace("forward contracts", "options"),
        "Item 2. Properties",
        "The Company's headquarters are located in Cupertino, California, and it owns and leases "
        "facilities and land for corporate functions, research and data centers.",
        "Item 7. Management's Discussion and Analysis",
        "Net sales increased during the year driven by higher Services revenue and iPhone sales, "
        "partially offset by lower Mac net sales in several geographic segments.",
    ])


# ---------- Text extraction ----------

def test_html_extraction_drops_hidden_xbrl_scripts_and_nbsp() -> None:
    html = (
        "<html><body>"
        "<ix:header><ix:hidden>HIDDEN-XBRL-FACT</ix:hidden></ix:header>"
        "<div><span>Item 1A.</span></div>"
        "<p>Risk&#160;text continues <b>here</b>.</p>"
        "<script>var x = 1;</script><style>p {color: red}</style>"
        "</body></html>"
    )
    text = extract_text_from_html(html)
    assert "HIDDEN-XBRL-FACT" not in text
    assert "var x" not in text and "color" not in text
    assert "Risk text continues here ." in text or "Risk text continues here." in text
    assert "\xa0" not in text
    # Block elements become separate paragraphs
    assert text.index("Item 1A.") < text.index("Risk text")
    assert "\n\n" in text


def test_page_footers_removed() -> None:
    html = "<p>First paragraph about products.</p><p>Apple Inc. | 2025 Form 10-K | 8</p><p>Second.</p>"
    text = extract_text_from_html(html)
    assert "Form 10-K |" not in text
    assert "First paragraph" in text and "Second." in text


def test_pdf_extraction(tmp_path: Path) -> None:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    pdf.multi_cell(0, 8, "Item 1A. Risk Factors\n\nThe Company depends on suppliers for components.")
    path = tmp_path / "aapl_10k.pdf"
    pdf.output(str(path))

    text = extract_text_from_pdf(path)
    assert "Item 1A. Risk Factors" in text
    assert "depends on suppliers" in text


# ---------- Sections ----------

def test_sections_use_real_headings_not_table_of_contents() -> None:
    sections = split_into_sections(ten_k_text())
    assert [s.item for s in sections] == ["1", "1A", "2", "7"]
    risk = next(s for s in sections if s.item == "1A")
    assert "single-source suppliers" in risk.text
    assert "Cupertino" not in risk.text  # stops at the next item
    business = next(s for s in sections if s.item == "1")
    assert business.text.startswith("The Company designs")


def test_heading_with_period_on_next_line() -> None:
    sections = split_into_sections("Item 12\n. Security Ownership\n\nOwnership details here.")
    assert sections[0].item == "12"
    assert "Ownership details" in sections[0].text


# ---------- Paragraphs ----------

def test_short_paragraphs_merge_forward_and_page_numbers_dropped() -> None:
    text = "Competition\n\n" + SUPPLY + "\n\n12\n\n" + CURRENCY
    paragraphs = split_paragraphs(text)
    assert paragraphs[0].startswith("Competition The Company depends")
    assert "12" not in paragraphs
    assert len(paragraphs) == 2


def test_long_paragraph_split_at_sentences() -> None:
    long_paragraph = " ".join([SUPPLY] * 12)
    pieces = split_paragraphs(long_paragraph, max_chars=500)
    assert len(pieces) > 1
    assert all(len(p) <= 500 for p in pieces)
    assert all(p.endswith(".") for p in pieces)


# ---------- Semantic chunking ----------

def test_topic_change_starts_new_chunk() -> None:
    paragraphs = [SUPPLY, SUPPLY.replace("delay", "interrupt"), SUPPLY.replace("components", "parts"),
                  CURRENCY, CURRENCY.replace("reduce", "lower"), CURRENCY.replace("forward", "option")]
    chunks = semantic_chunk(paragraphs, BagOfWordsEmbedder())
    assert len(chunks) == 2
    assert "suppliers" in chunks[0] and "currency" not in chunks[0].lower()
    assert "currency" in chunks[1].lower() and "suppliers" not in chunks[1]


def test_size_cap_respected_with_overlap_on_same_topic() -> None:
    paragraphs = [SUPPLY.replace("delay", f"delay batch {i}") for i in range(8)]
    chunks = semantic_chunk(paragraphs, BagOfWordsEmbedder(), max_chars=450)
    assert len(chunks) > 2
    assert all(len(c) <= 450 for c in chunks)
    # Size-based breaks repeat the previous paragraph so context isn't cut mid-topic
    for first, second in zip(chunks, chunks[1:]):
        assert first.split("\n\n")[-1] == second.split("\n\n")[0]


def test_uniform_topic_has_no_topic_breaks() -> None:
    paragraphs = [SUPPLY] * 4  # identical similarities everywhere
    assert semantic_chunk(paragraphs, BagOfWordsEmbedder(), max_chars=5000) == ["\n\n".join(paragraphs)]


def test_tiny_chunk_merged_into_neighbour() -> None:
    paragraphs = [SUPPLY, SUPPLY.replace("delay", "interrupt"), CURRENCY, "Hedging uses forward contracts."]
    chunks = semantic_chunk(paragraphs, BagOfWordsEmbedder())
    assert all(len(c) >= 100 for c in chunks)
    assert any("Hedging uses forward contracts." in c for c in chunks)


def test_empty_and_single_paragraph() -> None:
    assert semantic_chunk([], BagOfWordsEmbedder()) == []
    assert semantic_chunk([SUPPLY], BagOfWordsEmbedder()) == [SUPPLY]


# ---------- Chunks: ids + metadata ----------

def test_chunk_id_format() -> None:
    assert chunk_id(INFO, "1A", 7) == "AAPL-10K-2025-1A-007"


def test_build_chunks_only_indexed_sections_with_metadata() -> None:
    chunks = build_chunks(ten_k_text(), INFO, BagOfWordsEmbedder())
    sections = {c.metadata["section"] for c in chunks}
    assert sections == {"1", "1A", "7"}  # Item 2 (Properties) is not indexed
    risk = [c for c in chunks if c.metadata["section"] == "1A"]
    assert [c.id for c in risk] == [f"AAPL-10K-2025-1A-{i:03d}" for i in range(len(risk))]
    meta = risk[0].metadata
    assert meta["ticker"] == "AAPL" and meta["form_type"] == "10-K" and meta["fiscal_year"] == 2025
    assert meta["accession"] == "0000320193-25-000079" and meta["source"] == "edgar"
    assert meta["section_title"] == "Risk Factors" and meta["chunk_index"] == 0
    assert all(len(c.text) <= MAX_CHUNK_CHARS for c in chunks)


def test_build_chunks_is_deterministic() -> None:
    first = build_chunks(ten_k_text(), INFO, BagOfWordsEmbedder())
    second = build_chunks(ten_k_text(), INFO, BagOfWordsEmbedder())
    assert [(c.id, c.text) for c in first] == [(c.id, c.text) for c in second]
