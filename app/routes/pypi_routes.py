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

from urllib.parse import urlparse, quote
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException, Request
from datetime import datetime, timedelta
import httpx
import app.config as config
import app.utils as utils

from app.validators import (
    validate_pypi_package_name,
    validate_pypi_artifact_path,
    validate_version_string,
    safe_join_path,
    ValidationError
)

router = APIRouter(prefix="/pypi", tags=["PyPI"])

PYPI_UPSTREAM = "https://pypi.org"
PYPI_CACHE = config.CACHE_DIR / "pypi"


def rewrite_index_html(html: str, base_url: str) -> str:
    """Rewrite PyPI simple index links to route through proxy."""
    soup = BeautifulSoup(html, "html.parser")

    for a in soup.find_all("a", href=True):
        orig: str = str(a["href"])
        parsed = urlparse(orig)
        new_href = orig

        if parsed.scheme in ("http", "https"):
            host = parsed.netloc.lower()
            if host.endswith("files.pythonhosted.org") and "/packages/" in parsed.path:
                suffix = parsed.path.split("/packages/", 1)[1]
                new_href = f"{base_url}/packages/{suffix}"
            elif host.endswith("pypi.org"):
                path = parsed.path.lstrip("/")
                new_href = f"{base_url}/{path}" if path else f"{base_url}/"
        else:
            # relative URL
            rel = orig.lstrip("/")
            if rel.startswith("packages/"):
                suffix = rel[len("packages/"):]
                new_href = f"{base_url}/packages/{suffix}"
            elif rel.startswith("pypi/"):
                new_href = f"{base_url}/{rel}"

        if parsed.query:
            new_href += f"?{parsed.query}"
        if parsed.fragment:
            new_href += f"#{parsed.fragment}"

        a["href"] = new_href

    return str(soup)


@router.get("/simple/")
async def pypi_root_index(request: Request):
    local_path = utils.safe_cache_path(PYPI_CACHE, "simple", "index.html")

    # Check if cache is stale (older than 24 hours)
    if utils.is_cache_stale(local_path, max_age_hours=config.PYPI_METADATA_TTL_HOURS):
        async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
            r = await client.get(f"{PYPI_UPSTREAM}/simple/")
            if r.status_code != 200:
                raise HTTPException(status_code=r.status_code)

            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_text(r.text, encoding="utf-8")

    return await utils.conditional_file_response(request, local_path, "text/html")


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
            status_code=400,
            detail=f"Invalid PyPI package name: {package}"
        )

    try:
        local_path = safe_join_path(
            PYPI_CACHE, "simple", package, "index.html")
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Check if cache needs refresh based on TTL configuration
    if utils.is_cache_stale(local_path, config.PYPI_METADATA_TTL_HOURS):
        url = f"{PYPI_UPSTREAM}/simple/{package}/"
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
                r = await client.get(url)
                if r.status_code == 200:
                    rewritten = rewrite_index_html(r.text, base_url="/pypi")
                    local_path.parent.mkdir(parents=True, exist_ok=True)
                    local_path.write_text(rewritten, encoding="utf-8")
                elif not local_path.exists():
                    raise HTTPException(status_code=r.status_code)
        except httpx.RequestError:
            # Network error - serve stale cache if available
            if not local_path.exists():
                raise HTTPException(
                    status_code=503, detail="Upstream unavailable")

    return await utils.conditional_file_response(request, local_path, "text/html")


@router.get("/packages/{path:path}")
async def pypi_artifact(path: str, request: Request):
    # SECURITY: Validate artifact path charset (also rejects CR/LF and other
    # control characters that could otherwise be smuggled into the upstream
    # request URL below).
    if not validate_pypi_artifact_path(path):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid PyPI artifact path: {path}"
        )

    try:
        local_path = utils.safe_cache_path(PYPI_CACHE, "packages", path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not local_path.exists():
        upstream_path = path
        if upstream_path.startswith("packages/"):
            upstream_path = upstream_path[len("packages/"):]
        upstream_url = f"https://files.pythonhosted.org/packages/{quote(upstream_path, safe='/')}"
        await utils.fetch_and_cache(upstream_url, local_path)

    try:
        attachment = local_path.suffix in [".whl", ".zip", ".gz", ".tar"]
        return await utils.conditional_file_response(request, local_path, "application/octet-stream", attachment=attachment)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Artifact not found")


@router.get("/{package}/json")
async def pypi_package_json(package: str, request: Request):
    # SECURITY: Validate package name
    if not validate_pypi_package_name(package):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid PyPI package name: {package}"
        )

    try:
        local_path = safe_join_path(PYPI_CACHE, package, "index.json")
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not local_path.exists():
        await utils.fetch_and_cache(f"{PYPI_UPSTREAM}/pypi/{package}/json", local_path)

    return await utils.conditional_file_response(request, local_path, "application/json")


@router.get("/{package}/{version}/json")
async def pypi_package_version_json(package: str, version: str, request: Request):
    # SECURITY: Validate package name and version
    if not validate_pypi_package_name(package):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid PyPI package name: {package}"
        )

    if not validate_version_string(version):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid version string: {version}"
        )

    try:
        local_path = safe_join_path(PYPI_CACHE, package, version, "index.json")
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not local_path.exists():
        await utils.fetch_and_cache(f"{PYPI_UPSTREAM}/pypi/{package}/{version}/json", local_path)

    return await utils.conditional_file_response(request, local_path, "application/json")
