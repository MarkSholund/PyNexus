# tests/test_main.py
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock
from app import main
import app.config as config
import importlib

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
    routes = [r.path for r in main.app.routes]
    # Check that at least one route from each router exists
    assert any(r.startswith("/pypi") for r in routes)
    assert any(r.startswith("/maven2") for r in routes)
    assert any(r.startswith("/npm") for r in routes)


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


# ========================================
# Security fix tests
# ========================================

def test_require_https_accepts_https_url():
    """_require_https must accept valid HTTPS URLs."""
    from app.config import _require_https
    assert _require_https("https://registry.npmjs.org", "NPM_REGISTRY") == "https://registry.npmjs.org"


def test_require_https_rejects_http_url():
    """_require_https must raise ValueError for HTTP URLs."""
    from app.config import _require_https
    with pytest.raises(ValueError, match="must use HTTPS"):
        _require_https("http://registry.npmjs.org", "NPM_REGISTRY")


def test_require_https_rejects_ftp_url():
    """_require_https must reject non-HTTP schemes."""
    from app.config import _require_https
    with pytest.raises(ValueError, match="must use HTTPS"):
        _require_https("ftp://example.com", "MAVEN_CENTRAL")


def test_config_upstream_urls_are_https():
    """All default upstream registry URLs must use HTTPS."""
    assert config.NPM_REGISTRY.startswith("https://")
    assert config.PYPI_REGISTRY.startswith("https://")
    assert config.MAVEN_CENTRAL.startswith("https://")


def test_config_rejects_http_env_override(monkeypatch):
    """Setting an upstream registry to HTTP via env must raise ValueError at import."""
    import importlib
    monkeypatch.setenv("NPM_REGISTRY", "http://evil.example.com")
    with pytest.raises(ValueError, match="must use HTTPS"):
        import app.config as cfg_module
        importlib.reload(cfg_module)
