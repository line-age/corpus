import hashlib

import httpx
import pytest
import respx
from corpus_core.ia import IAClient, IAError
from corpus_core.storage import LocalStore, open_store, raw_key

BASE = "https://archive.org"


@respx.mock
async def test_scrape_follows_the_cursor():
    route = respx.get(f"{BASE}/services/search/v1/scrape")
    route.side_effect = [
        httpx.Response(
            200,
            json={"items": [{"identifier": "a"}, {"identifier": "b"}], "cursor": "c1"},
        ),
        httpx.Response(200, json={"items": [{"identifier": "c"}]}),
    ]
    async with IAClient(BASE) as ia:
        got = [it["identifier"] async for it in ia.scrape("pub_x")]
    assert got == ["a", "b", "c"]
    assert route.calls[1].request.url.params["cursor"] == "c1"


@respx.mock
async def test_metadata_download_and_checksums(monkeypatch):
    monkeypatch.setattr("corpus_core.ia.asyncio.sleep", _no_sleep)
    body = b"<html>hocr</html>"
    respx.get(f"{BASE}/metadata/item1").respond(
        json={
            "server": "ia800.us.archive.org",
            "dir": "/1/items/item1",
            "metadata": {"access-restricted-item": "false"},
            "files": [
                {
                    "name": "item1_hocr.html",
                    "size": str(len(body)),
                    "md5": hashlib.md5(body).hexdigest(),
                },
                {"name": "item1_bad.txt", "size": "3", "md5": "0" * 32},
            ],
        }
    )
    data = respx.get("https://ia800.us.archive.org/1/items/item1/item1_hocr.html")
    data.side_effect = [httpx.Response(503), httpx.Response(200, content=body)]
    respx.get("https://ia800.us.archive.org/1/items/item1/item1_bad.txt").respond(
        content=b"abc"
    )
    async with IAClient(BASE) as ia:
        meta = await ia.metadata("item1")
        assert not meta.restricted
        hocr = meta.file("_hocr.html")
        assert hocr is not None
        assert await ia.download(meta, hocr) == body  # retried once after a 503
        bad = meta.file("_bad.txt")
        assert bad is not None
        with pytest.raises(IAError, match="md5"):
            await ia.download(meta, bad)


@respx.mock
async def test_client_errors_are_not_retried():
    route = respx.get(f"{BASE}/metadata/missing").respond(404)
    async with IAClient(BASE) as ia:
        with pytest.raises(IAError, match="404"):
            await ia.metadata("missing")
    assert route.call_count == 1


async def _no_sleep(_s: float) -> None:
    return None


def test_local_store(tmp_path):
    st = open_store(f"file://{tmp_path}")
    assert isinstance(st, LocalStore)
    k = raw_key("pub_x", "item1", "item1_hocr.html")
    assert not st.exists(k)
    assert st.put(k, b"x") is True
    assert st.put(k, b"y") is False  # write-once: the first copy stays
    assert st.exists(k) and st.get(k) == b"x"
    assert [p.name for p in (tmp_path / "raw/pub_x/item1").iterdir()] == [
        "item1_hocr.html"
    ]
    with pytest.raises(ValueError):
        st.put("../../etc/passwd", b"no")
