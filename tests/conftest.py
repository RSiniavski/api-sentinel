
import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app


@pytest.fixture
def client():
    app.dependency_overrides[get_settings] = lambda: Settings(
        app_name="API Sentinel Test",
        app_version="0.0.0-test",
        environment="testing",
    )

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
