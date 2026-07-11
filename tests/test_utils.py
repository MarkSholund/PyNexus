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
from unittest.mock import AsyncMock, MagicMock
# from pathlib import Path
import json
# import os
# from datetime import datetime
from fastapi.responses import Response
import app.utils as utils
from http import HTTPMethod


@pytest.fixture(autouse=True)
def patch_cache_dir(tmp_path, monkeypatch):
    """Force config.CACHE_DIR to a safe temp directory for all tests."""
    monkeypatch.setattr(utils.config, "CACHE_DIR", tmp_path)
    yield


# ------------------------
# safe_cache_path
# ------------------------

def test_safe_cache_path_allows_nested(monkeypatch, tmp_path):
    path = utils.safe_cache_path(tmp_path, "nested", "dir", "file.txt")
    assert path == tmp_path / "nested" / "dir" / "file.txt"


def test_safe_cache_path_rejects_traversal(tmp_path):
    with pytest.raises(ValueError):
        utils.safe_cache_path(tmp_path, "../evil.txt")


def test_safe_cache_path_rejects_absolute(tmp_path):
    with pytest.raises(ValueError):
        utils.safe_cache_path(tmp_path, "/etc/passwd")


# ------------------------
# open_cached_file
# ------------------------

def test_open_cached_file(tmp_path):
    file_path = tmp_path / "test.txt"
    file_path.write_bytes(b"hello")
    content = utils.open_cached_file(file_path)
    assert content == b"hello"

    missing_file = tmp_path / "missing.txt"
    with pytest.raises(FileNotFoundError):
        utils.open_cached_file(missing_file)


# ------------------------
# make_etag_and_last_modified
# ------------------------

def test_make_etag_and_last_modified(tmp_path):
    file_path = tmp_path / "file.txt"
    file_path.write_text("abc")
    etag, last_modified = utils.make_etag_and_last_modified(file_path)
    assert isinstance(etag, str)
    assert isinstance(last_modified, str)
    assert len(etag) == 64  # sha256 hash length


# ------------------------
# file_headers
# ------------------------

def test_file_headers(tmp_path):
    file_path = tmp_path / "file.txt"
    file_path.write_text("abc")
    headers = utils.file_headers(file_path)
    assert "ETag" in headers
    assert "Last-Modified" in headers


# ------------------------
# conditional_file_response
# ------------------------

@pytest.mark.asyncio
async def test_conditional_file_response_returns_content(tmp_path):
    file_path = tmp_path / "file.txt"
    file_path.write_text("abc")

    request = MagicMock()
    request.headers = {}

    response = await utils.conditional_file_response(request, file_path, "text/plain")
    assert isinstance(response, Response)
    assert response.status_code == 200
    assert response.body == b"abc"
    assert "ETag" in response.headers
    assert "Last-Modified" in response.headers


@pytest.mark.asyncio
async def test_conditional_file_response_304(tmp_path):
    file_path = tmp_path / "file.txt"
    file_path.write_text("abc")

    etag, last_modified = utils.make_etag_and_last_modified(file_path)

    request = MagicMock()
    request.headers = {"If-None-Match": etag}
    response = await utils.conditional_file_response(request, file_path, "text/plain")
    assert response.status_code == 304

    request.headers = {"If-Modified-Since": last_modified}
    response = await utils.conditional_file_response(request, file_path, "text/plain")
    assert response.status_code == 304


@pytest.mark.asyncio
async def test_conditional_file_response_rejects_symlink(tmp_path):
    """A symlink under the cache dir must never be served, even if its
    target resolves to somewhere inside the cache root."""
    real_file = tmp_path / "real.txt"
    real_file.write_text("secret-ish content")

    link_path = tmp_path / "link.txt"
    link_path.symlink_to(real_file)

    request = MagicMock()
    request.headers = {}

    with pytest.raises(FileNotFoundError):
        await utils.conditional_file_response(request, link_path, "text/plain")


@pytest.mark.asyncio
async def test_conditional_file_response_rejects_symlink_escaping_cache(tmp_path):
    """A symlink pointing outside the cache root must be refused."""
    outside_dir = tmp_path.parent / "outside_secret"
    outside_dir.mkdir(exist_ok=True)
    outside_file = outside_dir / "passwd.txt"
    outside_file.write_text("root:x:0:0")

    link_path = tmp_path / "escape.txt"
    link_path.symlink_to(outside_file)

    request = MagicMock()
    request.headers = {}

    with pytest.raises(FileNotFoundError):
        await utils.conditional_file_response(request, link_path, "text/plain")


@pytest.mark.asyncio
async def test_conditional_file_response_rejects_outside_cache(tmp_path):
    file_path = tmp_path.parent / "outside.txt"
    file_path.write_text("abc")
    request = MagicMock()
    request.headers = {}
    with pytest.raises(FileNotFoundError):
        await utils.conditional_file_response(request, file_path, "text/plain")


# ------------------------
# fetch_and_cache
# ------------------------

@pytest.mark.asyncio
async def test_fetch_and_cache_get_bytes(monkeypatch, tmp_path):
    """Simulate GET fetch with byte content"""
    url = "http://example.com/data.txt"
    dest = tmp_path / "data.txt"

    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b"abc"
    mock_resp.text = "abc"
    mock_resp.raise_for_status = MagicMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.get.return_value = mock_resp
    monkeypatch.setattr(utils.httpx, "AsyncClient", lambda **_: mock_client)

    result = await utils.fetch_and_cache(url, dest)
    assert dest.exists()
    assert dest.read_bytes() == b"abc"
    assert result == dest


@pytest.mark.asyncio
async def test_fetch_and_cache_post_json(monkeypatch, tmp_path):
    """Simulate POST fetch returning JSON"""
    url = "http://example.com/api"
    dest = tmp_path / "resp.json"
    data = b'{"query":1}'

    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"ok":true}'
    mock_resp.content = b'{"ok":true}'
    mock_resp.raise_for_status = MagicMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.post.return_value = mock_resp
    monkeypatch.setattr(utils.httpx, "AsyncClient", lambda **_: mock_client)

    result = await utils.fetch_and_cache(
        url, dest, method=HTTPMethod.POST, data=data, return_json=True
    )
    assert dest.exists()
    with open(dest) as f:
        saved = json.load(f)
    assert saved == {"ok": True}
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_fetch_and_cache_raises_http_exception(monkeypatch, tmp_path):
    """Simulate upstream 404"""
    url = "http://example.com/missing"
    dest = tmp_path / "missing.txt"

    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.raise_for_status.side_effect = utils.httpx.HTTPStatusError(
        "Not Found", request=None, response=mock_resp
    )
    mock_client.__aenter__.return_value = mock_client
    mock_client.get.return_value = mock_resp
    monkeypatch.setattr(utils.httpx, "AsyncClient", lambda **_: mock_client)

    with pytest.raises(utils.HTTPException) as exc:
        await utils.fetch_and_cache(url, dest)
    assert exc.value.status_code == 404


# ------------------------
# Additional safety checks
# ------------------------

@pytest.mark.asyncio
async def test_fetch_and_cache_refuses_outside_cache(monkeypatch, tmp_path):
    """Destination outside cache should raise HTTPException(400)."""
    outside = tmp_path.parent / "escape.txt"
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    monkeypatch.setattr(utils.httpx, "AsyncClient", lambda **_: mock_client)

    with pytest.raises(utils.HTTPException) as exc:
        await utils.fetch_and_cache("http://example.com", outside)
    assert exc.value.status_code == 400


def test_safe_cache_path_preserves_relative(tmp_path):
    p = utils.safe_cache_path(tmp_path, "a/b/c.txt")
    assert str(tmp_path) in str(p)


# ========================================
# Additional Coverage Tests
# ========================================

def test_safe_cache_path_rejects_double_dot(tmp_path):
    """Test explicit rejection of .. segment."""
    with pytest.raises(ValueError, match="Path traversal not allowed"):
        utils.safe_cache_path(tmp_path, "a/..")


def test_safe_cache_path_rejects_single_dot_slash(tmp_path):
    """Test rejection of paths starting with ./"""
    # ./evil should be rejected if it contains .. or is otherwise malicious
    # In this case, it's a valid relative path, so it should work
    p = utils.safe_cache_path(tmp_path, "./file.txt")
    assert p == tmp_path / "file.txt"


def test_safe_cache_path_rejects_backslash_prefix(tmp_path):
    """Test rejection of paths starting with backslash (Windows UNC)."""
    with pytest.raises(ValueError, match="Absolute path not allowed"):
        utils.safe_cache_path(tmp_path, "\\absolute")


def test_safe_cache_path_rejects_drive_letter(tmp_path):
    """Test drive letter rejection (Windows)."""
    with pytest.raises(ValueError, match="Drive letter path not allowed"):
        utils.safe_cache_path(tmp_path, "C:\\evil")
    # with pytest.raises(ValueError, match="Absolute path not allowed"):
    #     utils.safe_cache_path(tmp_path, "C:\\evil")

def test_safe_cache_path_handles_none_parts(tmp_path):
    """Test that None parts are skipped."""
    p = utils.safe_cache_path(tmp_path, "a", None, "b.txt")
    assert p == tmp_path / "a" / "b.txt"


def test_open_cached_file_rejects_directory(tmp_path):
    """Test that open_cached_file rejects directories."""
    dir_path = tmp_path / "subdir"
    dir_path.mkdir()
    with pytest.raises(FileNotFoundError):
        utils.open_cached_file(dir_path)


@pytest.mark.asyncio
async def test_fetch_and_cache_returns_existing_file_bytes(monkeypatch, tmp_path):
    """Test that fetch_and_cache returns existing file without fetching."""
    dest = tmp_path / "data.txt"
    dest.write_bytes(b"cached content")
    
    result = await utils.fetch_and_cache("http://example.com/data.txt", dest)
    assert result == dest
    assert dest.read_bytes() == b"cached content"


@pytest.mark.asyncio
async def test_fetch_and_cache_returns_existing_file_json(monkeypatch, tmp_path):
    """Test that fetch_and_cache returns existing JSON without fetching."""
    dest = tmp_path / "data.json"
    dest.write_text('{"cached": true}')
    
    result = await utils.fetch_and_cache(
        "http://example.com/data.json", dest, return_json=True
    )
    assert result == {"cached": True}


@pytest.mark.asyncio
async def test_conditional_file_response_not_found(tmp_path):
    """Test that conditional_file_response raises FileNotFoundError for missing files."""
    file_path = tmp_path / "missing.txt"
    request = MagicMock()
    request.headers = {}
    
    with pytest.raises(FileNotFoundError):
        await utils.conditional_file_response(request, file_path, "text/plain")


@pytest.mark.asyncio
async def test_conditional_file_response_with_attachment(tmp_path):
    """Test that conditional_file_response sets Content-Disposition for attachments."""
    file_path = tmp_path / "file.bin"
    file_path.write_bytes(b"binary data")
    
    request = MagicMock()
    request.headers = {}
    
    response = await utils.conditional_file_response(
        request, file_path, "application/octet-stream", attachment=True
    )
    assert response.status_code == 200
    assert "Content-Disposition" in response.headers
    assert 'attachment; filename="file.bin"' in response.headers["Content-Disposition"]


@pytest.mark.asyncio
async def test_conditional_file_response_if_none_match_case_insensitive(tmp_path):
    """Test that if-none-match header works with lowercase key."""
    file_path = tmp_path / "file.txt"
    file_path.write_text("content")
    
    etag, _ = utils.make_etag_and_last_modified(file_path)
    
    request = MagicMock()
    request.headers = {"if-none-match": etag}
    
    response = await utils.conditional_file_response(request, file_path, "text/plain")
    assert response.status_code == 304


@pytest.mark.asyncio
async def test_conditional_file_response_if_modified_since_case_insensitive(tmp_path):
    """Test that if-modified-since header works with lowercase key."""
    file_path = tmp_path / "file.txt"
    file_path.write_text("content")
    
    _, last_modified = utils.make_etag_and_last_modified(file_path)
    
    request = MagicMock()
    request.headers = {"if-modified-since": last_modified}
    
    response = await utils.conditional_file_response(request, file_path, "text/plain")
    assert response.status_code == 304


@pytest.mark.asyncio
async def test_fetch_and_cache_post_without_json(monkeypatch, tmp_path):
    """Test POST request returning bytes (not JSON)."""
    url = "http://example.com/upload"
    dest = tmp_path / "result.bin"
    
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b"binary result"
    mock_resp.raise_for_status = MagicMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.post.return_value = mock_resp
    monkeypatch.setattr(utils.httpx, "AsyncClient", lambda **_: mock_client)
    
    result = await utils.fetch_and_cache(
        url, dest, method=HTTPMethod.POST, data=b"request data", return_json=False
    )
    assert dest.exists()
    assert dest.read_bytes() == b"binary result"
    assert result == dest


@pytest.mark.asyncio
async def test_fetch_and_cache_timeout_parameter(monkeypatch, tmp_path):
    """Test that timeout parameter is passed to AsyncClient."""
    url = "http://example.com/data.txt"
    dest = tmp_path / "data.txt"
    
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b"data"
    mock_resp.raise_for_status = MagicMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.get.return_value = mock_resp
    
    client_init_args = {}
    def mock_client_init(**kwargs):
        client_init_args.update(kwargs)
        return mock_client
    
    monkeypatch.setattr(utils.httpx, "AsyncClient", mock_client_init)
    
    await utils.fetch_and_cache(url, dest, timeout=120.0)
    assert client_init_args.get("timeout") == 120.0


def test_safe_cache_path_multiple_absolute_attempts(tmp_path):
    """Test rejection of multiple parts that try absolute path injection."""
    with pytest.raises(ValueError):
        utils.safe_cache_path(tmp_path, "/abs1", "/abs2")


@pytest.mark.asyncio
async def test_conditional_file_response_case_insensitive_headers(tmp_path):
    """Test that uppercase header keys are also supported."""
    file_path = tmp_path / "file.txt"
    file_path.write_text("content")
    
    etag, last_modified = utils.make_etag_and_last_modified(file_path)
    
    # Test with uppercase If-None-Match
    request = MagicMock()
    request.headers = {"If-None-Match": etag}
    response = await utils.conditional_file_response(request, file_path, "text/plain")
    assert response.status_code == 304
    
    # Test with uppercase If-Modified-Since
    request = MagicMock()
    request.headers = {"If-Modified-Since": last_modified}
    response = await utils.conditional_file_response(request, file_path, "text/plain")
    assert response.status_code == 304
