"""Errors raised while ingesting an item. Their messages are written to the log."""


class IngestError(Exception):
    """Ingesting an item failed for a known reason, given in the message."""
    pass


class FetchError(IngestError):
    """A source file could not be downloaded or decoded."""
    pass


class InvalidSourceData(IngestError):
    """The source files were downloaded but are missing data or contradict each other."""
    pass
