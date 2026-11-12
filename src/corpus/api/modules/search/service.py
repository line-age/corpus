"""Search logic: turns a query into ranked passages."""

from corpus.api.modules.search.repository import SearchRepository


class SearchService:
    def __init__(self, repository: SearchRepository) -> None:
        self._repository = repository
