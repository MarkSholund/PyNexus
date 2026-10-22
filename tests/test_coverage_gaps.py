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

"""Tests for previously uncovered error/fallback branches."""

import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx2
import pytest
from fastapi import HTTPException

from app import config, utils
from app.routes import maven_routes, npm_routes, pypi_routes
from app.validators import (
    ValidationError,
    safe_join_path,
    validate_maven_path,
    validate_tarball_name,
    validate_version_string,
)

TWO_DAYS = 2 * 24 * 3600


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(npm_routes, "NPM_CACHE", tmp_path / "npm")
    monkeypatch.setattr(maven_routes, "MAVEN_CACHE", tmp_path / "maven")
    monkeypatch.setattr(pypi_routes, "PYPI_CACHE", tmp_path / "pypi")
    return tmp_path


def _age(path, seconds):
    old = time.time() - seconds
    os.utime(path, (old, old))


def _request(headers=None, body=b'{"requires": {}}'):
    req = MagicMock()
    req.headers = {} if headers is None else headers
    req.body = AsyncMock(return_value=body)
    return req


def _fake_client(get=None):
    client = MagicMock()
    client.get = get
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return patch("app.routes.pypi_routes.httpx2.AsyncClient", return_value=cm)


def _make_dir_symlink(link, target):
    """
    Create a directory symlink, or skip the test if the OS won't allow it.

    On Windows, creating symlinks needs Developer Mode or an elevated shell
    (otherwise OSError / WinError 1314), and directory links must be created
    with target_is_directory=True.
    """
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"symlinks not available on this system: {e}")


def _resp(status=200, text=""):
    r = MagicMock()
    r.status_code = status
    r.text = text
    return r


# ----------------------------------------------------------------------
# npm audit endpoints
# ----------------------------------------------------------------------
AUDIT_ENDPOINTS = [
    pytest.param(npm_routes.npm_security_audit_quick, id="quick"),
    pytest.param(npm_routes.npm_security_bulk, id="bulk"),
]


class _ItemsRaises:
    """headers object whose .items() fails but is still dict()-able."""

    def items(self):
        raise RuntimeError("no items")

    def keys(self):
        return ["x-a"]

    def __getitem__(self, key):
        return "1"


class _ItemsAndDictRaise:
    def items(self):
        raise RuntimeError("no items")


class _AsyncItems:
    def items(self):
        async def _coro():
            return [("x-a", "1")]

        return _coro()


class _AsyncItemsRaises:
    def items(self):
        async def _coro():
            raise RuntimeError("boom")

        return _coro()


class _NonIterableItems:
    def items(self):
        return 5


class _TupleItems:
    def items(self):
        return [("x-a", "1")]


HEADER_CASES = [
    pytest.param({"x-a": "1"}, {"x-a": "1"}, id="dict"),
    pytest.param(_TupleItems(), {"x-a": "1"}, id="items-list"),
    pytest.param(_ItemsRaises(), {"x-a": "1"}, id="items-raises-dict-fallback"),
    pytest.param(_ItemsAndDictRaise(), {}, id="items-and-dict-raise"),
    pytest.param(_AsyncItems(), {"x-a": "1"}, id="awaitable-items"),
    pytest.param(_AsyncItemsRaises(), {}, id="awaitable-items-raises"),
    pytest.param(_NonIterableItems(), {}, id="items-not-iterable"),
]


@pytest.mark.parametrize("endpoint", AUDIT_ENDPOINTS)
@pytest.mark.parametrize("raw_headers,expected", HEADER_CASES)
@pytest.mark.asyncio
async def test_npm_audit_header_extraction(cache, endpoint, raw_headers, expected):
    fetch = AsyncMock(return_value={"ok": True})
    with patch("app.routes.npm_routes.utils.fetch_and_cache", fetch):
        result = await endpoint(_request(raw_headers))

    assert result == {"ok": True}
    sent = fetch.call_args.kwargs["headers"]
    assert sent == {**expected, "content-type": "application/json"}


@pytest.mark.parametrize("endpoint", AUDIT_ENDPOINTS)
@pytest.mark.asyncio
async def test_npm_audit_preserves_client_content_type(cache, endpoint):
    fetch = AsyncMock(return_value={})
    with patch("app.routes.npm_routes.utils.fetch_and_cache", fetch):
        await endpoint(_request({"content-type": "application/x-test"}))
    assert fetch.call_args.kwargs["headers"]["content-type"] == "application/x-test"


@pytest.mark.parametrize("endpoint", AUDIT_ENDPOINTS)
@pytest.mark.asyncio
async def test_npm_audit_invalid_cache_path_returns_400(cache, endpoint):
    with patch(
        "app.routes.npm_routes.safe_join_path", side_effect=ValidationError("bad")
    ):
        with pytest.raises(HTTPException) as exc:
            await endpoint(_request())
    assert exc.value.status_code == 400


@pytest.mark.parametrize("endpoint", AUDIT_ENDPOINTS)
@pytest.mark.asyncio
async def test_npm_audit_stale_cache_forces_refresh(cache, endpoint):
    fetch = AsyncMock(return_value={"fresh": True})
    with (
        patch("app.routes.npm_routes.utils.is_cache_stale", return_value=True),
        patch("app.routes.npm_routes.utils.fetch_and_cache", fetch),
    ):
        result = await endpoint(_request())
    assert result == {"fresh": True}
    assert fetch.call_args.kwargs["force_refresh"] is True


@pytest.mark.parametrize("endpoint", AUDIT_ENDPOINTS)
@pytest.mark.asyncio
async def test_npm_audit_fresh_cache_served_without_fetch(cache, endpoint):
    fetch = AsyncMock()
    cond = AsyncMock(return_value="cached-response")
    with (
        patch("app.routes.npm_routes.utils.is_cache_stale", return_value=False),
        patch("app.routes.npm_routes.utils.conditional_file_response", cond),
        patch("app.routes.npm_routes.utils.fetch_and_cache", fetch),
    ):
        result = await endpoint(_request())
    assert result == "cached-response"
    fetch.assert_not_called()


@pytest.mark.parametrize("endpoint", AUDIT_ENDPOINTS)
@pytest.mark.asyncio
async def test_npm_audit_fresh_but_missing_file_fetches_upstream(cache, endpoint):
    fetch = AsyncMock(return_value={"fetched": True})
    cond = AsyncMock(side_effect=FileNotFoundError)
    with (
        patch("app.routes.npm_routes.utils.is_cache_stale", return_value=False),
        patch("app.routes.npm_routes.utils.conditional_file_response", cond),
        patch("app.routes.npm_routes.utils.fetch_and_cache", fetch),
    ):
        result = await endpoint(_request())
    assert result == {"fetched": True}
    assert "force_refresh" not in fetch.call_args.kwargs


# ----------------------------------------------------------------------
# pypi routes
# ----------------------------------------------------------------------
def _seed_pypi_index(cache, body="<html>old</html>", age=None):
    f = cache / "pypi" / "simple" / "requests" / "index.html"
    f.parent.mkdir(parents=True)
    f.write_text(body)
    if age:
        _age(f, age)
    return f


@pytest.mark.asyncio
async def test_pypi_package_index_invalid_name_400(cache):
    with pytest.raises(HTTPException) as exc:
        await pypi_routes.pypi_package_index("bad name!", _request())
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_pypi_package_index_unsafe_path_400(cache):
    with patch(
        "app.routes.pypi_routes.safe_join_path", side_effect=ValidationError("bad")
    ):
        with pytest.raises(HTTPException) as exc:
            await pypi_routes.pypi_package_index("requests", _request())
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_pypi_package_index_refreshes_and_rewrites(cache):
    html = '<a href="https://files.pythonhosted.org/packages/ab/cd/x.whl">x</a>'
    with _fake_client(AsyncMock(return_value=_resp(200, html))):
        await pypi_routes.pypi_package_index("requests", _request())
    written = (cache / "pypi" / "simple" / "requests" / "index.html").read_text()
    assert 'href="/pypi/packages/ab/cd/x.whl"' in written


@pytest.mark.asyncio
async def test_pypi_package_index_upstream_error_no_cache_propagates(cache):
    with _fake_client(AsyncMock(return_value=_resp(404))):
        with pytest.raises(HTTPException) as exc:
            await pypi_routes.pypi_package_index("requests", _request())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_pypi_package_index_upstream_error_serves_stale(cache):
    f = _seed_pypi_index(cache, age=TWO_DAYS * 100)
    with _fake_client(AsyncMock(return_value=_resp(500))):
        resp = await pypi_routes.pypi_package_index("requests", _request())
    assert b"old" in resp.body
    assert f.read_text() == "<html>old</html>"


@pytest.mark.asyncio
async def test_pypi_package_index_network_error_no_cache_503(cache):
    with _fake_client(AsyncMock(side_effect=httpx2.ConnectError("down"))):
        with pytest.raises(HTTPException) as exc:
            await pypi_routes.pypi_package_index("requests", _request())
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_pypi_package_index_network_error_serves_stale(cache):
    _seed_pypi_index(cache, age=TWO_DAYS * 100)
    with _fake_client(AsyncMock(side_effect=httpx2.ConnectError("down"))):
        resp = await pypi_routes.pypi_package_index("requests", _request())
    assert b"old" in resp.body


@pytest.mark.asyncio
async def test_pypi_root_index_upstream_error_raises(cache):
    with _fake_client(AsyncMock(return_value=_resp(502))):
        with pytest.raises(HTTPException) as exc:
            await pypi_routes.pypi_root_index(_request())
    assert exc.value.status_code == 502


def test_rewrite_index_html_relative_pypi_query_and_fragment():
    html = (
        '<a href="/pypi/simple/foo/">a</a>'
        '<a href="https://files.pythonhosted.org/packages/a/b.whl?x=1#sha256=abc">b</a>'
    )
    out = pypi_routes.rewrite_index_html(html, base_url="/pypi")
    assert 'href="/pypi/pypi/simple/foo/"' in out
    assert 'href="/pypi/packages/a/b.whl?x=1#sha256=abc"' in out


@pytest.mark.asyncio
async def test_pypi_artifact_unsafe_cache_path_400(cache):
    with patch(
        "app.routes.pypi_routes.utils.safe_cache_path", side_effect=ValueError("x")
    ):
        with pytest.raises(HTTPException) as exc:
            await pypi_routes.pypi_artifact("ab/cd/pkg-1.0.whl", _request())
    assert exc.value.status_code == 400


# ----------------------------------------------------------------------
# maven HEAD
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_maven_head_invalid_path_400(cache):
    with pytest.raises(HTTPException) as exc:
        await maven_routes.maven_head_proxy("../etc/passwd", _request())
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_maven_head_unsafe_join_400(cache):
    with patch(
        "app.routes.maven_routes.safe_join_path", side_effect=ValidationError("bad")
    ):
        with pytest.raises(HTTPException) as exc:
            await maven_routes.maven_head_proxy("org/example/lib.jar", _request())
    assert exc.value.status_code == 400


# ----------------------------------------------------------------------
# utils
# ----------------------------------------------------------------------
def test_safe_cache_path_skips_none_and_stringifies(tmp_path):
    p = utils.safe_cache_path(tmp_path, None, 5, "f.txt")
    assert p == tmp_path.resolve() / "5" / "f.txt"


def test_safe_cache_path_rejects_unc(tmp_path):
    with pytest.raises(ValueError, match="UNC"):
        utils.safe_cache_path(tmp_path, "\\\\server\\share")


def test_safe_cache_path_rejects_symlink_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    _make_dir_symlink(root / "link", outside)
    with pytest.raises(ValueError, match="outside cache"):
        utils.safe_cache_path(root, "link", "x.txt")


def test_safe_cache_path_resolve_fallback(tmp_path):
    real_resolve = Path.resolve

    def fake_resolve(self, strict=False):
        if self.name == "file.txt":
            raise FileNotFoundError
        return real_resolve(self, strict)

    with patch.object(Path, "resolve", fake_resolve):
        p = utils.safe_cache_path(tmp_path, "dir", "file.txt")
    assert p == tmp_path.resolve() / "dir" / "file.txt"


@pytest.mark.asyncio
async def test_fetch_and_cache_network_error_is_502(cache):
    client = MagicMock()
    client.get = AsyncMock(side_effect=httpx2.ConnectError("down"))
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    with patch("app.utils.httpx2.AsyncClient", return_value=cm):
        with pytest.raises(HTTPException) as exc:
            await utils.fetch_and_cache("https://x.test/a", cache / "a.json")
    assert exc.value.status_code == 502
    assert not (cache / "a.json").exists()


def test_atomic_write_cleans_up_temp_file_on_failure(tmp_path):
    dest = tmp_path / "out.bin"
    with patch("app.utils.os.replace", side_effect=OSError("disk")):
        with pytest.raises(OSError):
            utils._atomic_write(dest, b"data")
    assert list(tmp_path.iterdir()) == []


def test_atomic_write_tolerates_temp_file_already_gone(tmp_path):
    dest = tmp_path / "out.bin"

    def replace_and_vanish(src, dst):
        os.unlink(src)
        raise OSError("disk")

    with patch("app.utils.os.replace", side_effect=replace_and_vanish):
        with pytest.raises(OSError):
            utils._atomic_write(dest, b"data")


def test_is_cache_stale_stat_error_is_stale(tmp_path):
    f = tmp_path / "f"
    with (
        patch.object(Path, "exists", return_value=True),
        patch.object(Path, "stat", side_effect=OSError("denied")),
    ):
        assert utils.is_cache_stale(f, max_age_hours=24) is True


@pytest.mark.asyncio
async def test_fetch_and_serve_json_fetches_on_miss(cache):
    dest = cache / "sub" / "doc.json"

    async def fake_fetch(url, path, return_json=False):
        path.write_text("{}")
        return path

    fetch = AsyncMock(side_effect=fake_fetch)
    cond = AsyncMock(return_value="resp")
    with (
        patch("app.utils.fetch_and_cache", fetch),
        patch("app.utils.conditional_file_response", cond),
    ):
        result = await utils.fetch_and_serve_json(
            "https://x.test/doc", dest, _request()
        )

    assert result == "resp"
    fetch.assert_awaited_once()
    cond.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_and_serve_json_skips_fetch_on_hit(cache):
    dest = cache / "doc.json"
    dest.write_text("{}")
    fetch = AsyncMock()
    cond = AsyncMock(return_value="resp")
    with (
        patch("app.utils.fetch_and_cache", fetch),
        patch("app.utils.conditional_file_response", cond),
    ):
        await utils.fetch_and_serve_json("https://x.test/doc", dest, _request())
    fetch.assert_not_called()


# ----------------------------------------------------------------------
# validators
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "version", ["", "1" * 101, "1..2", "1/2", "1\\2", "1\x002", "1 2", "1$"]
)
def test_validate_version_string_rejects(version):
    assert validate_version_string(version) is False


@pytest.mark.parametrize("version", ["1.2.3", "2.0.0-beta.1", "1.0+local"])
def test_validate_version_string_accepts(version):
    assert validate_version_string(version) is True


@pytest.mark.parametrize(
    "path",
    [
        "",
        "a/" * 600,
        "../etc/passwd",
        "/abs/path",
        "a\\b",
        "a\x00b",
        "C:foo/bar",
        "c:foo/bar",
        "org/exa mple/lib.jar",
        "org/ex$ample/lib.jar",
        "org//example/lib.jar",
    ],
)
def test_validate_maven_path_rejects(path):
    assert validate_maven_path(path) is False


def test_validate_maven_path_accepts_normal_artifact():
    assert validate_maven_path("org/example/lib/1.0/lib-1.0.jar") is True


@pytest.mark.parametrize(
    "name",
    [
        "",
        "a" * 252 + ".tgz",
        "a/b.tgz",
        "a\\b.tgz",
        "a..b.tgz",
        "a\x00b.tgz",
        "pkg-1.0.zip",
        "pkg-1.0",
        "pkg$.tgz",
        "pkg 1.tgz",
    ],
)
def test_validate_tarball_name_rejects(name):
    assert validate_tarball_name(name) is False


@pytest.mark.parametrize(
    "name", ["pkg-1.0.0.tgz", "pkg.tar.gz", "pkg.tar", "pkg.tar.bz2", "pkg.tar.xz"]
)
def test_validate_tarball_name_accepts(name):
    assert validate_tarball_name(name) is True


def test_safe_join_path_skips_empty_parts(tmp_path):
    assert safe_join_path(tmp_path, "", "a", "", "b") == tmp_path.resolve() / "a" / "b"


@pytest.mark.parametrize("part", ["..", "a/../b", "/abs", "a\\b", "a\x00b"])
def test_safe_join_path_rejects_bad_component(tmp_path, part):
    with pytest.raises(ValidationError, match="Invalid path component"):
        safe_join_path(tmp_path, part)


@pytest.mark.parametrize("exc", [OSError("io"), RuntimeError("loop")])
def test_safe_join_path_resolution_failure(tmp_path, exc):
    with patch.object(Path, "resolve", side_effect=exc):
        with pytest.raises(ValidationError, match="resolution failed"):
            safe_join_path(tmp_path, "a")


def test_safe_join_path_rejects_symlink_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    base = tmp_path / "base"
    base.mkdir()
    _make_dir_symlink(base / "link", outside)
    with pytest.raises(ValidationError, match="traversal"):
        safe_join_path(base, "link", "x")
