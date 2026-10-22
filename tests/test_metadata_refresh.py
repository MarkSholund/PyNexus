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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_npm_fresh_metadata_not_refetched(cache):
    index = cache / "npm" / "vue" / "index.json"
    index.parent.mkdir(parents=True)
    index.write_text(json.dumps({"dist-tags": {"latest": "3.5.39"}}))

    patcher, client = _fake_upstream(b"{}")
    with patcher:
        resp = await npm_routes.npm_package_metadata("vue", _request())

    client.get.assert_not_called()
    assert json.loads(resp.body)["dist-tags"]["latest"] == "3.5.39"


@pytest.mark.asyncio
async def test_npm_stale_metadata_served_when_upstream_down(cache):
    index = cache / "npm" / "vue" / "index.json"
    index.parent.mkdir(parents=True)
    index.write_text(json.dumps({"dist-tags": {"latest": "3.5.39"}}))
    _age(index, TWO_DAYS)

    patcher, _ = _fake_upstream(b"", status=503)
    with patcher:
        resp = await npm_routes.npm_package_metadata("vue", _request())

    assert json.loads(resp.body)["dist-tags"]["latest"] == "3.5.39"


@pytest.mark.asyncio
async def test_npm_missing_metadata_and_upstream_down_raises(cache):
    patcher, _ = _fake_upstream(b"", status=503)
    with patcher, pytest.raises(HTTPException) as exc:
        await npm_routes.npm_package_metadata("vue", _request())
    assert exc.value.status_code == 503


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_pypi_package_json_fresh_not_refetched(cache):
    f = cache / "pypi" / "requests" / "index.json"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({"info": {"version": "1.0"}}))

    patcher, client = _fake_upstream(b"{}")
    with patcher:
        resp = await pypi_routes.pypi_package_json("requests", _request())

    client.get.assert_not_called()
    assert json.loads(resp.body)["info"]["version"] == "1.0"


# ----------------------------------------------------------------------
# tarball URL rewriting
# ----------------------------------------------------------------------
UP = "https://registry.npmjs.org"
BASE = "https://nexus.example.gov/npm"


def test_rewrite_tarball_urls_unscoped_and_scoped():
    body = json.dumps(
        {
            "versions": {
                "1.0.0": {"dist": {"tarball": f"{UP}/vue/-/vue-1.0.0.tgz"}},
                "2.0.0": {"dist": {"tarball": f"{UP}/@vue/shared/-/shared-2.0.0.tgz"}},
            }
        }
    ).encode()
    out = json.loads(npm_routes.rewrite_tarball_urls(body, UP, BASE))
    assert out["versions"]["1.0.0"]["dist"]["tarball"] == f"{BASE}/vue/-/vue-1.0.0.tgz"
    assert (
        out["versions"]["2.0.0"]["dist"]["tarball"]
        == f"{BASE}/@vue/shared/-/shared-2.0.0.tgz"
    )


def test_rewrite_tarball_urls_handles_http_scheme_and_pretty_json():
    body = b'{"dist": {"tarball" : "http://registry.npmjs.org/a/-/a-1.0.0.tgz"}}'
    out = npm_routes.rewrite_tarball_urls(body, UP, BASE + "/")
    assert json.loads(out)["dist"]["tarball"] == f"{BASE}/a/-/a-1.0.0.tgz"


def test_rewrite_tarball_urls_leaves_other_text_alone():
    body = json.dumps(
        {"readme": f"see {UP}/vue for details", "homepage": f"{UP}/x"}
    ).encode()
    assert npm_routes.rewrite_tarball_urls(body, UP, BASE) == body


@pytest.mark.asyncio
async def test_npm_metadata_response_has_proxy_tarball_urls(cache):
    index = cache / "npm" / "vue" / "index.json"
    index.parent.mkdir(parents=True)
    index.write_text(
        json.dumps({"versions": {"1.0.0": {"dist": {"tarball": f"{UP}/vue/-/vue-1.0.0.tgz"}}}})
    )
    req = MagicMock()
    req.headers = {"host": "nexus.example.gov", "x-forwarded-proto": "https"}
    req.url.scheme = "http"
    req.scope = {}
    resp = await npm_routes.npm_package_metadata("vue", req)

    tarball = json.loads(resp.body)["versions"]["1.0.0"]["dist"]["tarball"]
    assert tarball == "https://nexus.example.gov/npm/vue/-/vue-1.0.0.tgz"
    assert int(resp.headers["content-length"]) == len(resp.body)
    # cached file on disk is left untouched (rewriting is per-request)
    assert UP in index.read_text()


# ----------------------------------------------------------------------
# /-/ping and /-/v1/search
# ----------------------------------------------------------------------
from starlette.datastructures import QueryParams  # noqa: E402


def _search_request(qs):
    req = _request()
    req.query_params = QueryParams(qs)
    return req


@pytest.mark.asyncio
async def test_npm_ping_returns_empty_object():
    assert await npm_routes.npm_ping() == {}


def test_ping_and_search_registered_before_package_catch_all():
    paths = [r.path for r in npm_routes.router.routes]
    assert paths.index("/npm/-/ping") < paths.index("/npm/{package:path}")
    assert paths.index("/npm/-/v1/search") < paths.index("/npm/{package:path}")


@pytest.mark.asyncio
async def test_npm_search_fetches_caches_and_forwards_only_known_params(cache):
    body = json.dumps({"objects": [{"package": {"name": "vue"}}], "total": 1}).encode()
    patcher, client = _fake_upstream(body)
    qs = "text=vue&size=5&from=0&quality=0.5&evil=1&popularity=0.9"
    with patcher:
        resp = await npm_routes.npm_search(_search_request(qs))
        again = await npm_routes.npm_search(_search_request(qs))

    assert json.loads(resp.body)["objects"][0]["package"]["name"] == "vue"
    assert again.body == resp.body
    client.get.assert_awaited_once()  # second call served from cache
    url = client.get.await_args.args[0]
    assert url.startswith("https://registry.npmjs.org/-/v1/search?")
    assert "text=vue" in url and "size=5" in url and "evil" not in url


@pytest.mark.asyncio
async def test_npm_search_param_order_shares_cache_entry(cache):
    patcher, client = _fake_upstream(b'{"objects": []}')
    with patcher:
        await npm_routes.npm_search(_search_request("text=vue&size=5"))
        await npm_routes.npm_search(_search_request("size=5&text=vue"))
    client.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_npm_search_stale_is_refreshed(cache):
    patcher, client = _fake_upstream(b'{"total": 1}')
    with patcher:
        await npm_routes.npm_search(_search_request("text=vue"))
    f = next((cache / "npm" / "search").glob("*.json"))
    _age(f, TWO_DAYS)

    patcher, client = _fake_upstream(b'{"total": 2}')
    with patcher:
        resp = await npm_routes.npm_search(_search_request("text=vue"))
    client.get.assert_awaited_once()
    assert json.loads(resp.body)["total"] == 2


@pytest.mark.parametrize(
    "qs",
    [
        "",  # text missing
        "size=5",  # text missing
        "text=vue&size=abc",
        "text=vue&size=-1",
        "text=vue&size=251",
        "text=vue&from=-5",
        "text=vue&quality=1.5",
        "text=vue&popularity=x",
        "text=" + "a" * 300,
    ],
)
@pytest.mark.asyncio
async def test_npm_search_rejects_bad_params(cache, qs):
    patcher, client = _fake_upstream(b"{}")
    with patcher, pytest.raises(HTTPException) as exc:
        await npm_routes.npm_search(_search_request(qs))
    assert exc.value.status_code == 400
    client.get.assert_not_called()


@pytest.mark.asyncio
async def test_npm_search_empty_text_is_allowed(cache):
    patcher, client = _fake_upstream(b'{"objects": []}')
    with patcher:
        await npm_routes.npm_search(_search_request("text="))
    client.get.assert_awaited_once()
