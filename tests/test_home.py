def test_home_returns_200(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Your time is worth" in response.data


def test_missing_page_uses_friendly_error(client):
    response = client.get("/missing")
    assert response.status_code == 404
    assert b"wandered off" in response.data
