"""Search text derived from raw OCR. Raw text is never changed; spelling is never changed.

`pipe line` stays `pipe line`: the era vocabulary decides equivalence at query time.
"""

import re

_WS = re.compile(r"\s+")
_CAPS = re.compile(r"[A-Z]")
_LETTERS = re.compile(r"[A-Za-z]")
_DIGITS = re.compile(r"\d")


def is_running_head(line: str) -> bool:
    """A page's first line that looks like a running head: short, mostly capitals, with a page
    number or the word JOURNAL. Deliberately conservative; a missed head costs little."""
    letters = _LETTERS.findall(line)
    if not letters or len(line.split()) > 14:
        return False
    caps_share = len(_CAPS.findall(line)) / len(letters)
    return caps_share >= 0.6 and (
        bool(_DIGITS.search(line)) or "JOURNAL" in line.upper()
    )


def dehyphenate(text: str) -> str:
    """Rejoin words split across a line break. A lower-case continuation is one word broken by
    the compositor (`or-\\nganized` -> `organized`); a capitalised one is a real compound and
    keeps its hyphen (`Gas-\\nLight` -> `Gas-Light`)."""
    text = re.sub(r"(\w)-\n([a-z])", r"\1\2", text)
    return re.sub(r"(\w)-\n([A-Z0-9])", r"\1-\2", text)


def search_text(raw: str, drop_running_head: bool = False) -> str:
    text = raw
    if drop_running_head:
        first, sep, rest = text.partition("\n")
        if sep and is_running_head(first):
            text = rest
    text = dehyphenate(text)
    return _WS.sub(" ", text).strip()
