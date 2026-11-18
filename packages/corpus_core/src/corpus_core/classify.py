"""Item kinds. Every item is kept; the kind decides whether it is indexed."""

import re
from enum import StrEnum

from corpus_core.collections import COLLECTIONS


class Kind(StrEnum):
    ISSUE = "issue"
    INDEX = "index"
    SUPPLEMENT = "supplement"
    OTHER = "other"  # a title outside the collection's main run; a person decides


_INDEX = re.compile(r"(^|_)index($|_)|\bindex\b", re.IGNORECASE)
_SUPPLEMENT = re.compile(r"supplement", re.IGNORECASE)

INDEXED_KINDS = frozenset({Kind.ISSUE, Kind.INDEX})


def classify(collection: str, identifier: str, title: str) -> Kind:
    if _INDEX.search(identifier) or _INDEX.search(title):
        return Kind.INDEX
    if _SUPPLEMENT.search(identifier) or _SUPPLEMENT.search(title):
        return Kind.SUPPLEMENT
    spec = COLLECTIONS.get(collection)
    if spec is not None and any(p.search(title) for p in spec.main_titles):
        return Kind.ISSUE
    return Kind.OTHER
