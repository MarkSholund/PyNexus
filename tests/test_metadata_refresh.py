# SPDX-License-Identifier: GPL-3.0-or-later
"""
Regression tests: stale cached metadata must actually be re-fetched.

These deliberately do NOT mock utils.fetch_and_cache, because the original bug
lived in the interaction between the route's staleness check and
fetch_and_cache's "file exists -> return cached copy" shortcut.
"""

import json
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx2
import pytest
from fastapi import HTTPException

from app import config, utils
from app.routes import maven_routes, npm_routes, pypi_routes

pytestmark = pytest.mark.asyncio

TWO_DAYS = 2 * 24 * 3600


def _age(path, seconds):
    old = time.time() - seconds
    os.utime(path, (old, old))


def _fake_upstream(body: bytes, status: int = 200):
    """Patch httpx2.AsyncClient used by utils.fetch_and_cache."""
    resp = MagicMock()
    resp.content = body
    resp.text = body.decode()
    resp.status_code = status
    if status >= 400:
        resp.raise_for_status.side_effect = httpx2.HTTPStatusError(
            "boom", request=MagicMock(), response=resp
        )
    client = MagicMock()
    client.get = AsyncMock(return_value=resp)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return patch("app.utils.httpx2.AsyncClient", return_value=cm), client


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(npm_routes, "NPM_CACHE", tmp_path / "npm")
    monkeypatch.setattr(maven_routes, "MAVEN_CACHE", tmp_path / "maven")
    monkeypatch.setattr(pypi_routes, "PYPI_CACHE", tmp_path / "pypi")
    return tmp_path


def _request():
    req = MagicMock()
    req.headers = {}
    return req


async def test_npm_stale_metadata_is_refreshed(cache):
    index = cache / "npm" / "vue" / "index.json"
    index.parent.mkdir(parents=True)
    index.write_text(json.dumps({"dist-tags": {"latest": "3.5.39"}}))
    _age(index, TWO_DAYS * 100)

    new = json.dumps({"dist-tags": {"latest": "3.5.43"}}).encode()
    patcher, client = _fake_upstream(new)
    with patcher:
        resp = await npm_routes.npm_package_metadata("vue", _request())

    client.get.assert_awaited_once()
    assert json.loads(resp.body)["dist-tags"]["latest"] == "3.5.43"
    assert json.loads(index.read_text())["dist-tags"]["latest"] == "3.5.43"
    assert not utils.is_cache_stale(index, config.NPM_METADATA_TTL_HOURS)


async def test_npm_fresh_metadata_not_refetched(cache):
    index = cache / "npm" / "vue" / "index.json"
    index.parent.mkdir(parents=True)
    index.write_text(json.dumps({"dist-tags": {"latest": "3.5.39"}}))

    patcher, client = _fake_upstream(b"{}")
    with patcher:
        resp = await npm_routes.npm_package_metadata("vue", _request())

    client.get.assert_not_called()
    assert json.loads(resp.body)["dist-tags"]["latest"] == "3.5.39"


async def test_npm_stale_metadata_served_when_upstream_down(cache):
    index = cache / "npm" / "vue" / "index.json"
    index.parent.mkdir(parents=True)
    index.write_text(json.dumps({"dist-tags": {"latest": "3.5.39"}}))
    _age(index, TWO_DAYS)

    patcher, _ = _fake_upstream(b"", status=503)
    with patcher:
        resp = await npm_routes.npm_package_metadata("vue", _request())

    assert json.loads(resp.body)["dist-tags"]["latest"] == "3.5.39"


async def test_npm_missing_metadata_and_upstream_down_raises(cache):
    patcher, _ = _fake_upstream(b"", status=503)
    with patcher, pytest.raises(HTTPException) as exc:
        await npm_routes.npm_package_metadata("vue", _request())
    assert exc.value.status_code == 503


async def test_maven_stale_metadata_is_refreshed(cache):
    path = "org/example/lib/maven-metadata.xml"
    f = cache / "maven" / path
    f.parent.mkdir(parents=True)
    f.write_text("<old/>")
    _age(f, TWO_DAYS * 100)

    patcher, client = _fake_upstream(b"<new/>")
    with patcher:
        await maven_routes.maven_proxy(path, _request())

    client.get.assert_awaited_once()
    assert f.read_text() == "<new/>"


async def test_pypi_package_json_stale_is_refreshed(cache):
    f = cache / "pypi" / "requests" / "index.json"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({"info": {"version": "1.0"}}))
    _age(f, TWO_DAYS * 100)

    new = json.dumps({"info": {"version": "2.0"}}).encode()
    patcher, client = _fake_upstream(new)
    with patcher:
        resp = await pypi_routes.pypi_package_json("requests", _request())

    client.get.assert_awaited_once()
    assert json.loads(resp.body)["info"]["version"] == "2.0"


async def test_pypi_package_json_fresh_not_refetched(cache):
    f = cache / "pypi" / "requests" / "index.json"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({"info": {"version": "1.0"}}))

    patcher, client = _fake_upstream(b"{}")
    with patcher:
        resp = await pypi_routes.pypi_package_json("requests", _request())

    client.get.assert_not_called()
    assert json.loads(resp.body)["info"]["version"] == "1.0"
