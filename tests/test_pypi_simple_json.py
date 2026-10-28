# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the PEP 691 JSON flavour of the PyPI simple API."""

import json
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx2
import pytest
from fastapi import HTTPException

from app import config
from app.routes import pypi_routes

JSON_T = "application/vnd.pypi.simple.v1+json"
TWO_DAYS = 2 * 24 * 3600

PIP_ACCEPT = (
    "application/vnd.pypi.simple.v1+json, "
    "application/vnd.pypi.simple.v1+html; q=0.1, text/html; q=0.01"
)


# ----------------------------------------------------------------------
# content negotiation
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "accept,expected",
    [
        (None, False),
        ("", False),
        ("*/*", False),
        ("text/html", False),
        ("application/vnd.pypi.simple.v1+html", False),
        (JSON_T, True),
        ("application/vnd.pypi.simple.latest+json", True),
        (PIP_ACCEPT, True),
        (f"{JSON_T}, */*", False),  # tie -> HTML
        (f"{JSON_T}; q=0.5, text/html", False),
        (f"text/html; q=0.2, {JSON_T}; q=0.9", True),
        (f"{JSON_T}; q=0", False),
        (f"{JSON_T}; q=oops, text/html", False),
        ("APPLICATION/VND.PYPI.SIMPLE.V1+JSON", True),
    ],
)
def test_wants_simple_json(accept, expected):
    assert pypi_routes.wants_simple_json(accept) is expected


def test_wants_simple_json_ignores_non_strings():
    assert pypi_routes.wants_simple_json(AsyncMock()) is False


# ----------------------------------------------------------------------
# URL rewriting
# ----------------------------------------------------------------------
def test_rewrite_simple_json_rewrites_file_urls_only():
    data = {
        "meta": {"api-version": "1.0"},
        "name": "pkg",
        "files": [
            {
                "filename": "pkg-1.0-py3-none-any.whl",
                "url": "https://files.pythonhosted.org/packages/ab/cd/pkg-1.0-py3-none-any.whl",
                "hashes": {"sha256": "abc"},
                "core-metadata": {"sha256": "def"},
            },
            {"filename": "x", "url": "/packages/ef/gh/x.tar.gz"},
        ],
    }
    out = pypi_routes.rewrite_simple_json(data, "/pypi")
    assert out["files"][0]["url"] == "/pypi/packages/ab/cd/pkg-1.0-py3-none-any.whl"
    assert out["files"][1]["url"] == "/pypi/packages/ef/gh/x.tar.gz"
    assert out["files"][0]["hashes"] == {"sha256": "abc"}
    assert out["meta"] == {"api-version": "1.0"}


def test_rewrite_url_matches_html_behaviour():
    html = '<a href="https://files.pythonhosted.org/packages/a/b.whl?x=1#sha256=abc">b</a>'
    out = pypi_routes.rewrite_index_html(html, base_url="/pypi")
    assert 'href="/pypi/packages/a/b.whl?x=1#sha256=abc"' in out


# ----------------------------------------------------------------------
# route behaviour
# ----------------------------------------------------------------------
@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(pypi_routes, "PYPI_CACHE", tmp_path / "pypi")
    return tmp_path


def _request(accept=None):
    req = MagicMock()
    req.headers = {} if accept is None else {"accept": accept}
    return req


def _upstream(text="", status=200, error=None):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    client = MagicMock()
    client.get = AsyncMock(side_effect=error) if error else AsyncMock(return_value=resp)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return patch("app.routes.pypi_routes.httpx2.AsyncClient", return_value=cm), client


PROJECT = {
    "meta": {"api-version": "1.0"},
    "name": "requests",
    "files": [
        {
            "filename": "requests-2.0.whl",
            "url": "https://files.pythonhosted.org/packages/ab/cd/requests-2.0.whl",
            "hashes": {"sha256": "abc"},
        }
    ],
}


@pytest.mark.asyncio
async def test_package_json_fetched_rewritten_and_cached(cache):
    patcher, client = _upstream(json.dumps(PROJECT))
    with patcher:
        resp = await pypi_routes.pypi_package_index("requests", _request(PIP_ACCEPT))
        again = await pypi_routes.pypi_package_index("requests", _request(PIP_ACCEPT))

    body = json.loads(resp.body)
    assert body["files"][0]["url"] == "/pypi/packages/ab/cd/requests-2.0.whl"
    assert body["files"][0]["hashes"] == {"sha256": "abc"}
    assert resp.headers["content-type"].startswith(JSON_T)
    assert resp.headers["vary"] == "Accept"
    assert again.body == resp.body
    client.get.assert_awaited_once()  # second call served from cache
    assert client.get.await_args.kwargs["headers"] == {"Accept": JSON_T}
    assert (cache / "pypi" / "simple" / "requests" / "index.json").exists()


@pytest.mark.asyncio
async def test_html_and_json_cached_independently(cache):
    html = '<a href="https://files.pythonhosted.org/packages/ab/cd/r.whl">r</a>'
    patcher, _ = _upstream(html)
    with patcher:
        h = await pypi_routes.pypi_package_index("requests", _request("text/html"))
    patcher, _ = _upstream(json.dumps(PROJECT))
    with patcher:
        j = await pypi_routes.pypi_package_index("requests", _request(PIP_ACCEPT))

    assert h.headers["content-type"].startswith("text/html")
    assert h.headers["vary"] == "Accept"
    assert b"<a " in h.body
    assert j.headers["content-type"].startswith(JSON_T)
    assert json.loads(j.body)["name"] == "requests"


@pytest.mark.asyncio
async def test_package_json_stale_refreshed(cache):
    patcher, _ = _upstream(json.dumps(PROJECT))
    with patcher:
        await pypi_routes.pypi_package_index("requests", _request(PIP_ACCEPT))
    f = cache / "pypi" / "simple" / "requests" / "index.json"
    old = time.time() - TWO_DAYS
    os.utime(f, (old, old))

    newer = dict(PROJECT, files=[])
    patcher, client = _upstream(json.dumps(newer))
    with patcher:
        resp = await pypi_routes.pypi_package_index("requests", _request(PIP_ACCEPT))
    client.get.assert_awaited_once()
    assert json.loads(resp.body)["files"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 500])
async def test_package_json_upstream_error_no_cache_propagates(cache, status):
    patcher, _ = _upstream(status=status)
    with patcher, pytest.raises(HTTPException) as exc:
        await pypi_routes.pypi_package_index("requests", _request(PIP_ACCEPT))
    assert exc.value.status_code == status


@pytest.mark.asyncio
async def test_package_json_network_error_no_cache_503(cache):
    patcher, _ = _upstream(error=httpx2.ConnectError("down"))
    with patcher, pytest.raises(HTTPException) as exc:
        await pypi_routes.pypi_package_index("requests", _request(PIP_ACCEPT))
    assert exc.value.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["500", "network"])
async def test_package_json_serves_stale_when_refresh_fails(cache, failure):
    patcher, _ = _upstream(json.dumps(PROJECT))
    with patcher:
        await pypi_routes.pypi_package_index("requests", _request(PIP_ACCEPT))
    f = cache / "pypi" / "simple" / "requests" / "index.json"
    old = time.time() - TWO_DAYS
    os.utime(f, (old, old))

    if failure == "500":
        patcher, _ = _upstream(status=500)
    else:
        patcher, _ = _upstream(error=httpx2.ConnectError("down"))
    with patcher:
        resp = await pypi_routes.pypi_package_index("requests", _request(PIP_ACCEPT))
    assert json.loads(resp.body)["name"] == "requests"


@pytest.mark.asyncio
async def test_package_json_rejects_non_json_upstream_body(cache):
    # upstream ignoring our Accept header and returning HTML must not be cached as JSON
    patcher, _ = _upstream("<html>not json</html>")
    with patcher, pytest.raises(HTTPException) as exc:
        await pypi_routes.pypi_package_index("requests", _request(PIP_ACCEPT))
    assert exc.value.status_code == 502
    assert not (cache / "pypi" / "simple" / "requests" / "index.json").exists()


@pytest.mark.asyncio
async def test_package_json_invalid_name_400(cache):
    with pytest.raises(HTTPException) as exc:
        await pypi_routes.pypi_package_index("bad name!", _request(PIP_ACCEPT))
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_root_json_served_without_rewriting(cache):
    root = {"meta": {"api-version": "1.0"}, "projects": [{"name": "requests"}]}
    patcher, client = _upstream(json.dumps(root))
    with patcher:
        resp = await pypi_routes.pypi_root_index(_request(PIP_ACCEPT))
    assert json.loads(resp.body)["projects"] == [{"name": "requests"}]
    assert resp.headers["content-type"].startswith(JSON_T)
    assert client.get.await_args.args[0] == "https://pypi.org/simple/"
    assert (cache / "pypi" / "simple" / "index.json").exists()
