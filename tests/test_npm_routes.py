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

import time
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch
from pathlib import Path
from app.routes import npm_routes


# Test cache staleness logic for npm metadata
@pytest.mark.asyncio
@patch("app.routes.npm_routes.utils.is_cache_stale", return_value=True)
@patch("app.routes.npm_routes.utils.conditional_file_response", new_callable=AsyncMock)
@patch("app.routes.npm_routes.utils.fetch_and_cache", new_callable=AsyncMock)
async def test_npm_package_metadata_stale_refresh(mock_fetch, mock_response, mock_stale):
    test_package = "lodash"
    with patch.object(Path, "exists", return_value=True):
        mock_response.return_value = {"name": "lodash"}
        await npm_routes.npm_package_metadata(test_package, request=AsyncMock())
        mock_fetch.assert_called_once()
        mock_response.assert_called_once()

# Test cache freshness logic for npm metadata
@pytest.mark.asyncio
@patch("app.routes.npm_routes.utils.is_cache_stale", return_value=False)
@patch("app.routes.npm_routes.utils.conditional_file_response", new_callable=AsyncMock)
@patch("app.routes.npm_routes.utils.fetch_and_cache", new_callable=AsyncMock)
async def test_npm_package_metadata_fresh_cache(mock_fetch, mock_response, mock_stale):
    test_package = "lodash"
    with patch.object(Path, "exists", return_value=True):
        mock_response.return_value = {"name": "lodash"}
        await npm_routes.npm_package_metadata(test_package, request=AsyncMock())
        mock_fetch.assert_not_called()
        mock_response.assert_called_once()

client = TestClient(npm_routes.router)


@pytest.mark.asyncio
async def test_encode_scoped_package():
    # Scoped package
    assert npm_routes.encode_scoped_package("@types/react") == "%40types/react"
    # Normal package
    assert npm_routes.encode_scoped_package("lodash") == "lodash"


@pytest.mark.asyncio
@patch("app.routes.npm_routes.utils.conditional_file_response", new_callable=AsyncMock)
@patch("app.routes.npm_routes.utils.fetch_and_cache", new_callable=AsyncMock)
async def test_npm_package_metadata_fetch(mock_fetch, mock_response):
    test_package = "lodash"
    local_path = npm_routes.NPM_CACHE / Path(test_package) / "index.json"

    # File does not exist -> fetch_and_cache should be called
    with patch.object(Path, "exists", return_value=False):
        mock_response.return_value = {"name": "lodash"}
        response = await npm_routes.npm_package_metadata(test_package, request=AsyncMock())
        mock_fetch.assert_called_once()
        mock_response.assert_called_once()


@pytest.mark.asyncio
@patch("app.routes.npm_routes.utils.is_cache_stale", return_value=False)
@patch("app.routes.npm_routes.utils.conditional_file_response", new_callable=AsyncMock)
@patch("app.routes.npm_routes.utils.fetch_and_cache", new_callable=AsyncMock)
async def test_npm_package_metadata_cached(mock_fetch, mock_response, mock_stale):
    test_package = "lodash"

    # File exists -> fetch_and_cache should NOT be called
    with patch.object(Path, "exists", return_value=True):
        mock_response.return_value = {"name": "lodash"}
        response = await npm_routes.npm_package_metadata(test_package, request=AsyncMock())
        mock_fetch.assert_not_called()
        mock_response.assert_called_once()


@pytest.mark.asyncio
@patch("app.routes.npm_routes.utils.conditional_file_response", new_callable=AsyncMock)
@patch("app.routes.npm_routes.utils.fetch_and_cache", new_callable=AsyncMock)
async def test_npm_package_tarball_fetch(mock_fetch, mock_response):
    test_package = "lodash"
    tarball = "lodash-4.17.21.tgz"

    # File does not exist -> fetch_and_cache should be called
    with patch.object(Path, "exists", return_value=False):
        mock_response.return_value = b"tarball content"
        response = await npm_routes.npm_package_tarball(test_package, tarball, request=AsyncMock())
        mock_fetch.assert_called_once()
        mock_response.assert_called_once()


@pytest.mark.asyncio
@patch("app.routes.npm_routes.utils.conditional_file_response", new_callable=AsyncMock)
@patch("app.routes.npm_routes.utils.fetch_and_cache", new_callable=AsyncMock)
async def test_npm_package_tarball_cached(mock_fetch, mock_response):
    test_package = "lodash"
    tarball = "lodash-4.17.21.tgz"

    # File exists -> fetch_and_cache should NOT be called
    with patch.object(Path, "exists", return_value=True):
        mock_response.return_value = b"cached tarball"
        response = await npm_routes.npm_package_tarball(test_package, tarball, request=AsyncMock())
        mock_fetch.assert_not_called()
        mock_response.assert_called_once()


@pytest.mark.asyncio
@patch("app.routes.npm_routes.utils.conditional_file_response", new_callable=AsyncMock)
@patch("app.routes.npm_routes.utils.fetch_and_cache", new_callable=AsyncMock)
async def test_npm_security_bulk_fetch(mock_fetch, mock_response):
    body = b'{"advisories":[]}'
    body_hash = str(abs(hash(body)))
    local_path = npm_routes.NPM_CACHE / "security" / f"{body_hash}.json"

    # File does not exist -> fetch_and_cache should be called
    with patch.object(Path, "exists", return_value=False):
        request_mock = AsyncMock()
        request_mock.body.return_value = body
        mock_fetch.return_value = {"result": "ok"}
        response = await npm_routes.npm_security_bulk(request_mock)
        mock_fetch.assert_called_once()
        assert response == {"result": "ok"}


@pytest.mark.asyncio
@patch("app.routes.npm_routes.utils.is_cache_stale", return_value=False)
@patch("app.routes.npm_routes.utils.conditional_file_response", new_callable=AsyncMock)
async def test_npm_security_bulk_cached(mock_response, mock_stale):
    body = b'{"advisories":[]}'
    request_mock = AsyncMock()
    request_mock.body.return_value = body

    # File exists and is fresh -> conditional_file_response should be called
    with patch.object(Path, "exists", return_value=True):
        mock_response.return_value = {"cached": True}
        response = await npm_routes.npm_security_bulk(request_mock)
        mock_response.assert_called_once()
        assert response == {"cached": True}


@pytest.mark.asyncio
@patch("app.routes.npm_routes.utils.is_cache_stale", return_value=True)
@patch("app.routes.npm_routes.utils.fetch_and_cache", new_callable=AsyncMock)
async def test_npm_security_bulk_stale_refresh(mock_fetch, mock_stale):
    """Test that security bulk cache is refreshed when stale (>24h old)."""
    body = b'{"advisories":[]}'
    request_mock = AsyncMock()
    request_mock.body.return_value = body

    mock_fetch.return_value = {"freshly": "fetched"}
    response = await npm_routes.npm_security_bulk(request_mock)
    mock_fetch.assert_called_once()
    assert response == {"freshly": "fetched"}


@pytest.mark.asyncio
async def test_npm_package_metadata_rejects_absolute_path():
    with pytest.raises(HTTPException) as exc_info:
        await npm_routes.npm_package_metadata("/etc/passwd", request=AsyncMock())
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_npm_package_tarball_rejects_traversal():
    with pytest.raises(HTTPException) as exc_info:
        await npm_routes.npm_package_tarball("../evil", "file.tgz", request=AsyncMock())
    assert exc_info.value.status_code == 400


# ========================================
# Additional Coverage Tests
# ========================================

@pytest.mark.asyncio
async def test_npm_package_metadata_raises_validation_error():
    """Test that ValidationError in safe_join_path raises HTTPException(400)."""
    from app.validators import ValidationError
    
    with patch("app.routes.npm_routes.safe_join_path", side_effect=ValidationError("Invalid path")):
        with pytest.raises(HTTPException) as exc_info:
            await npm_routes.npm_package_metadata("lodash", request=AsyncMock())
        assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_npm_package_metadata_file_not_found_error():
    """Test that FileNotFoundError from conditional_file_response raises HTTPException(404)."""
    with patch("app.routes.npm_routes.utils.is_cache_stale", return_value=False):
        with patch("app.routes.npm_routes.utils.conditional_file_response", 
                   side_effect=FileNotFoundError("Not found")):
            with pytest.raises(HTTPException) as exc_info:
                await npm_routes.npm_package_metadata("nonexistent", request=AsyncMock())
            assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_npm_package_tarball_invalid_tarball_name():
    """Test rejection of invalid tarball filename."""
    with pytest.raises(HTTPException) as exc_info:
        await npm_routes.npm_package_tarball("lodash", "../../../etc/passwd", request=AsyncMock())
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_npm_package_tarball_raises_validation_error():
    """Test that ValidationError in safe_join_path raises HTTPException(400)."""
    from app.validators import ValidationError
    
    with patch("app.routes.npm_routes.safe_join_path", side_effect=ValidationError("Invalid path")):
        with pytest.raises(HTTPException) as exc_info:
            await npm_routes.npm_package_tarball("lodash", "lodash-4.17.21.tgz", request=AsyncMock())
        assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_npm_package_tarball_file_not_found_error():
    """Test that FileNotFoundError from conditional_file_response raises HTTPException(404)."""
    with patch("app.routes.npm_routes.utils.conditional_file_response", 
               side_effect=FileNotFoundError("Not found")):
        with patch.object(Path, "exists", return_value=True):
            with pytest.raises(HTTPException) as exc_info:
                await npm_routes.npm_package_tarball("lodash", "missing.tgz", request=AsyncMock())
            assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_npm_security_bulk_raises_validation_error():
    """Test that ValidationError in safe_join_path raises HTTPException(400)."""
    from app.validators import ValidationError
    
    body = b'{"advisories":[]}'
    request_mock = AsyncMock()
    request_mock.body.return_value = body
    
    with patch("app.routes.npm_routes.safe_join_path", side_effect=ValidationError("Invalid path")):
        with pytest.raises(HTTPException) as exc_info:
            await npm_routes.npm_security_bulk(request_mock)
        assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_npm_security_bulk_file_not_found_error():
    """Test that FileNotFoundError triggers fetch from upstream."""
    from unittest.mock import AsyncMock as AsyncMockClass
    
    body = b'{"advisories":[]}'
    request_mock = AsyncMockClass()
    request_mock.body.return_value = body
    
    # is_cache_stale returns False (cache is fresh), but conditional_file_response raises FileNotFoundError
    # This edge case should trigger fetch from upstream
    with patch("app.routes.npm_routes.utils.is_cache_stale", return_value=False):
        with patch("app.routes.npm_routes.utils.conditional_file_response", 
                   side_effect=FileNotFoundError("Not found")):
            with patch("app.routes.npm_routes.utils.fetch_and_cache", new_callable=AsyncMockClass) as mock_fetch:
                mock_fetch.return_value = {"fetched": "data"}
                response = await npm_routes.npm_security_bulk(request_mock)
                mock_fetch.assert_called_once()
                assert response == {"fetched": "data"}


@pytest.mark.asyncio
async def test_encode_scoped_package_with_slash():
    """Test encoding of scoped packages with slashes."""
    result = npm_routes.encode_scoped_package("@babel/core")
    assert result == "%40babel/core"


@pytest.mark.asyncio
async def test_encode_scoped_package_unscoped():
    """Test that unscoped packages are quoted properly."""
    result = npm_routes.encode_scoped_package("package-name")
    assert result == "package-name"


@pytest.mark.asyncio
async def test_npm_package_metadata_invalid_package_name():
    """Test rejection of invalid package names."""
    with pytest.raises(HTTPException) as exc_info:
        await npm_routes.npm_package_metadata("../../etc/passwd", request=AsyncMock())
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_npm_package_tarball_invalid_package_name():
    """Test rejection of invalid package names in tarball endpoint."""
    with pytest.raises(HTTPException) as exc_info:
        await npm_routes.npm_package_tarball("../../evil", "tarball.tgz", request=AsyncMock())
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
@patch("app.routes.npm_routes.utils.is_cache_stale", return_value=False)
@patch("app.routes.npm_routes.utils.conditional_file_response", new_callable=AsyncMock)
@patch("app.routes.npm_routes.utils.fetch_and_cache", new_callable=AsyncMock)
async def test_npm_package_tarball_fresh_cache(mock_fetch, mock_response, mock_stale):
    """Test that tarball fetch is skipped for fresh cache."""
    test_package = "lodash"
    tarball = "lodash-4.17.21.tgz"

    with patch.object(Path, "exists", return_value=True):
        mock_response.return_value = b"cached content"
        response = await npm_routes.npm_package_tarball(test_package, tarball, request=AsyncMock())
        mock_fetch.assert_not_called()
        mock_response.assert_called_once()


# ========================================
# Security fix tests
# ========================================

def test_sanitize_headers_strips_crlf_in_value():
    """Headers with CRLF in values must be dropped."""
    result = npm_routes._sanitize_headers({
        "accept": "application/json",
        "x-evil": "value\r\nX-Injected: yes",
    })
    assert "x-evil" not in result
    assert "accept" in result


def test_sanitize_headers_strips_crlf_in_key():
    """Headers with CRLF in key names must be dropped."""
    result = npm_routes._sanitize_headers({
        "good-header": "ok",
        "bad\r\nheader": "value",
    })
    assert "good-header" in result
    assert len(result) == 1


def test_sanitize_headers_strips_newline_only():
    """Headers with lone \\n must also be dropped."""
    result = npm_routes._sanitize_headers({
        "x-newline": "val\nue",
        "normal": "fine",
    })
    assert "x-newline" not in result
    assert "normal" in result


def test_sanitize_headers_passes_clean_headers():
    """Clean headers must pass through unchanged."""
    headers = {
        "content-type": "application/json",
        "authorization": "Bearer token123",
        "x-request-id": "abc-123",
    }
    result = npm_routes._sanitize_headers(headers)
    assert result == headers


def test_encode_scoped_package_quotes_name():
    """The name portion of scoped packages must also be URL-encoded."""
    # name contains a space (edge case) — must be encoded
    result = npm_routes.encode_scoped_package("@scope/name with space")
    assert "name%20with%20space" in result or "name+with+space" in result


def test_encode_scoped_package_normal_name_unchanged():
    """Normal package names without special chars round-trip correctly."""
    assert npm_routes.encode_scoped_package("@types/react") == "%40types/react"
    assert npm_routes.encode_scoped_package("lodash") == "lodash"
