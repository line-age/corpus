"""Overlapping word windows over a page's raw text, with character offsets into it."""

import re
from dataclasses import dataclass

from corpus_core.normalise import search_text

_WORD = re.compile(r"\S+")

WINDOW_WORDS = 250
OVERLAP_WORDS = 50
MIN_TAIL_WORDS = 40  # a shorter last window is folded into the one before


@dataclass(frozen=True)
class Passage:
    seq: int
    char_start: int
    char_end: int
    word_count: int
    search_text: str


def passages(raw: str, window: int = WINDOW_WORDS, overlap: int = OVERLAP_WORDS) -> list[Passage]:
    if not 0 <= overlap < window:
        raise ValueError("overlap must be smaller than the window")
    spans = [m.span() for m in _WORD.finditer(raw)]
    if not spans:
        return []
    step = window - overlap
    starts = list(range(0, len(spans), step))
    # a last window adding fewer than MIN_TAIL_WORDS new words is folded into the one before
    while len(starts) > 1 and len(spans) - starts[-1] - overlap < MIN_TAIL_WORDS:
        starts.pop()
    out: list[Passage] = []
    for seq, s in enumerate(starts):
        e = len(spans) if seq == len(starts) - 1 else min(s + window, len(spans))
        a, b = spans[s][0], spans[e - 1][1]
        out.append(Passage(seq, a, b, e - s, search_text(raw[a:b])))
    return out
