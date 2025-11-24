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
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch
from pathlib import Path
from app.routes import maven_routes

client = TestClient(maven_routes.router)


@pytest.mark.asyncio
@patch("app.routes.maven_routes.utils.conditional_file_response", new_callable=AsyncMock)
@patch("app.routes.maven_routes.utils.fetch_and_cache", new_callable=AsyncMock)
async def test_maven_proxy_fetch(mock_fetch, mock_response):
    """
    Test fetching a file when local cache does not exist
    """
    test_path = "com/example/test/1.0/test-1.0.jar"
    local_path = maven_routes.MAVEN_CACHE / Path(*test_path.split("/"))

    # Patch Path.exists to simulate file missing
    with patch.object(Path, "exists", return_value=False):
        mock_response.return_value = b"file content"
        response = await maven_routes.maven_proxy(test_path, request=AsyncMock())

        # Ensure fetch_and_cache is called with safe path
        mock_fetch.assert_called_once()
        called_path = mock_fetch.call_args[0][1]
        assert str(local_path.resolve()) == str(called_path.resolve())

        mock_response.assert_called_once()


@pytest.mark.asyncio
@patch("app.routes.maven_routes.utils.conditional_file_response", new_callable=AsyncMock)
@patch("app.routes.maven_routes.utils.fetch_and_cache", new_callable=AsyncMock)
async def test_maven_proxy_cached(mock_fetch, mock_response):
    """
    Test fetching a file that exists in local cache
    """
    test_path = "com/example/test/1.0/test-1.0.jar"
    local_path = maven_routes.MAVEN_CACHE / Path(*test_path.split("/"))

    # File exists -> fetch_and_cache should NOT be called
    with patch.object(Path, "exists", return_value=True):
        mock_response.return_value = b"cached content"
        response = await maven_routes.maven_proxy(test_path, request=AsyncMock())

        mock_fetch.assert_not_called()
        mock_response.assert_called_once()


@pytest.mark.asyncio
@patch("app.routes.maven_routes.utils.conditional_file_response", new_callable=AsyncMock)
@patch("app.routes.maven_routes.utils.fetch_and_cache", new_callable=AsyncMock)
async def test_maven_proxy_file_not_found(mock_fetch, mock_response):
    """
    Test that a FileNotFoundError inside conditional_file_response
    raises HTTPException with 404 status code
    """
    test_path = "com/example/missing/1.0/missing-1.0.jar"

    # File exists, but conditional_file_response raises FileNotFoundError
    with patch.object(Path, "exists", return_value=True):
        mock_response.side_effect = FileNotFoundError
        with pytest.raises(HTTPException) as exc_info:
            await maven_routes.maven_proxy(test_path, request=AsyncMock())
        assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_maven_proxy_rejects_absolute_path():
    """
    Test that an absolute path raises HTTPException (400)
    """
    test_path = "/etc/passwd"
    with pytest.raises(HTTPException) as exc_info:
        await maven_routes.maven_proxy(test_path, request=AsyncMock())
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_maven_proxy_rejects_traversal_path():
    """
    Test that a path traversal attempt raises HTTPException (400)
    """
    test_path = "../evil.txt"
    with pytest.raises(HTTPException) as exc_info:
        await maven_routes.maven_proxy(test_path, request=AsyncMock())
    assert exc_info.value.status_code == 400


# ========================================
# Additional Coverage Tests
# ========================================

@pytest.mark.asyncio
@patch("app.routes.maven_routes.utils.is_cache_stale", return_value=True)
@patch("app.routes.maven_routes.utils.conditional_file_response", new_callable=AsyncMock)
@patch("app.routes.maven_routes.utils.fetch_and_cache", new_callable=AsyncMock)
async def test_maven_proxy_metadata_stale_refresh(mock_fetch, mock_response, mock_stale):
    """Test that stale metadata (.xml, .pom) is refreshed."""
    test_path = "com/example/test/1.0/test-1.0.pom"

    with patch.object(Path, "exists", return_value=True):
        mock_response.return_value = b"pom content"
        response = await maven_routes.maven_proxy(test_path, request=AsyncMock())
        mock_fetch.assert_called_once()
        mock_response.assert_called_once()


@pytest.mark.asyncio
@patch("app.routes.maven_routes.utils.is_cache_stale", return_value=False)
@patch("app.routes.maven_routes.utils.conditional_file_response", new_callable=AsyncMock)
@patch("app.routes.maven_routes.utils.fetch_and_cache", new_callable=AsyncMock)
async def test_maven_proxy_metadata_fresh_cache(mock_fetch, mock_response, mock_stale):
    """Test that fresh metadata is not re-fetched."""
    test_path = "com/example/test/1.0/test-1.0.pom"

    with patch.object(Path, "exists", return_value=True):
        mock_response.return_value = b"pom content"
        response = await maven_routes.maven_proxy(test_path, request=AsyncMock())
        mock_fetch.assert_not_called()
        mock_response.assert_called_once()


@pytest.mark.asyncio
@patch("app.routes.maven_routes.utils.conditional_file_response", new_callable=AsyncMock)
@patch("app.routes.maven_routes.utils.fetch_and_cache", new_callable=AsyncMock)
async def test_maven_proxy_artifact_cached(mock_fetch, mock_response):
    """Test that artifact files (.jar) are never refreshed when cached."""
    test_path = "com/example/test/1.0/test-1.0.jar"

    with patch.object(Path, "exists", return_value=True):
        mock_response.return_value = b"jar content"
        response = await maven_routes.maven_proxy(test_path, request=AsyncMock())
        mock_fetch.assert_not_called()
        mock_response.assert_called_once()


@pytest.mark.asyncio
async def test_maven_proxy_raises_validation_error():
    """Test that ValidationError from safe_join_path raises HTTPException(400)."""
    from app.validators import ValidationError

    with patch("app.routes.maven_routes.safe_join_path", side_effect=ValidationError("Invalid path")):
        with pytest.raises(HTTPException) as exc_info:
            await maven_routes.maven_proxy("com/example/test", request=AsyncMock())
        assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_maven_proxy_metadata_sha1():
    """Test that .sha1 files are treated as metadata."""
    test_path = "com/example/test/1.0/test-1.0.jar.sha1"

    with patch("app.routes.maven_routes.utils.is_cache_stale", return_value=True) as mock_stale:
        with patch("app.routes.maven_routes.utils.conditional_file_response", new_callable=AsyncMock) as mock_response:
            with patch("app.routes.maven_routes.utils.fetch_and_cache", new_callable=AsyncMock) as mock_fetch:
                with patch.object(Path, "exists", return_value=True):
                    mock_response.return_value = b"sha1hash"
                    await maven_routes.maven_proxy(test_path, request=AsyncMock())
                    mock_fetch.assert_called_once()


@pytest.mark.asyncio
async def test_maven_proxy_metadata_md5():
    """Test that .md5 files are treated as metadata."""
    test_path = "com/example/test/1.0/test-1.0.jar.md5"

    with patch("app.routes.maven_routes.utils.is_cache_stale", return_value=True) as mock_stale:
        with patch("app.routes.maven_routes.utils.conditional_file_response", new_callable=AsyncMock) as mock_response:
            with patch("app.routes.maven_routes.utils.fetch_and_cache", new_callable=AsyncMock) as mock_fetch:
                with patch.object(Path, "exists", return_value=True):
                    mock_response.return_value = b"md5hash"
                    await maven_routes.maven_proxy(test_path, request=AsyncMock())
                    mock_fetch.assert_called_once()


@pytest.mark.asyncio
async def test_maven_proxy_metadata_xml():
    """Test that .xml files are treated as metadata."""
    test_path = "com/example/test/maven-metadata.xml"

    with patch("app.routes.maven_routes.utils.is_cache_stale", return_value=True) as mock_stale:
        with patch("app.routes.maven_routes.utils.conditional_file_response", new_callable=AsyncMock) as mock_response:
            with patch("app.routes.maven_routes.utils.fetch_and_cache", new_callable=AsyncMock) as mock_fetch:
                with patch.object(Path, "exists", return_value=True):
                    mock_response.return_value = b"<metadata></metadata>"
                    await maven_routes.maven_proxy(test_path, request=AsyncMock())
                    mock_fetch.assert_called_once()


@pytest.mark.asyncio
@patch("app.routes.maven_routes.utils.is_cache_stale", return_value=True)
@patch("app.routes.maven_routes.utils.conditional_file_response", new_callable=AsyncMock)
@patch("app.routes.maven_routes.utils.fetch_and_cache", new_callable=AsyncMock)
async def test_maven_head_proxy_metadata_sha1(mock_fetch, mock_conditional, mock_stale):
    """Test that .sha1 files are treated as metadata for HEAD and are refreshed if stale."""
    test_path = "com/example/test/1.0/test-1.0.jar.sha1"
    with patch.object(Path, "exists", return_value=True):
        mock_conditional.return_value = type(
            "Resp", (), {"status_code": 200, "headers": {}})()
        await maven_routes.maven_head_proxy(test_path, request=AsyncMock())
        mock_fetch.assert_called_once()
        mock_conditional.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.asyncio
@patch("app.routes.maven_routes.utils.is_cache_stale", return_value=True)
@patch("app.routes.maven_routes.utils.conditional_file_response", new_callable=AsyncMock)
@patch("app.routes.maven_routes.utils.fetch_and_cache", new_callable=AsyncMock)
async def test_maven_head_proxy_metadata_md5(mock_fetch, mock_conditional, mock_stale):
    """Test that .md5 files are treated as metadata for HEAD and are refreshed if stale."""
    test_path = "com/example/test/1.0/test-1.0.jar.md5"
    with patch.object(Path, "exists", return_value=True):
        mock_conditional.return_value = type(
            "Resp", (), {"status_code": 200, "headers": {}})()
        await maven_routes.maven_head_proxy(test_path, request=AsyncMock())
        mock_fetch.assert_called_once()
        mock_conditional.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.asyncio
@patch("app.routes.maven_routes.utils.is_cache_stale", return_value=True)
@patch("app.routes.maven_routes.utils.conditional_file_response", new_callable=AsyncMock)
@patch("app.routes.maven_routes.utils.fetch_and_cache", new_callable=AsyncMock)
async def test_maven_head_proxy_metadata_xml(mock_fetch, mock_conditional, mock_stale):
    """Test that .xml files are treated as metadata for HEAD and are refreshed if stale."""
    test_path = "com/example/test/maven-metadata.xml"
    with patch.object(Path, "exists", return_value=True):
        mock_conditional.return_value = type(
            "Resp", (), {"status_code": 200, "headers": {}})()
        await maven_routes.maven_head_proxy(test_path, request=AsyncMock())
        mock_fetch.assert_called_once()
        mock_conditional.assert_called_once()


@pytest.mark.asyncio
@patch("app.routes.maven_routes.utils.conditional_file_response", new_callable=AsyncMock)
async def test_maven_head_proxy_conditional_error(mock_conditional):
    """Test that FileNotFoundError in conditional_file_response for HEAD propagates as 404."""
    test_path = "com/example/test/1.0/test-1.0.jar"
    with patch.object(Path, "exists", return_value=True):
        mock_conditional.side_effect = FileNotFoundError
        with pytest.raises(HTTPException) as exc_info:
            await maven_routes.maven_head_proxy(test_path, request=AsyncMock())
        assert exc_info.value.status_code == 404
