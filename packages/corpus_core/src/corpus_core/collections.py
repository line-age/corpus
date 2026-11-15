"""The collections corpus ingests, and how to recognise their main run.

Titles change over a run's life (American Gas-Light Journal -> American Gas Journal ->
Pipeline & Gas Journal; The Oil Weekly -> World Oil). An item whose title matches none of its
collection's patterns is classified `other` and listed for a person to look at.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Collection:
    id: str
    name: str
    publisher_pattern: re.Pattern[str]
    main_titles: tuple[re.Pattern[str], ...]
    authority_tier: int  # 1 = primary, 2 = secondary


def _rx(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


COLLECTIONS: dict[str, Collection] = {
    c.id: c
    for c in (
        Collection(
            id="pub_pipeline-gas-journal",
            name="American Gas Light Journal / American Gas Journal / Pipeline & Gas Journal",
            publisher_pattern=re.compile(r"oildom", re.IGNORECASE),
            main_titles=_rx(
                r"american gas[- ]light journal",
                r"american gas journal",
                r"american gas engineering journal",
                r"pipe ?line (and|&) gas journal",
                r"mining and petroleum standard and american gas[- ]light journal",
            ),
            authority_tier=1,
        ),
        Collection(
            id="pub_oil-gas-journal",
            name="The Oil & Gas Journal",
            publisher_pattern=re.compile(r"pennwell", re.IGNORECASE),
            main_titles=_rx(r"oil (and|&) gas journal", r"oil investors'? journal"),
            authority_tier=1,
        ),
        Collection(
            id="pub_world-oil",
            name="World Oil",
            publisher_pattern=re.compile(r"gulf publishing", re.IGNORECASE),
            main_titles=_rx(r"world oil", r"the oil weekly", r"oil weekly"),
            authority_tier=2,
        ),
    )
}

# Never ingested: the British Gas Journal (Benn Publications, London). Legend decision 0015.
EXCLUDED_COLLECTIONS = frozenset({"pub_gas-journal"})
