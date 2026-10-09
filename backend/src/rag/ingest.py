"""Turn an SEC filing (EDGAR HTML or a PDF) into metadata-tagged chunks.

Pipeline:
1. extract plain text (HTML/inline-XBRL via BeautifulSoup, or PDF via pypdf);
2. split the 10-K into its "Item" sections (1, 1A, 7, 7A, ...);
3. inside each section, split into paragraphs and group them into chunks with
   SEMANTIC chunking: start a new chunk where the topic changes (embedding
   similarity between neighbouring paragraphs drops) or the size cap is hit;
4. give each chunk a deterministic id + metadata for pre-filtered retrieval.

Usage (manual PDF ingestion):  python -m src.rag.ingest <file.pdf> <TICKER> <FISCAL_YEAR>
"""

import math
import re
import sys
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from pypdf import PdfReader

from src.rag.embeddings import Embedder

# 10-K sections we index. Others (properties, exhibits, financial statement tables, ...)
# add noise and tokens without helping the debate.
INDEXED_SECTIONS: dict[str, str] = {
    "1": "Business",
    "1A": "Risk Factors",
    "1C": "Cybersecurity",
    "3": "Legal Proceedings",
    "7": "Management's Discussion and Analysis",
    "7A": "Quantitative and Qualitative Disclosures About Market Risk",
}

MAX_CHUNK_CHARS = 1200      # hard size cap per chunk
MIN_PARAGRAPH_CHARS = 80    # shorter paragraphs (sub-headings) merge into the next one
MIN_CHUNK_CHARS = 250       # shorter chunks merge into the previous chunk when it still fits
BREAKPOINT_PERCENTILE = 20  # lowest 20% of neighbour similarities count as topic changes

# "Item 1A." / "Item 7" at the start of a line, optionally followed by the title.
# Some filings put the period on the next line ("Item 12\n."), so allow that.
ITEM_HEADING = re.compile(r"(?im)^[ \t]*item[ \t]+(\d{1,2}[a-c]?)[ \t]*\n?[ \t]*\.?[ \t]*(.*)$")


@dataclass(frozen=True)
class Section:
    item: str    # e.g. "1A"
    title: str
    text: str


@dataclass(frozen=True)
class FilingInfo:
    """Metadata shared by every chunk of one filing."""

    ticker: str
    form_type: str      # "10-K"
    fiscal_year: int
    filing_date: str    # ISO date, or "" if unknown (manual PDFs)
    accession: str      # EDGAR accession, or "pdf-<filename>" for manual PDFs
    source: str         # "edgar" or "pdf"


@dataclass(frozen=True)
class Chunk:
    id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------- 1. Text extraction ----------

# Page footers like "Apple Inc. | 2025 Form 10-K | 8" would otherwise end up inside chunks
PAGE_FOOTER = re.compile(r"(?im)^[^\n|]{1,80}\|\s*\d{4}\s+Form\s+10-K\s*\|\s*\d+\s*$")


def _normalize_whitespace(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = PAGE_FOOTER.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_text_from_html(html: str) -> str:
    """Plain text from an EDGAR 10-K (HTML / inline XBRL)."""
    # EDGAR inline-XBRL files are XHTML; lxml parses them fine as HTML, so silence the warning.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(html, "lxml")
    # Hidden XBRL header data, scripts and styles are not part of the readable text
    for tag in soup.find_all(["ix:header", "script", "style"]):
        tag.decompose()
    # Block elements become paragraph breaks; inline elements are joined with spaces
    for block in soup.find_all(["p", "div", "tr", "li", "h1", "h2", "h3", "h4", "br"]):
        block.insert_after("\n\n")
    return _normalize_whitespace(soup.get_text(" "))


def extract_text_from_pdf(path: Path) -> str:
    """Plain text from a PDF filing (page by page)."""
    reader = PdfReader(str(path))
    pages = [page.extract_text() or "" for page in reader.pages]
    return _normalize_whitespace("\n\n".join(pages))


# ---------- 2. Sections ----------

def split_into_sections(text: str) -> list[Section]:
    """Split a 10-K into Item sections.

    Each heading appears twice: in the table of contents and at the real section start.
    The LAST occurrence of each item is the real one, so we keep that.
    """
    last_heading: dict[str, tuple[int, int, str]] = {}  # item -> (start, end_of_heading, title)
    for match in ITEM_HEADING.finditer(text):
        item = match.group(1).upper()
        last_heading[item] = (match.start(), match.end(), match.group(2).strip())

    ordered = sorted(last_heading.items(), key=lambda kv: kv[1][0])
    sections = []
    for i, (item, (_, body_start, title)) in enumerate(ordered):
        body_end = ordered[i + 1][1][0] if i + 1 < len(ordered) else len(text)
        sections.append(Section(item=item, title=title, text=text[body_start:body_end].strip()))
    return sections


# ---------- 3. Paragraphs + semantic chunking ----------

def split_paragraphs(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Paragraphs separated by blank lines. Short ones merge forward; long ones split by sentence."""
    raw = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    raw = [p for p in raw if not p.isdigit()]  # bare page numbers

    merged: list[str] = []
    carry = ""
    for paragraph in raw:
        paragraph = f"{carry} {paragraph}".strip() if carry else paragraph
        if len(paragraph) < MIN_PARAGRAPH_CHARS:
            carry = paragraph  # e.g. a sub-heading: keep it with the paragraph after it
            continue
        carry = ""
        merged.append(paragraph)
    if carry:
        merged.append(carry)

    result: list[str] = []
    for paragraph in merged:
        result.extend(_split_long(paragraph, max_chars) if len(paragraph) > max_chars else [paragraph])
    return result


def _split_long(paragraph: str, max_chars: int) -> list[str]:
    """Split an over-long paragraph at sentence boundaries into pieces <= max_chars."""
    sentences = re.split(r"(?<=[.!?])\s+", paragraph)
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        while len(sentence) > max_chars:  # a single giant "sentence": hard cut
            pieces.append(sentence[:max_chars])
            sentence = sentence[max_chars:]
        if current and len(current) + 1 + len(sentence) > max_chars:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(pct / 100 * len(ordered)) - 1))
    return ordered[index]


def semantic_chunk(paragraphs: list[str], embedder: Embedder, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Group consecutive paragraphs into chunks.

    A new chunk starts when:
    - the similarity between a paragraph and the previous one is in the lowest
      BREAKPOINT_PERCENTILE of this section AND below the section's median
      (a topic change; the median check means a section that stays on one topic
      has no topic breaks), or
    - adding the paragraph would exceed max_chars.
    Only size-based breaks repeat the last paragraph as overlap (same topic continues);
    topic breaks start clean.
    """
    if not paragraphs:
        return []
    if len(paragraphs) == 1:
        return paragraphs

    vectors = embedder.embed(paragraphs)
    similarities = [_cosine(vectors[i - 1], vectors[i]) for i in range(1, len(paragraphs))]
    threshold = _percentile(similarities, BREAKPOINT_PERCENTILE)
    median = _percentile(similarities, 50)

    chunks: list[str] = []
    current = [paragraphs[0]]
    for i in range(1, len(paragraphs)):
        paragraph = paragraphs[i]
        topic_change = similarities[i - 1] <= threshold and similarities[i - 1] < median
        too_big = len("\n\n".join(current + [paragraph])) > max_chars
        if topic_change or too_big:
            chunks.append("\n\n".join(current))
            overlap = current[-1]
            use_overlap = too_big and not topic_change and len(overlap) + 2 + len(paragraph) <= max_chars
            current = [overlap, paragraph] if use_overlap else [paragraph]
        else:
            current.append(paragraph)
    chunks.append("\n\n".join(current))
    return _merge_small_chunks(chunks, max_chars)


def _merge_small_chunks(chunks: list[str], max_chars: int) -> list[str]:
    """A one-sentence chunk retrieves poorly and wastes a top-k slot: fold it into a neighbour."""
    merged: list[str] = []
    for chunk in chunks:
        if merged and len(chunk) < MIN_CHUNK_CHARS and len(merged[-1]) + 2 + len(chunk) <= max_chars:
            merged[-1] = f"{merged[-1]}\n\n{chunk}"
        elif merged and len(merged[-1]) < MIN_CHUNK_CHARS and len(merged[-1]) + 2 + len(chunk) <= max_chars:
            merged[-1] = f"{merged[-1]}\n\n{chunk}"  # previous one was small: grow it with this one
        else:
            merged.append(chunk)
    return merged


# ---------- 4. Chunks with ids + metadata ----------

def chunk_id(info: FilingInfo, section: str, index: int) -> str:
    """Deterministic id, e.g. AAPL-10K-2025-1A-007. Same filing -> same ids (safe re-ingestion)."""
    form = info.form_type.replace("-", "")
    return f"{info.ticker}-{form}-{info.fiscal_year}-{section}-{index:03d}"


def build_chunks(text: str, info: FilingInfo, embedder: Embedder) -> list[Chunk]:
    """Full pipeline from filing text to chunks for the indexed sections."""
    chunks: list[Chunk] = []
    for section in split_into_sections(text):
        if section.item not in INDEXED_SECTIONS:
            continue
        pieces = semantic_chunk(split_paragraphs(section.text), embedder)
        for index, piece in enumerate(pieces):
            chunks.append(Chunk(
                id=chunk_id(info, section.item, index),
                text=piece,
                metadata={
                    "ticker": info.ticker,
                    "form_type": info.form_type,
                    "fiscal_year": info.fiscal_year,
                    "filing_date": info.filing_date,
                    "accession": info.accession,
                    "source": info.source,
                    "section": section.item,
                    "section_title": INDEXED_SECTIONS[section.item],
                    "chunk_index": index,
                },
            ))
    return chunks


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python -m src.rag.ingest <file.pdf> <TICKER> <FISCAL_YEAR>")
        sys.exit(2)
    from src.rag.retriever import FilingStore

    pdf_path, ticker_arg, year_arg = Path(sys.argv[1]), sys.argv[2].upper(), int(sys.argv[3])
    pdf_info = FilingInfo(ticker=ticker_arg, form_type="10-K", fiscal_year=year_arg,
                          filing_date="", accession=f"pdf-{pdf_path.name}", source="pdf")
    store = FilingStore()
    count = store.add_chunks(build_chunks(extract_text_from_pdf(pdf_path), pdf_info, store.embedder))
    print(f"Indexed {count} chunks from {pdf_path.name} for {ticker_arg} FY{year_arg}")
