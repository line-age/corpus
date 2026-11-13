from corpus import __version__


def test_index_describes_the_service(make_client):
    response = make_client().get("/")

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "corpus"
    assert body["version"] == __version__


def test_index_links_docs_when_enabled(configure, make_client):
    configure(docs_enabled=True)

    assert make_client().get("/").json()["docs"] == "/docs"


def test_index_omits_docs_link_when_disabled(configure, make_client):
    configure(docs_enabled=False)

    assert make_client().get("/").json()["docs"] is None
