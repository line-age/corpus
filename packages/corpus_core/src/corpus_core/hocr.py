"""Pages from Internet Archive hOCR and page-number files.

The raw text of a page is rebuilt from hOCR words: words joined by spaces, lines by newlines,
paragraphs by a blank line. That string is what corpus quotes, and passage offsets point into it.
"""

import hashlib
import json
import re
from dataclasses import dataclass

from lxml import html as lxml_html

_WCONF = re.compile(r"x_wconf\s+(\d+)")
_PPAGENO = re.compile(r"ppageno\s+(\d+)")
_WS = re.compile(r"\s+")

MIN_WORDS_FOR_DUPLICATE = 20  # near-blank pages share hashes and are not duplicates

# Tesseract writes text lines under several classes; all of them hold words we must keep.
_LINE_XPATH = (
    './/*[contains(@class, "ocr_line") or contains(@class, "ocr_header")'
    ' or contains(@class, "ocr_caption") or contains(@class, "ocr_textfloat")]'
)


@dataclass(frozen=True)
class Page:
    leaf: int
    raw_text: str
    word_count: int
    mean_conf: float | None
    printed_page: str | None
    text_hash: str
    duplicate_of_leaf: int | None = None


def text_hash(text: str) -> str:
    return hashlib.sha256(_WS.sub(" ", text).strip().encode()).hexdigest()


def parse_page_numbers(data: bytes | None) -> list[tuple[int, str | None]]:
    """(leafNum, printed page or None) for every page entry, in leaf order."""
    if not data:
        return []
    doc = json.loads(data)
    out = [
        (int(p["leafNum"]), str(p.get("pageNumber") or "").strip() or None)
        for p in doc.get("pages", [])
        if p.get("leafNum") is not None
    ]
    return sorted(out)


def _printed_for(leaves: list[int], numbers: list[tuple[int, str | None]]) -> dict[int, str]:
    """Map hOCR leaves to printed numbers. When both files list the same number of pages they
    describe the same pages in the same order, so match by position; that survives leaves the
    hOCR numbers differently from the scan. Otherwise fall back to matching leaf numbers."""
    if len(numbers) == len(leaves):
        return {leaf: n for leaf, (_, n) in zip(leaves, numbers, strict=True) if n}
    by_leaf = {leaf: n for leaf, n in numbers if n}
    return {leaf: by_leaf[leaf] for leaf in leaves if leaf in by_leaf}


def parse_hocr(hocr: bytes, page_numbers: bytes | None = None) -> list[Page]:
    root = lxml_html.fromstring(hocr)
    page_els = root.xpath('//*[contains(@class, "ocr_page")]')
    leaves: list[int] = []
    for idx, page_el in enumerate(page_els):
        m = _PPAGENO.search(page_el.get("title", ""))
        leaves.append(int(m.group(1)) if m else idx)
    if len(set(leaves)) != len(leaves):
        raise ValueError("hOCR leaf numbers are not unique")
    printed = _printed_for(leaves, parse_page_numbers(page_numbers))
    pages: list[Page] = []
    seen: dict[str, int] = {}
    for leaf, page_el in zip(leaves, page_els, strict=True):
        confs: list[int] = []
        pars: list[str] = []
        for par in page_el.xpath('.//*[contains(@class, "ocr_par")]'):
            lines: list[str] = []
            for line in par.xpath(_LINE_XPATH):
                words: list[str] = []
                for w in line.xpath('.//*[contains(@class, "ocrx_word")]'):
                    t = (w.text_content() or "").strip()
                    if not t:
                        continue
                    words.append(t)
                    c = _WCONF.search(w.get("title", ""))
                    if c:
                        confs.append(int(c.group(1)))
                if words:
                    lines.append(" ".join(words))
            if lines:
                pars.append("\n".join(lines))
        raw = "\n\n".join(pars)
        n_words = sum(len(p.split()) for p in pars)
        h = text_hash(raw)
        dup = seen.get(h) if n_words >= MIN_WORDS_FOR_DUPLICATE else None
        if dup is None and n_words >= MIN_WORDS_FOR_DUPLICATE:
            seen[h] = leaf
        pages.append(
            Page(
                leaf=leaf,
                raw_text=raw,
                word_count=n_words,
                mean_conf=round(sum(confs) / len(confs), 2) if confs else None,
                printed_page=printed.get(leaf),
                text_hash=h,
                duplicate_of_leaf=dup,
            )
        )
    return pages
