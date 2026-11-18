import itertools
import re

import pytest
from corpus_core.hocr import parse_hocr
from corpus_core.normalise import dehyphenate, is_running_head, search_text
from corpus_core.passages import MIN_TAIL_WORDS, passages


def test_pages_from_hocr(hocr_bytes, page_numbers_bytes):
    pages = parse_hocr(hocr_bytes, page_numbers_bytes)
    assert [p.leaf for p in pages] == [0, 1, 2]
    p0, p1, p2 = pages
    assert p0.raw_text.startswith("AMERICAN GAS LIGHT JOURNAL. 161\n\nThe gas works at Noble")
    assert "were or-\nganized" in p0.raw_text  # raw keeps the line break exactly
    assert p0.printed_page == "161" and p1.printed_page is None and p2.printed_page == "163"
    assert p0.duplicate_of_leaf is None and p1.duplicate_of_leaf == 0
    assert p2.duplicate_of_leaf is None  # too short to call a duplicate
    assert p0.word_count == len(p0.raw_text.split())
    assert 80 < (p0.mean_conf or 0) < 100


def test_search_text_keeps_spelling_and_rejoins_breaks(hocr_bytes):
    raw = parse_hocr(hocr_bytes)[0].raw_text
    st = search_text(raw, drop_running_head=True)
    assert not st.startswith("AMERICAN")
    assert "organized" in st and "or- ganized" not in st
    assert "pipe line" in st and "pipeline" not in st
    assert "Gas-Light" in st  # capitalised continuation keeps its hyphen
    assert search_text(raw).startswith("AMERICAN")  # head only dropped when asked


@pytest.mark.parametrize(
    ("line", "head"),
    [
        ("AMERICAN GAS LIGHT JOURNAL. 161", True),
        ("162 THE OIL AND GAS JOURNAL", True),
        ("The gas works at Noble", False),
        ("CONTENTS", False),
    ],
)
def test_running_head(line, head):
    assert is_running_head(line) is head


def test_dehyphenate():
    assert dehyphenate("affirma-\ntive") == "affirmative"
    assert dehyphenate("Gas-\nLight") == "Gas-Light"


def _words(n: int) -> str:
    return "\n".join(" ".join(f"w{i}" for i in range(j, min(j + 7, n))) for j in range(0, n, 7))


@pytest.mark.parametrize("n", [1, 39, 250, 251, 290, 300, 1000, 1037])
def test_passages_cover_every_word_with_true_offsets(n):
    raw = _words(n)
    ps = passages(raw)
    words = raw.split()
    covered: set[int] = set()
    for p in ps:
        chunk = raw[p.char_start : p.char_end]
        toks = chunk.split()
        first = int(toks[0][1:])
        assert toks == words[first : first + len(toks)]
        assert p.word_count == len(toks)
        assert p.search_text == re.sub(r"\s+", " ", chunk)
        covered.update(range(first, first + len(toks)))
    assert covered == set(range(n))
    assert [p.seq for p in ps] == list(range(len(ps)))
    for a, b in itertools.pairwise(ps):
        assert b.char_start < a.char_end  # windows overlap
    if len(ps) > 1:  # a short tail is folded into the window before it
        assert ps[-1].word_count - 50 >= MIN_TAIL_WORDS


def test_empty_page_has_no_passages():
    assert passages("") == []


HOCR_HEADER = b"""<html><body>
<div class="ocr_page" title="bbox 0 0 9 9; ppageno 3"><p class="ocr_par">
<span class="ocr_header"><span class="ocrx_word" title="x_wconf 90">HEADLINE</span></span>
<span class="ocr_line"><span class="ocrx_word" title="x_wconf 90">Body</span></span>
<span class="ocr_caption"><span class="ocrx_word" title="x_wconf 90">Fig.</span></span>
</p></div>
<div class="ocr_page" title="bbox 0 0 9 9; ppageno 4"><p class="ocr_par">
<span class="ocr_textfloat"><span class="ocrx_word" title="x_wconf 90">Float</span></span>
</p></div></body></html>"""


def test_headers_captions_and_floats_are_kept():
    p = parse_hocr(HOCR_HEADER)
    assert p[0].raw_text == "HEADLINE\nBody\nFig." and p[1].raw_text == "Float"


def test_printed_numbers_match_by_position_when_counts_agree():
    # hOCR numbers its pages 3 and 4; the page-number file uses scan leaves 7 and 8
    numbers = b'{"pages":[{"leafNum":7,"pageNumber":"12"},{"leafNum":8,"pageNumber":""}]}'
    p = parse_hocr(HOCR_HEADER, numbers)
    assert [x.printed_page for x in p] == ["12", None]


def test_printed_numbers_fall_back_to_leaf_match():
    numbers = b'{"pages":[{"leafNum":4,"pageNumber":"9"}]}'
    assert [x.printed_page for x in parse_hocr(HOCR_HEADER, numbers)] == [None, "9"]


def test_leaf_comes_from_page_id_when_ppageno_is_always_zero():
    real = b"""<html><body>
    <div class="ocr_page" id="page_000000" title="bbox 0 0 9 9; ppageno 0"></div>
    <div class="ocr_page" id="page_000001" title="bbox 0 0 9 9; ppageno 0"></div>
    <div class="ocr_page" id="page_000002" title="bbox 0 0 9 9; ppageno 0"></div>
    </body></html>"""
    numbers = b'{"pages":[{"leafNum":1,"pageNumber":"7"}]}'
    pages = parse_hocr(real, numbers)
    assert [p.leaf for p in pages] == [0, 1, 2]
    assert [p.printed_page for p in pages] == [None, "7", None]


def test_colliding_leaf_numbers_fail_loudly():
    bad = b"""<html><body><div class="ocr_page" title="ppageno 1"></div>
    <div class="ocr_page" title="bbox 0 0 1 1"></div></body></html>"""
    with pytest.raises(ValueError, match="not unique"):
        parse_hocr(bad)
