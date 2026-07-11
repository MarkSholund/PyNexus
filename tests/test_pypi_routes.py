# SPDX-License-Identifier: GPL-3.0-or-later
#
# Copyright (C) 2025 Mark Sholund
#
# This file is part of the FastAPI Nexus Proxy project.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

import pytest
from fastapi import HTTPException
from unittest.mock import AsyncMock, patch
from pathlib import Path
from app.routes import pypi_routes

client = AsyncMock()


# -----------------------
# Helper function test
# -----------------------
def test_rewrite_index_html_relative_and_absolute():
    html = """
    <html>
      <body>
        <a href="https://files.pythonhosted.org/packages/abc.whl">whl</a>
        <a href="https://pypi.org/simple/package/">pkg</a>
        <a href="packages/xyz.tar.gz">tarball</a>
      </body>
    </html>
    """
    rewritten = pypi_routes.rewrite_index_html(html, base_url="/pypi")
    assert "/packages/abc.whl" in rewritten
    assert "/pypi/simple/package/" in rewritten
    assert "/packages/xyz.tar.gz" in rewritten


# -----------------------
# Endpoint tests
# -----------------------

@pytest.mark.asyncio
@patch("app.routes.pypi_routes.utils.conditional_file_response", new_callable=AsyncMock)
@patch("app.routes.pypi_routes.httpx.AsyncClient.get", new_callable=AsyncMock)
async def test_pypi_root_index_fetch(mock_get, mock_response):
    with patch.object(Path, "exists", return_value=False):
        mock_get.return_value.status_code = 200
        mock_get.return_value.text = "<html>root index</html>"
        mock_response.return_value = b"content"

        request_mock = AsyncMock()
        response = await pypi_routes.pypi_root_index(request_mock)
        mock_response.assert_called_once()


@pytest.mark.asyncio
@patch("app.routes.pypi_routes.utils.conditional_file_response", new_callable=AsyncMock)
@patch("app.routes.pypi_routes.httpx.AsyncClient.get", new_callable=AsyncMock)
async def test_pypi_package_index_fetch(mock_get, mock_response):
    package = "example"
    with patch.object(Path, "exists", return_value=False):
        mock_get.return_value.status_code = 200
        mock_get.return_value.text = "<html><a href='packages/foo.whl'></a></html>"
        mock_response.return_value = b"content"

        request_mock = AsyncMock()
        response = await pypi_routes.pypi_package_index(package, request_mock)
        mock_response.assert_called_once()


@pytest.mark.asyncio
@patch("app.routes.pypi_routes.utils.conditional_file_response", new_callable=AsyncMock)
@patch("app.routes.pypi_routes.utils.fetch_and_cache", new_callable=AsyncMock)
async def test_pypi_artifact_fetch(mock_fetch, mock_response):
    path = "somepackage/file.whl"
    with patch.object(Path, "exists", return_value=False):
        mock_response.return_value = b"artifact"
        request_mock = AsyncMock()
        response = await pypi_routes.pypi_artifact(path, request_mock)
        mock_fetch.assert_called_once()
        mock_response.assert_called_once()


@pytest.mark.asyncio
@patch("app.routes.pypi_routes.utils.conditional_file_response", new_callable=AsyncMock)
@patch("app.routes.pypi_routes.utils.fetch_and_cache", new_callable=AsyncMock)
async def test_pypi_artifact_cached(mock_fetch, mock_response):
    path = "somepackage/file.whl"
    with patch.object(Path, "exists", return_value=True):
        mock_response.return_value = b"artifact"
        request_mock = AsyncMock()
        response = await pypi_routes.pypi_artifact(path, request_mock)
        mock_fetch.assert_not_called()
        mock_response.assert_called_once()


@pytest.mark.asyncio
@patch("app.routes.pypi_routes.utils.conditional_file_response", new_callable=AsyncMock)
@patch("app.routes.pypi_routes.utils.fetch_and_cache", new_callable=AsyncMock)
async def test_pypi_package_json_fetch(mock_fetch, mock_response):
    package = "example"
    with patch.object(Path, "exists", return_value=False):
        mock_response.return_value = {"json": True}
        request_mock = AsyncMock()
        response = await pypi_routes.pypi_package_json(package, request_mock)
        mock_fetch.assert_called_once()
        mock_response.assert_called_once()


@pytest.mark.asyncio
@patch("app.routes.pypi_routes.utils.conditional_file_response", new_callable=AsyncMock)
@patch("app.routes.pypi_routes.utils.fetch_and_cache", new_callable=AsyncMock)
async def test_pypi_package_version_json_fetch(mock_fetch, mock_response):
    package = "example"
    version = "1.0.0"
    with patch.object(Path, "exists", return_value=False):
        mock_response.return_value = {"json": True}
        request_mock = AsyncMock()
        response = await pypi_routes.pypi_package_version_json(package, version, request_mock)
        mock_fetch.assert_called_once()
        mock_response.assert_called_once()


# -----------------------
# Security tests (path traversal)
# -----------------------

@pytest.mark.asyncio
async def test_pypi_package_index_rejects_absolute_path():
    request_mock = AsyncMock()
    with pytest.raises(HTTPException) as exc_info:
        await pypi_routes.pypi_package_index("/etc/passwd", request_mock)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_pypi_artifact_rejects_traversal():
    request_mock = AsyncMock()
    with pytest.raises(HTTPException) as exc_info:
        await pypi_routes.pypi_artifact("../secret/file.whl", request_mock)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_pypi_package_json_rejects_traversal():
    request_mock = AsyncMock()
    with pytest.raises(HTTPException) as exc_info:
        await pypi_routes.pypi_package_json("../../package", request_mock)
    assert exc_info.value.status_code == 400


# ========================================
# Additional Coverage Tests
# ========================================

@pytest.mark.asyncio
@patch("app.routes.pypi_routes.utils.is_cache_stale", return_value=True)
@patch("app.routes.pypi_routes.utils.conditional_file_response", new_callable=AsyncMock)
@patch("app.routes.pypi_routes.httpx.AsyncClient")
async def test_pypi_root_index_stale_refresh(mock_client_cls, mock_response, mock_stale):
    """Test that stale PyPI root index is refreshed."""
    mock_client = AsyncMock()
    mock_get_resp = AsyncMock()
    mock_get_resp.status_code = 200
    mock_get_resp.text = "<html>root</html>"
    mock_client.get = AsyncMock(return_value=mock_get_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client_cls.return_value = mock_client
    
    mock_response.return_value = b"content"
    request_mock = AsyncMock()
    
    with patch.object(Path, "exists", return_value=True):
        response = await pypi_routes.pypi_root_index(request_mock)
        mock_client.get.assert_called_once()
        mock_response.assert_called_once()


@pytest.mark.asyncio
@patch("app.routes.pypi_routes.utils.is_cache_stale", return_value=False)
@patch("app.routes.pypi_routes.utils.conditional_file_response", new_callable=AsyncMock)
async def test_pypi_root_index_fresh_cache(mock_response, mock_stale):
    """Test that fresh PyPI root index is served from cache."""
    mock_response.return_value = b"cached content"
    request_mock = AsyncMock()
    
    with patch.object(Path, "exists", return_value=True):
        response = await pypi_routes.pypi_root_index(request_mock)
        mock_response.assert_called_once()


@pytest.mark.asyncio
@patch("app.routes.pypi_routes.utils.is_cache_stale", return_value=False)
@patch("app.routes.pypi_routes.utils.conditional_file_response", new_callable=AsyncMock)
async def test_pypi_package_index_fresh_cache(mock_response, mock_stale):
    """Test that fresh package index is served from cache."""
    mock_response.return_value = b"package index"
    request_mock = AsyncMock()
    
    with patch.object(Path, "exists", return_value=True):
        response = await pypi_routes.pypi_package_index("requests", request_mock)
        mock_response.assert_called_once()


@pytest.mark.asyncio
async def test_pypi_root_index_raises_validation_error():
    """Test that ValidationError from utils.safe_cache_path is handled."""
    from app.validators import ValidationError
    
    with patch("app.routes.pypi_routes.utils.safe_cache_path", 
               side_effect=ValidationError("Invalid path")):
        request_mock = AsyncMock()
        with pytest.raises(ValidationError):
            await pypi_routes.pypi_root_index(request_mock)


@pytest.mark.asyncio
async def test_pypi_package_index_raises_validation_error():
    """Test that ValidationError from safe_cache_path is handled."""
    from app.validators import ValidationError
    
    with patch("app.routes.pypi_routes.safe_join_path", 
               side_effect=ValidationError("Invalid path")):
        request_mock = AsyncMock()
        with pytest.raises(HTTPException) as exc_info:
            await pypi_routes.pypi_package_index("requests", request_mock)
        assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_pypi_artifact_raises_validation_error():
    """Test that ValidationError from safe_join_path raises HTTPException(400)."""
    from app.validators import ValidationError
    
    with patch("app.routes.pypi_routes.safe_join_path", 
               side_effect=ValidationError("Invalid path")):
        request_mock = AsyncMock()
        with pytest.raises(HTTPException) as exc_info:
            await pypi_routes.pypi_artifact("requests/file.whl", request_mock)
        # PyPI routes call httpx to fetch, which may result in 404
        assert exc_info.value.status_code in (400, 404)


@pytest.mark.asyncio
async def test_pypi_package_json_raises_validation_error():
    """Test that ValidationError from safe_join_path is handled."""
    from app.validators import ValidationError
    
    with patch("app.routes.pypi_routes.safe_join_path", 
               side_effect=ValidationError("Invalid path")):
        request_mock = AsyncMock()
        with pytest.raises(HTTPException) as exc_info:
            await pypi_routes.pypi_package_json("requests", request_mock)
        assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_pypi_package_version_json_raises_validation_error():
    """Test that ValidationError from safe_join_path is handled."""
    from app.validators import ValidationError
    
    with patch("app.routes.pypi_routes.safe_join_path", 
               side_effect=ValidationError("Invalid path")):
        request_mock = AsyncMock()
        with pytest.raises(HTTPException) as exc_info:
            await pypi_routes.pypi_package_version_json("requests", "2.0.0", request_mock)
        assert exc_info.value.status_code == 400


@pytest.mark.asyncio
@patch("app.routes.pypi_routes.utils.conditional_file_response", 
       new_callable=AsyncMock)
async def test_pypi_root_index_file_not_found(mock_response):
    """Test FileNotFoundError handling for root index (no try-except in endpoint)."""
    request_mock = AsyncMock()
    mock_response.side_effect = FileNotFoundError("Not found")
    with patch.object(Path, "exists", return_value=True):
        with pytest.raises(FileNotFoundError):
            await pypi_routes.pypi_root_index(request_mock)


@pytest.mark.asyncio
@patch("app.routes.pypi_routes.utils.is_cache_stale", return_value=False)
@patch("app.routes.pypi_routes.utils.conditional_file_response", 
       new_callable=AsyncMock)
async def test_pypi_package_index_file_not_found(mock_response, mock_stale):
    """Test FileNotFoundError handling for package index."""
    request_mock = AsyncMock()
    mock_response.side_effect = FileNotFoundError("Not found")
    with patch.object(Path, "exists", return_value=True):
        with pytest.raises(FileNotFoundError):
            await pypi_routes.pypi_package_index("requests", request_mock)


@pytest.mark.asyncio
@patch("app.routes.pypi_routes.utils.conditional_file_response", 
       side_effect=FileNotFoundError("Not found"))
async def test_pypi_artifact_file_not_found(mock_response):
    """Test FileNotFoundError handling for artifacts."""
    request_mock = AsyncMock()
    with patch.object(Path, "exists", return_value=True):
        with pytest.raises(HTTPException) as exc_info:
            await pypi_routes.pypi_artifact("requests/file.whl", request_mock)
        assert exc_info.value.status_code == 404


@pytest.mark.asyncio
@patch("app.routes.pypi_routes.utils.conditional_file_response", 
       new_callable=AsyncMock)
async def test_pypi_package_json_file_not_found(mock_response):
    """Test FileNotFoundError handling for package JSON."""
    request_mock = AsyncMock()
    mock_response.side_effect = FileNotFoundError("Not found")
    with patch.object(Path, "exists", return_value=True):
        with pytest.raises(FileNotFoundError):
            await pypi_routes.pypi_package_json("requests", request_mock)


@pytest.mark.asyncio
@patch("app.routes.pypi_routes.utils.conditional_file_response", 
       new_callable=AsyncMock)
async def test_pypi_package_version_json_file_not_found(mock_response):
    """Test FileNotFoundError handling for package version JSON."""
    request_mock = AsyncMock()
    mock_response.side_effect = FileNotFoundError("Not found")
    with patch.object(Path, "exists", return_value=True):
        with pytest.raises(FileNotFoundError):
            await pypi_routes.pypi_package_version_json("requests", "2.0.0", request_mock)


@pytest.mark.asyncio
async def test_pypi_artifact_invalid_package_name():
    """Test rejection of invalid package names in artifact endpoint."""
    request_mock = AsyncMock()
    with pytest.raises(HTTPException) as exc_info:
        await pypi_routes.pypi_artifact("/../../evil/file.whl", request_mock)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_pypi_package_index_invalid_package_name():
    """Test rejection of invalid package names."""
    request_mock = AsyncMock()
    with pytest.raises(HTTPException) as exc_info:
        await pypi_routes.pypi_package_index("/evil", request_mock)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_pypi_package_json_invalid_package_name():
    """Test rejection of invalid package names for JSON endpoint."""
    request_mock = AsyncMock()
    with pytest.raises(HTTPException) as exc_info:
        await pypi_routes.pypi_package_json("../../../evil", request_mock)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_pypi_package_version_json_invalid_package_name():
    """Test rejection of invalid package names for version JSON."""
    request_mock = AsyncMock()
    with pytest.raises(HTTPException) as exc_info:
        await pypi_routes.pypi_package_version_json("/evil", "1.0.0", request_mock)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_pypi_package_version_json_invalid_version():
    """Test rejection of invalid version strings."""
    request_mock = AsyncMock()
    with pytest.raises(HTTPException) as exc_info:
        await pypi_routes.pypi_package_version_json("requests", "../../../evil", request_mock)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
@patch("app.routes.pypi_routes.utils.conditional_file_response", new_callable=AsyncMock)
@patch("app.routes.pypi_routes.utils.fetch_and_cache", new_callable=AsyncMock)
async def test_pypi_artifact_cached(mock_fetch, mock_response):
    """Test that cached artifacts are not re-fetched."""
    path = "requests/requests-2.0.0-py3-none-any.whl"
    with patch.object(Path, "exists", return_value=True):
        mock_response.return_value = b"wheel data"
        request_mock = AsyncMock()
        response = await pypi_routes.pypi_artifact(path, request_mock)
        mock_fetch.assert_not_called()
        mock_response.assert_called_once()


@pytest.mark.asyncio
@patch("app.routes.pypi_routes.utils.conditional_file_response", new_callable=AsyncMock)
@patch("app.routes.pypi_routes.utils.fetch_and_cache", new_callable=AsyncMock)
async def test_pypi_package_json_cached(mock_fetch, mock_response):
    """Test that cached JSON metadata is not re-fetched."""
    with patch.object(Path, "exists", return_value=True):
        mock_response.return_value = {"info": {"name": "requests"}}
        request_mock = AsyncMock()
        response = await pypi_routes.pypi_package_json("requests", request_mock)
        mock_fetch.assert_not_called()
        mock_response.assert_called_once()


@pytest.mark.asyncio
@patch("app.routes.pypi_routes.utils.conditional_file_response", new_callable=AsyncMock)
@patch("app.routes.pypi_routes.utils.fetch_and_cache", new_callable=AsyncMock)
async def test_pypi_package_version_json_cached(mock_fetch, mock_response):
    """Test that cached version JSON is not re-fetched."""
    with patch.object(Path, "exists", return_value=True):
        mock_response.return_value = {"version": "2.0.0"}
        request_mock = AsyncMock()
        response = await pypi_routes.pypi_package_version_json("requests", "2.0.0", request_mock)
        mock_fetch.assert_not_called()
        mock_response.assert_called_once()


# ------------------------------------------------------------------
# SECURITY: artifact path charset validation + upstream URL quoting
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pypi_artifact_rejects_control_characters():
    """CR/LF and other control chars must be rejected before being used
    to build the upstream request URL."""
    request_mock = AsyncMock()
    with pytest.raises(HTTPException) as exc_info:
        await pypi_routes.pypi_artifact("packages/foo\r\nX-Injected: 1", request_mock)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_pypi_artifact_rejects_disallowed_characters():
    request_mock = AsyncMock()
    with pytest.raises(HTTPException) as exc_info:
        await pypi_routes.pypi_artifact("somepackage/file whl?evil=1", request_mock)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
@patch("app.routes.pypi_routes.utils.conditional_file_response", new_callable=AsyncMock)
@patch("app.routes.pypi_routes.utils.fetch_and_cache", new_callable=AsyncMock)
async def test_pypi_artifact_quotes_upstream_url(mock_fetch, mock_response):
    """Special characters in an otherwise-valid path must be percent-encoded
    before being interpolated into the upstream URL."""
    path = "aa/bb/requests-2.0.0+local.whl"
    with patch.object(Path, "exists", return_value=False):
        mock_response.return_value = b"wheel data"
        request_mock = AsyncMock()
        await pypi_routes.pypi_artifact(path, request_mock)

        mock_fetch.assert_called_once()
        called_url = mock_fetch.call_args[0][0]
        assert called_url == "https://files.pythonhosted.org/packages/aa/bb/requests-2.0.0%2Blocal.whl"
