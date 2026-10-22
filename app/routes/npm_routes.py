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


import re
from http import HTTPMethod
from urllib.parse import quote, urlencode, urlparse

from fastapi import APIRouter, HTTPException, Request, Response

from app import config, utils
from app.validators import (
    ValidationError,
    safe_join_path,
    validate_npm_package_name,
    validate_tarball_name,
)

NPM_UPSTREAM = config.NPM_REGISTRY
NPM_CACHE = config.CACHE_DIR / "npm"

ROUTE_PREFIX = "/npm"
router = APIRouter(prefix=ROUTE_PREFIX, tags=["npm"])


def encode_scoped_package(pkg: str) -> str:
    """
    Encode scoped NPM packages correctly for upstream URLs.
    '@types/react' -> '%40types/react'
    'lodash' -> 'lodash'
    """
    if pkg.startswith("@") and "/" in pkg:
        scope, name = pkg.split("/", 1)
        return f"{quote(scope)}/{name}"
    return quote(pkg)


def rewrite_tarball_urls(body: bytes, upstream: str, base_url: str) -> bytes:
    """
    Point every ``dist.tarball`` URL in a packument at this proxy.

    Upstream metadata contains absolute tarball URLs such as
    ``https://registry.npmjs.org/vue/-/vue-3.5.0.tgz``. Clients only translate
    those to the configured registry on a best-effort basis (and different npm
    versions drop the proxy's ``/npm`` path prefix, giving 404s), so we rewrite
    them to ``{base_url}/vue/-/vue-3.5.0.tgz`` ourselves.

    Only the ``"tarball"`` fields are touched, so package readmes or other
    text that merely mention the registry are left alone.
    """
    netloc = urlparse(upstream).netloc
    if not netloc:
        return body
    pattern = re.compile(
        rb'("tarball"\s*:\s*")https?://' + re.escape(netloc.encode()) + rb"/"
    )
    replacement = base_url.rstrip("/").encode() + b"/"
    return pattern.sub(lambda m: m.group(1) + replacement, body)


def _public_base_url(request: Request) -> str:
    """External URL of this router, honouring the reverse proxy's headers."""
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    scheme = scheme.split(",")[0].strip()
    host = request.headers.get("host") or request.url.netloc
    root_path = request.scope.get("root_path", "").rstrip("/")
    return f"{scheme}://{host}{root_path}{ROUTE_PREFIX}"


# Query parameters accepted by the upstream search endpoint. Anything else is
# dropped so clients can't use the cache to store arbitrary upstream queries.
_SEARCH_MAX_TEXT = 256
_SEARCH_MAX_SIZE = 250
_SEARCH_WEIGHTS = ("quality", "popularity", "maintenance")


def _normalize_search_params(query_params) -> list[tuple[str, str]]:
    """
    Validate and canonicalize `npm search` query parameters.
    Returns a sorted list of (name, value) pairs; raises HTTPException(400).
    """
    params: dict[str, str] = {}

    text = query_params.get("text")
    if text is None:
        raise HTTPException(status_code=400, detail="Missing required parameter: text")
    if len(text) > _SEARCH_MAX_TEXT or "\0" in text:
        raise HTTPException(status_code=400, detail="Invalid search text")
    params["text"] = text

    for name, upper in (("size", _SEARCH_MAX_SIZE), ("from", None)):
        raw = query_params.get(name)
        if raw is None:
            continue
        try:
            value = int(raw)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid {name}: {raw}")
        if value < 0 or (upper is not None and value > upper):
            raise HTTPException(status_code=400, detail=f"Invalid {name}: {raw}")
        params[name] = str(value)

    for name in _SEARCH_WEIGHTS:
        raw = query_params.get(name)
        if raw is None:
            continue
        try:
            value = float(raw)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid {name}: {raw}")
        if not 0.0 <= value <= 1.0:
            raise HTTPException(status_code=400, detail=f"Invalid {name}: {raw}")
        params[name] = raw

    return sorted(params.items())


@router.get("/-/ping")
async def npm_ping():
    """
    Registry health check used by `npm ping` and `npm doctor`.
    Answered locally (like the real registry, which returns `{}`), so it
    reflects whether *this proxy* is reachable.
    """
    return {}


@router.get("/-/v1/search")
async def npm_search(request: Request):
    """
    Proxy `npm search` queries.
    Example: GET /npm/-/v1/search?text=vue&size=20&from=0
    Responses are cached by a hash of the normalized query and refreshed after
    NPM_SEARCH_TTL_HOURS.
    """
    import hashlib

    params = _normalize_search_params(request.query_params)
    query = urlencode(params)
    key = hashlib.sha256(query.encode()).hexdigest()[:16]

    try:
        local_path = safe_join_path(NPM_CACHE, "search", f"{key}.json")
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if utils.is_cache_stale(local_path, max_age_hours=config.NPM_SEARCH_TTL_HOURS):
        await utils.refresh_cached(f"{NPM_UPSTREAM}/-/v1/search?{query}", local_path)

    try:
        return await utils.conditional_file_response(
            request, local_path, "application/json"
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Search results not found")


@router.post("/-/npm/v1/security/audits/quick")
async def npm_security_audit_quick(request: Request):
    """
    Proxy npm audit quick security checks.
    This endpoint is called by 'npm audit' to perform rapid vulnerability assessment.
    Cache responses based on a hash of the POST body (dependency tree).
    Invalidate cache if older than 24 hours.
    """
    import hashlib

    body_bytes = await request.body()

    # SECURITY: Use cryptographic hash instead of Python's hash()
    body_hash = hashlib.sha256(body_bytes).hexdigest()[:16]

    try:
        local_path = safe_join_path(
            NPM_CACHE, "security", "audits", f"{body_hash}.json"
        )
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Extract and prepare headers (handle AsyncMock in tests)
    import inspect

    async def _extract_headers(req):
        raw = getattr(req, "headers", {})
        if isinstance(raw, dict):
            return dict(raw)
        # Try calling items(); some test doubles return a coroutine
        try:
            items = raw.items()
        except Exception:
            try:
                return dict(raw)
            except Exception:
                return {}
        if inspect.isawaitable(items):
            try:
                items = await items
            except Exception:
                return {}
        try:
            return dict(items)
        except Exception:
            return {}

    headers = await _extract_headers(request)
    # Hop-by-hop headers (Host, Content-Length, etc.) are stripped inside
    # utils.fetch_and_cache before forwarding to the upstream registry.
    if "content-type" not in headers:
        headers["content-type"] = "application/json"

    # Use is_cache_stale from utils.py for 24h staleness check
    if utils.is_cache_stale(local_path, max_age_hours=config.NPM_METADATA_TTL_HOURS):
        upstream_url = f"{NPM_UPSTREAM}/-/npm/v1/security/audits/quick"
        data = await utils.fetch_and_cache(
            upstream_url,
            local_path,
            method=HTTPMethod.POST,
            data=body_bytes,
            headers=headers,
            return_json=True,  # Return JSON directly
            force_refresh=True,  # Force fetch since cache is stale
        )
        return data

    try:
        return await utils.conditional_file_response(
            request, local_path, "application/json"
        )
    except FileNotFoundError:
        # Cache miss: fetch from upstream
        upstream_url = f"{NPM_UPSTREAM}/-/npm/v1/security/audits/quick"
        data = await utils.fetch_and_cache(
            upstream_url,
            local_path,
            method=HTTPMethod.POST,
            data=body_bytes,
            headers=headers,
            return_json=True,
        )
        return data


@router.post("/-/npm/v1/security/advisories/bulk")
async def npm_security_bulk(request: Request):
    """
    Proxy npm audit bulk requests.
    Save response to cache based on a hash of the POST body.
    Invalidate cache if older than 24 hours (for security advisories).
    """
    import hashlib

    body_bytes = await request.body()

    # SECURITY: Use cryptographic hash instead of Python's hash()
    body_hash = hashlib.sha256(body_bytes).hexdigest()[:16]

    try:
        local_path = safe_join_path(NPM_CACHE, "security", f"{body_hash}.json")
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Extract and prepare headers (handle AsyncMock in tests)
    import inspect

    async def _extract_headers(req):
        raw = getattr(req, "headers", {})
        if isinstance(raw, dict):
            return dict(raw)
        try:
            items = raw.items()
        except Exception:
            try:
                return dict(raw)
            except Exception:
                return {}
        if inspect.isawaitable(items):
            try:
                items = await items
            except Exception:
                return {}
        try:
            return dict(items)
        except Exception:
            return {}

    headers = await _extract_headers(request)
    # Hop-by-hop headers (Host, Content-Length, etc.) are stripped inside
    # utils.fetch_and_cache before forwarding to the upstream registry.
    if "content-type" not in headers:
        headers["content-type"] = "application/json"

    # Use is_cache_stale from utils.py for 24h staleness check
    if utils.is_cache_stale(local_path, max_age_hours=config.NPM_METADATA_TTL_HOURS):
        upstream_url = f"{NPM_UPSTREAM}/-/npm/v1/security/advisories/bulk"
        data = await utils.fetch_and_cache(
            upstream_url,
            local_path,
            method=HTTPMethod.POST,
            data=body_bytes,
            headers=headers,
            return_json=True,
            force_refresh=True,
        )
        return data

    try:
        return await utils.conditional_file_response(
            request, local_path, "application/json"
        )
    except FileNotFoundError:
        # Cache miss: fetch from upstream
        upstream_url = f"{NPM_UPSTREAM}/-/npm/v1/security/advisories/bulk"
        data = await utils.fetch_and_cache(
            upstream_url,
            local_path,
            method=HTTPMethod.POST,
            data=body_bytes,
            headers=headers,
            return_json=True,
        )
        return data


@router.get("/{package:path}/-/{tarball}")
async def npm_package_tarball(package: str, tarball: str, request: Request):
    """
    Serve tarball files on-demand.
    Example: GET /npm/lodash/-/lodash-4.17.21.tgz
    GET /npm/@types/react/-/react-18.2.21.tgz
    """
    # SECURITY: Validate package name
    if not validate_npm_package_name(package):
        raise HTTPException(
            status_code=400, detail=f"Invalid NPM package name: {package}"
        )

    # SECURITY: Validate tarball filename
    if not validate_tarball_name(tarball):
        raise HTTPException(
            status_code=400, detail=f"Invalid tarball filename: {tarball}"
        )

    try:
        # Use safe path joining
        local_path = safe_join_path(NPM_CACHE, package, "-", tarball)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not local_path.exists():
        upstream_url = (
            f"{NPM_UPSTREAM}/{encode_scoped_package(package)}/-/{quote(tarball)}"
        )
        await utils.fetch_and_cache(upstream_url, local_path)

    try:
        return await utils.conditional_file_response(
            request, local_path, "application/octet-stream", attachment=True
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Tarball not found")


@router.get("/{package:path}")
async def npm_package_metadata(package: str, request: Request):
    """
    Serve package metadata (package.json-style).
    Example: GET /npm/lodash or /npm/@types/react
    Only cache metadata; do NOT prefetch tgz files.
    """
    # SECURITY: Validate package name format
    if not validate_npm_package_name(package):
        raise HTTPException(
            status_code=400, detail=f"Invalid NPM package name: {package}"
        )

    try:
        # Use safe path joining with validation
        local_path = safe_join_path(NPM_CACHE, package, "index.json")
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Use is_cache_stale from utils.py for 24h staleness check
    if utils.is_cache_stale(local_path, max_age_hours=config.NPM_METADATA_TTL_HOURS):
        upstream_url = f"{NPM_UPSTREAM}/{encode_scoped_package(package)}"
        # Must force a refresh: plain fetch_and_cache() returns the existing
        # file untouched, which would leave stale metadata in place forever.
        await utils.refresh_cached(upstream_url, local_path)

    try:
        response = await utils.conditional_file_response(
            request, local_path, "application/json"
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Package not found")

    if isinstance(response, Response) and response.status_code == 200:
        body = rewrite_tarball_urls(
            response.body, NPM_UPSTREAM, _public_base_url(request)
        )
        headers = {
            k: v for k, v in response.headers.items() if k.lower() != "content-length"
        }
        return Response(content=body, headers=headers, media_type="application/json")
    return response
