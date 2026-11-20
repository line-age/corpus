"""Command line: ingest archive.org items into the configured database.

    python -m corpus.ingestion <identifier> [<identifier> ...]
    python -m corpus.ingestion --collection pub_world-oil --to 1919-12-31 --sample 50

Items can be named directly, or taken from whole collections, optionally limited to a
date range and thinned to an evenly spaced sample. Several items are downloaded at once
(--concurrency). With --skip-existing, items already stored are left alone, so an
interrupted run can be repeated to resume it.

Each item is stored in its own transaction, replacing any earlier copy, so a failed item
leaves the others (and its own earlier copy) in place. Exits 0 if every item was
ingested, 1 if any failed (or a collection could not be listed) and 2 for bad arguments.
"""

import argparse
import asyncio
import sys
import time
from collections.abc import Sequence
from datetime import date

from corpus.config import Settings, get_settings
from corpus.db import make_engine, make_sessionmaker
from corpus.ingestion.client import ArchiveOrgClient, is_valid_identifier
from corpus.ingestion.exceptions import IngestError
from corpus.ingestion.service import IngestService
from corpus.lib.logger import configure_logging, logger

MAX_CONCURRENCY = 16


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level)
    return asyncio.run(_run(args, settings))


def evenly_spaced[T](items: Sequence[T], count: int) -> list[T]:
    """`count` items spread evenly from the first to the last, or all of them if fewer."""
    if count >= len(items):
        return list(items)
    if count == 1:
        return [items[0]]
    step = (len(items) - 1) / (count - 1)
    return [items[round(i * step)] for i in range(count)]


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m corpus.ingestion",
        description="Ingest archive.org items' OCR text, one row per scanned page.",
    )
    parser.add_argument(
        "identifiers",
        nargs="*",
        metavar="identifier",
        type=_identifier,
        help="archive.org item identifier, e.g. sim_oil-gas-journal_1951-06-07_50_5",
    )
    parser.add_argument(
        "--collection",
        action="append",
        default=[],
        type=_identifier,
        dest="collections",
        help="also ingest the items of this archive.org collection (repeatable)",
    )
    parser.add_argument(
        "--from",
        dest="date_from",
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="only collection items dated on or after this day",
    )
    parser.add_argument(
        "--to",
        dest="date_to",
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="only collection items dated on or before this day",
    )
    parser.add_argument(
        "--sample",
        type=_positive,
        metavar="N",
        help="only N items per collection, evenly spaced across its date range",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="leave items that are already stored alone (resumes an interrupted run)",
    )
    parser.add_argument(
        "--concurrency",
        type=_positive,
        default=4,
        metavar="N",
        help=f"items to download at once, at most {MAX_CONCURRENCY} (default 4)",
    )
    args = parser.parse_args(argv)
    if not args.identifiers and not args.collections:
        parser.error("name at least one identifier or --collection")
    if not args.collections and (args.date_from or args.date_to or args.sample):
        parser.error("--from, --to and --sample apply only with --collection")
    if args.date_from and args.date_to and args.date_from > args.date_to:
        parser.error("--from is after --to")
    if args.concurrency > MAX_CONCURRENCY:
        parser.error(f"--concurrency is at most {MAX_CONCURRENCY}")
    return args


def _identifier(value: str) -> str:
    if not is_valid_identifier(value):
        raise argparse.ArgumentTypeError(
            f"{value!r} is not an archive.org identifier (letters, digits, '.', '_', '-')"
        )
    return value


def _positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError(f"{value!r} is not a positive whole number")
    return number


async def _run(args: argparse.Namespace, settings: Settings) -> int:
    client = ArchiveOrgClient(
        settings.archive_org_base_url, settings.ingest_http_timeout_seconds
    )
    identifiers = list(args.identifiers)
    listing_failed = False
    for collection in args.collections:
        try:
            found = await asyncio.to_thread(
                client.list_collection,
                collection,
                date_from=args.date_from,
                date_to=args.date_to,
            )
        except IngestError as e:
            logger.error(
                "listing %s failed: %s", collection, e, extra={"collection": collection}
            )
            listing_failed = True
            continue
        chosen = evenly_spaced(found, args.sample) if args.sample else found
        logger.info(
            "collection %s: %d items, %d chosen",
            collection,
            len(found),
            len(chosen),
            extra={
                "collection": collection,
                "found": len(found),
                "chosen": len(chosen),
            },
        )
        identifiers.extend(chosen)
    identifiers = list(dict.fromkeys(identifiers))  # drop repeats, keep order

    engine = make_engine(settings)
    try:
        service = IngestService(client, make_sessionmaker(engine))
        if args.skip_existing:
            existing = await service.already_ingested(identifiers)
            if existing:
                logger.info(
                    "skipping %d items already stored",
                    len(existing),
                    extra={"skipped": len(existing)},
                )
            identifiers = [i for i in identifiers if i not in existing]
        failed = await _ingest_all(service, identifiers, args.concurrency)
    finally:
        await engine.dispose()

    succeeded = len(identifiers) - failed
    logger.info(
        "ingest finished: %d succeeded, %d failed",
        succeeded,
        failed,
        extra={"succeeded": succeeded, "failed": failed},
    )
    return 1 if failed or listing_failed else 0


async def _ingest_all(
    service: IngestService, identifiers: list[str], concurrency: int
) -> int:
    """Ingest every item, `concurrency` at a time. Returns how many failed."""
    limit = asyncio.Semaphore(concurrency)

    async def one(identifier: str) -> bool:
        async with limit:
            return await _ingest_one(service, identifier)

    results = await asyncio.gather(*(one(identifier) for identifier in identifiers))
    return results.count(False)


async def _ingest_one(service: IngestService, identifier: str) -> bool:
    """Ingest one item, logging the outcome. Returns whether it succeeded."""
    logger.info("ingesting %s", identifier, extra={"identifier": identifier})
    started = time.perf_counter()
    try:
        result = await service.ingest(identifier)
    except IngestError as e:
        logger.error(
            "ingest of %s failed: %s", identifier, e, extra={"identifier": identifier}
        )
        return False
    except Exception:  # noqa: BLE001
        logger.exception(
            "ingest of %s failed", identifier, extra={"identifier": identifier}
        )
        return False
    logger.info(
        "ingested %s: %d pages, %d with printed page numbers",
        identifier,
        result.pages,
        result.numbered_pages,
        extra={
            "identifier": identifier,
            "document_id": result.document_id,
            "pages": result.pages,
            "numbered_pages": result.numbered_pages,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        },
    )
    return True


if __name__ == "__main__":
    sys.exit(main())
