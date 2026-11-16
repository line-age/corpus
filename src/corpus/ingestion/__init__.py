"""Ingest: fetch scanned publications and store their OCR text, one row per page.

Run it from the command line (`make ingest ITEM=<identifier>` or
`make ingest ARGS="--collection <name> ..."`); see __main__.py for the options:

    python -m corpus.ingestion <identifier> [<identifier> ...]
    python -m corpus.ingestion --collection <name> [--from/--to YYYY-MM-DD] [--sample N]

The steps, one module each:

- client.py      lists collections and fetches an item's metadata and OCR files
                 (network only)
- parsing.py     turns them into a document and its pages (pure functions)
- repository.py  writes a document and its pages, replacing any earlier copy (SQL only)
- service.py     runs the three for one identifier, storing it in one transaction
"""
