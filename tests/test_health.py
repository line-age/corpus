from corpus import __version__


def test_healthy_when_database_is_migrated(migrated_db, make_client):
    response = make_client().get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["checks"]["database"]["status"] == "ok"
    assert body["checks"]["pgvector"]["status"] == "ok"
    assert body["checks"]["pgvector"]["detail"].startswith("vector ")


def test_unhealthy_when_pgvector_is_not_installed(bare_db, make_client):
    response = make_client().get("/healthz")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "error"
    assert body["checks"]["database"]["status"] == "ok"
    assert body["checks"]["pgvector"]["status"] == "error"
    assert "not installed" in body["checks"]["pgvector"]["detail"]


def test_unhealthy_when_database_is_unreachable(configure, make_client):
    # Nothing listens on port 1, so connections are refused immediately.
    configure(postgres_host="127.0.0.1", postgres_port=1)

    response = make_client().get("/healthz")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "error"
    assert {check["status"] for check in body["checks"].values()} == {"error"}
    # Connection details stay in the logs, not in the response.
    assert "127.0.0.1" not in response.text
