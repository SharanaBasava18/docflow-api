import os
import pytest
from fastapi.testclient import TestClient

os.environ["ENV"] = "testing"
os.environ["APP_UPLOAD_DIR"] = "/uploads"
os.environ["APP_MAX_CHUNK_SIZE"] = "1024"

@pytest.fixture(scope="module")
def test_app():
    from main import create_application

    app = create_application()
    with TestClient(app) as test_client:
        yield test_client
