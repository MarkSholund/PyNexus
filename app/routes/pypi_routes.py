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

import asyncio
import inspect
import json
from urllib.parse import quote, urlparse

import httpx2
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException, Request, Response

from app import config, utils
from app.validators import (
    ValidationError,
    safe_join_path,
    validate_pypi_artifact_path,
    validate_pypi_package_name,
    validate_version_string,
)

router = APIRouter(prefix="/pypi", tags=["PyPI"])

PYPI_UPSTREAM = "https://pypi.org"
PYPI_CACHE = config.CACHE_DIR / "pypi"


def rewrite_url(orig: str, base_url: str) -> str:
    """Map an upstream PyPI/pythonhosted URL to the equivalent proxy URL."""
    parsed = urlparse(orig)
    new_url = orig

    if parsed.scheme in ("http", "https"):
        host = parsed.netloc.lower()
        if host.endswith("files.pythonhosted.org") and "/packages/" in parsed.path:
            suffix = parsed.path.split("/packages/", 1)[1]
            new_url = f"{base_url}/packages/{suffix}"
        elif host.endswith("pypi.org"):
            path = parsed.path.lstrip("/")
            new_url = f"{base_url}/{path}" if path else f"{base_url}/"
    else:
        # relative URL
        rel = orig.lstrip("/")
        if rel.startswith("packages/"):
            suffix = rel[len("packages/") :]
            new_url = f"{base_url}/packages/{suffix}"
        elif rel.startswith("pypi/"):
            new_url = f"{base_url}/{rel}"

    if parsed.query:
        new_url += f"?{parsed.query}"
    if parsed.fragment:
        new_url += f"#{parsed.fragment}"
    return new_url


def rewrite_index_html(html: str, base_url: str) -> str:
    """Rewrite PyPI simple index links to route through proxy."""
    soup = BeautifulSoup(html, "html.parser")

    for a in soup.find_all("a", href=True):
        a["href"] = rewrite_url(str(a["href"]), base_url)

    return str(soup)


# ----------------------------------------------------------------------
# JSON simple API (PEP 691)
# ----------------------------------------------------------------------
SIMPLE_JSON_TYPE = "application/vnd.pypi.simple.v1+json"
_SIMPLE_JSON_TYPES = (SIMPLE_JSON_TYPE, "application/vnd.pypi.simple.latest+json")


def wants_simple_json(accept) -> bool:
    """
    Content negotiation for the simple API: True when the client prefers the
    PEP 691 JSON format over HTML. Ties (e.g. ``*/*``) and a missing Accept
    header get HTML, which is what plain browsers and legacy tools expect.
    """
    if not isinstance(accept, str) or not accept:
        return False
    best_json = 0.0
    best_other = 0.0
    for part in accept.split(","):
        pieces = [p.strip() for p in part.split(";")]
        media = pieces[0].lower()
        q = 1.0
        for param in pieces[1:]:
            if param.lower().startswith("q="):
                try:
                    q = float(param[2:])
                except ValueError:
                    q = 0.0
        if media in _SIMPLE_JSON_TYPES:
            best_json = max(best_json, q)
        else:
            best_other = max(best_other, q)
    return best_json > 0 and best_json > best_other


def _accept_header(request) -> str | None:
    """Read the Accept header (tolerating test doubles whose .get() is a coroutine)."""
    value = request.headers.get("accept")
    if inspect.iscoroutine(value):
        value.close()
        return None
    return value if isinstance(value, str) else None


def rewrite_simple_json(data: dict, base_url: str) -> dict:
    """Rewrite every file URL in a PEP 691 project page to route through the proxy."""
    for f in data.get("files", []):
        if isinstance(f, dict) and isinstance(f.get("url"), str):
            f["url"] = rewrite_url(f["url"], base_url)
    return data


async def _serve_simple_json(
    request: Request, local_path, upstream_url: str, rewrite_files: bool
):
    """
    Serve a PEP 691 JSON simple page from the cache, refreshing it when stale.
    Mirrors the HTML handlers: a failed refresh falls back to the stale copy.
    """
    if utils.is_cache_stale(local_path, config.PYPI_METADATA_TTL_HOURS):
        try:
            async with httpx2.AsyncClient(
                follow_redirects=True, timeout=60.0
            ) as client:
                r = await client.get(
                    upstream_url, headers={"Accept": SIMPLE_JSON_TYPE}
                )
            if r.status_code == 200:
                try:
                    data = json.loads(r.text)
                except ValueError:
                    data = None
                if not isinstance(data, dict):
                    # upstream ignored our Accept header or sent junk
                    if not local_path.exists():
                        raise HTTPException(
                            status_code=502, detail="Invalid upstream JSON response"
                        )
                else:
                    if rewrite_files:
                        data = rewrite_simple_json(data, "/pypi")
                    local_path.parent.mkdir(parents=True, exist_ok=True)
                    payload = json.dumps(data, separators=(",", ":")).encode()
                    await asyncio.to_thread(utils._atomic_write, local_path, payload)
            elif not local_path.exists():
                raise HTTPException(status_code=r.status_code)
        except httpx2.RequestError:
            if not local_path.exists():
                raise HTTPException(status_code=503, detail="Upstream unavailable")

    return await _with_vary_accept(
        await utils.conditional_file_response(request, local_path, SIMPLE_JSON_TYPE)
    )


async def _with_vary_accept(response):
    """Tell caches the body depends on the Accept header (HTML vs JSON)."""
    if isinstance(response, Response):
        response.headers["Vary"] = "Accept"
    return response


@router.get("/simple/")
async def pypi_root_index(request: Request):
    if wants_simple_json(_accept_header(request)):
        json_path = utils.safe_cache_path(PYPI_CACHE, "simple", "index.json")
        return await _serve_simple_json(
            request, json_path, f"{PYPI_UPSTREAM}/simple/", rewrite_files=False
        )

    local_path = utils.safe_cache_path(PYPI_CACHE, "simple", "index.html")

    # Check if cache is stale (older than 24 hours)
    if utils.is_cache_stale(local_path, max_age_hours=config.PYPI_METADATA_TTL_HOURS):
        async with httpx2.AsyncClient(follow_redirects=True, timeout=60.0) as client:
            r = await client.get(f"{PYPI_UPSTREAM}/simple/")
            if r.status_code != 200:
                raise HTTPException(status_code=r.status_code)

            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_text(r.text, encoding="utf-8")

    return await _with_vary_accept(
        await utils.conditional_file_response(request, local_path, "text/html")
    )


@router.get("/simple/{package}/")
async def pypi_package_index(package: str, request: Request):
    """
    Serve the simple API page for a given package.
    Automatically refreshes if older than configured TTL (default: 24 hours).
    Set PYPI_METADATA_TTL_HOURS=0 to disable automatic updates.
    """
    # SECURITY: Validate package name
    if not validate_pypi_package_name(package):
        raise HTTPException(
            status_code=400, detail=f"Invalid PyPI package name: {package}"
        )

    try:
        local_path = safe_join_path(PYPI_CACHE, "simple", package, "index.html")
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if wants_simple_json(_accept_header(request)):
        try:
            json_path = safe_join_path(PYPI_CACHE, "simple", package, "index.json")
        except ValidationError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return await _serve_simple_json(
            request,
            json_path,
            f"{PYPI_UPSTREAM}/simple/{package}/",
            rewrite_files=True,
        )

    # Check if cache needs refresh based on TTL configuration
    if utils.is_cache_stale(local_path, config.PYPI_METADATA_TTL_HOURS):
        url = f"{PYPI_UPSTREAM}/simple/{package}/"
        try:
            async with httpx2.AsyncClient(
                follow_redirects=True, timeout=60.0
            ) as client:
                r = await client.get(url)
                if r.status_code == 200:
                    rewritten = rewrite_index_html(r.text, base_url="/pypi")
                    local_path.parent.mkdir(parents=True, exist_ok=True)
                    local_path.write_text(rewritten, encoding="utf-8")
                elif not local_path.exists():
                    raise HTTPException(status_code=r.status_code)
        except httpx2.RequestError:
            # Network error - serve stale cache if available
            if not local_path.exists():
                raise HTTPException(status_code=503, detail="Upstream unavailable")

    return await _with_vary_accept(
        await utils.conditional_file_response(request, local_path, "text/html")
    )


@router.get("/packages/{path:path}")
async def pypi_artifact(path: str, request: Request):
    # SECURITY: Validate artifact path charset (also rejects CR/LF and other
    # control characters that could otherwise be smuggled into the upstream
    # request URL below).
    if not validate_pypi_artifact_path(path):
        raise HTTPException(
            status_code=400, detail=f"Invalid PyPI artifact path: {path}"
        )

    try:
        local_path = utils.safe_cache_path(PYPI_CACHE, "packages", path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not local_path.exists():
        upstream_path = path
        upstream_path = upstream_path.removeprefix("packages/")
        upstream_url = (
            f"https://files.pythonhosted.org/packages/{quote(upstream_path, safe='/')}"
        )
        await utils.fetch_and_cache(upstream_url, local_path)

    try:
        attachment = local_path.suffix in [".whl", ".zip", ".gz", ".tar"]
        return await utils.conditional_file_response(
            request, local_path, "application/octet-stream", attachment=attachment
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Artifact not found")


@router.get("/{package}/json")
async def pypi_package_json(package: str, request: Request):
    # SECURITY: Validate package name
    if not validate_pypi_package_name(package):
        raise HTTPException(
            status_code=400, detail=f"Invalid PyPI package name: {package}"
        )

    try:
        local_path = safe_join_path(PYPI_CACHE, package, "index.json")
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Package-level JSON lists the latest release, so it must be refreshed
    # once stale (a missing file counts as stale).
    if utils.is_cache_stale(local_path, config.PYPI_METADATA_TTL_HOURS):
        await utils.refresh_cached(f"{PYPI_UPSTREAM}/pypi/{package}/json", local_path)

    return await utils.conditional_file_response(
        request, local_path, "application/json"
    )


@router.get("/{package}/{version}/json")
async def pypi_package_version_json(package: str, version: str, request: Request):
    # SECURITY: Validate package name and version
    if not validate_pypi_package_name(package):
        raise HTTPException(
            status_code=400, detail=f"Invalid PyPI package name: {package}"
        )

    if not validate_version_string(version):
        raise HTTPException(
            status_code=400, detail=f"Invalid version string: {version}"
        )

    try:
        local_path = safe_join_path(PYPI_CACHE, package, version, "index.json")
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not local_path.exists():
        await utils.fetch_and_cache(
            f"{PYPI_UPSTREAM}/pypi/{package}/{version}/json", local_path
        )

    return await utils.conditional_file_response(
        request, local_path, "application/json"
    )
