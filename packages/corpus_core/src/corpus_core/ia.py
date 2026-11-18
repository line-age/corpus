"""Internet Archive access: enumeration, item metadata, file download.

Only `enumerate` and `mirror` use this module. Files are fetched from each item's own data
server (`server` + `dir` in its metadata), which is what archive.org/download redirects to.
"""

import asyncio
import hashlib
import random
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Self

import httpx

SCRAPE_PATH = "/services/search/v1/scrape"
SCRAPE_FIELDS = (
    "identifier",
    "date",
    "title",
    "publisher",
    "volume",
    "issue",
    "imagecount",
)
SCRAPE_PAGE = 5000
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})

# The raw files the mirror keeps, by suffix. `_djvu.xml` is skipped: hOCR holds the same
# words plus confidences (ingestion plan, "What the sample changed").
MIRRORED_SUFFIXES = ("_hocr.html", "_page_numbers.json", "_djvu.txt", "_meta.xml")


class IAError(RuntimeError):
    pass


@dataclass(frozen=True)
class IAFile:
    name: str
    size: int | None
    md5: str | None


@dataclass(frozen=True)
class ItemMetadata:
    identifier: str
    server: str
    dir: str
    metadata: dict[str, Any]
    files: tuple[IAFile, ...] = field(default=())

    def file(self, suffix: str) -> IAFile | None:
        name = f"{self.identifier}{suffix}"
        return next((f for f in self.files if f.name == name), None)

    @property
    def restricted(self) -> bool:
        return (
            str(self.metadata.get("access-restricted-item", "false")).lower() == "true"
        )


class IAClient:
    def __init__(
        self,
        base_url: str = "https://archive.org",
        user_agent: str = "lineage-corpus/0.1",
        timeout_s: float = 60.0,
        max_attempts: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._max_attempts = max_attempts
        self._http = httpx.AsyncClient(
            headers={"User-Agent": user_agent},
            timeout=timeout_s,
            follow_redirects=True,
            transport=transport,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get(self, url: str, **kw: Any) -> httpx.Response:
        for attempt in range(1, self._max_attempts + 1):
            try:
                r = await self._http.get(url, **kw)
            except httpx.TransportError as e:
                if attempt == self._max_attempts:
                    raise IAError(f"GET {url}: {e}") from e
            else:
                if r.status_code not in RETRY_STATUS:
                    if r.status_code >= 400:
                        raise IAError(f"GET {url}: HTTP {r.status_code}")
                    return r
                if attempt == self._max_attempts:
                    raise IAError(
                        f"GET {url}: HTTP {r.status_code} after {attempt} attempts"
                    )
            await asyncio.sleep(min(30.0, 2**attempt) + random.random())
        raise AssertionError("unreachable")

    async def scrape(self, collection: str) -> AsyncIterator[dict[str, Any]]:
        """Every item in a collection, cursor-paged (advancedsearch caps at 10,000)."""
        cursor: str | None = None
        while True:
            params = {
                "q": f"collection:{collection}",
                "fields": ",".join(SCRAPE_FIELDS),
                "count": str(SCRAPE_PAGE),
            }
            if cursor:
                params["cursor"] = cursor
            data = (await self._get(self._base + SCRAPE_PATH, params=params)).json()
            for it in data.get("items", []):
                yield it
            cursor = data.get("cursor")
            if not cursor:
                return

    async def metadata(self, identifier: str) -> ItemMetadata:
        d = (await self._get(f"{self._base}/metadata/{identifier}")).json()
        if not d or "server" not in d:
            raise IAError(f"no metadata for {identifier}")
        files = tuple(
            IAFile(f["name"], int(f["size"]) if f.get("size") else None, f.get("md5"))
            for f in d.get("files", [])
        )
        return ItemMetadata(
            identifier, d["server"], d["dir"], d.get("metadata", {}), files
        )

    async def download(self, item: ItemMetadata, f: IAFile) -> bytes:
        """One file from the item's data server, checked against IA's md5 when it has one."""
        body = (await self._get(f"https://{item.server}{item.dir}/{f.name}")).content
        if f.md5 and hashlib.md5(body).hexdigest() != f.md5:
            raise IAError(f"{f.name}: md5 mismatch")
        if f.size is not None and len(body) != f.size:
            raise IAError(f"{f.name}: size {len(body)} != {f.size}")
        return body
