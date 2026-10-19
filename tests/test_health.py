from app import __version__


def test_health_reports_ok_and_version(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}
