import pytest

DOC_PATHS = ["/docs", "/redoc", "/openapi.json"]


@pytest.mark.parametrize("path", DOC_PATHS)
def test_docs_served_when_enabled(configure, make_client, path):
    configure(docs_enabled=True)

    assert make_client().get(path).status_code == 200


@pytest.mark.parametrize("path", DOC_PATHS)
def test_docs_hidden_when_disabled(configure, make_client, path):
    configure(docs_enabled=False)

    assert make_client().get(path).status_code == 404
