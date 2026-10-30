
def test_root_endpoint(client):
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "name": "API Sentinel Test",
        "version": "0.0.0-test",
        "status": "running",
    }


def test_health_endpoint(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "environment": "testing",
    }



def test_openapi_schema(client):
    response = client.get("/openapi.json")

    assert response.status_code == 200

    schema = response.json()

    assert schema["info"]["title"] == "API Sentinel"
    assert schema["info"]["version"] == "0.1.0"

    assert "/" in schema["paths"]
    assert "/health" in schema["paths"]

    assert "get" in schema["paths"]["/"]
    assert "get" in schema["paths"]["/health"]
