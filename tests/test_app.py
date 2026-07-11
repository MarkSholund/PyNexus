# tests/test_main.py
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock
from app import main
import app.config as config

client = TestClient(main.app)


# -----------------------
# Test root endpoint
# -----------------------
def test_root_endpoint():
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "Local caching proxy" in data["message"]


# -----------------------
# Test routers are included
# -----------------------
def test_routers_included():
    response = client.get("/openapi.json")
    paths = response.json()["paths"]
    assert any(p.startswith("/pypi") for p in paths)
    assert any(p.startswith("/maven2") for p in paths)
    assert any(p.startswith("/npm") for p in paths)


# -----------------------
# Test lifespan logging (Windows-compatible)
# -----------------------
@pytest.mark.asyncio
async def test_lifespan_logging():
    class DummyApp:
        pass

    dummy_app = DummyApp()

    # Patch the logger using regular `with patch(...)`
    with patch("app.main.logger") as mock_logger:
        # Run the async lifespan context manager
        async with main.lifespan(dummy_app):
            # Startup log should be called
            mock_logger.info.assert_any_call(f"CACHE_DIR => {config.CACHE_DIR}")

        # Shutdown log should be called after context exits
        mock_logger.info.assert_any_call("Shutting down FastAPI app")


# -----------------------
# Test that TestClient triggers lifespan logging
# -----------------------
def test_client_triggers_lifespan():
    with patch("app.main.logger") as mock_logger:
        with TestClient(main.app) as client_ctx:
            response = client_ctx.get("/")
            assert response.status_code == 200

        # Shutdown log should be called after TestClient context exits
        mock_logger.info.assert_any_call("Shutting down FastAPI app")
