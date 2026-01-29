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


from fastapi import APIRouter, HTTPException, Request
from urllib.parse import quote
from http import HTTPMethod

import app.config as config
import app.utils as utils
from app.validators import (
    validate_npm_package_name,
    validate_tarball_name,
    safe_join_path,
    ValidationError
)

NPM_UPSTREAM = config.NPM_REGISTRY
NPM_CACHE = config.CACHE_DIR / "npm"

router = APIRouter(prefix="/npm", tags=["npm"])


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
            status_code=400,
            detail=f"Invalid NPM package name: {package}"
        )

    # SECURITY: Validate tarball filename
    if not validate_tarball_name(tarball):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid tarball filename: {tarball}"
        )

    try:
        # Use safe path joining
        local_path = safe_join_path(NPM_CACHE, package, "-", tarball)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not local_path.exists():
        upstream_url = f"{NPM_UPSTREAM}/{encode_scoped_package(package)}/-/{quote(tarball)}"
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
            status_code=400,
            detail=f"Invalid NPM package name: {package}"
        )

    try:
        # Use safe path joining with validation
        local_path = safe_join_path(NPM_CACHE, package, "index.json")
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Use is_cache_stale from utils.py for 24h staleness check
    if utils.is_cache_stale(local_path, max_age_hours=config.NPM_METADATA_TTL_HOURS):
        upstream_url = f"{NPM_UPSTREAM}/{encode_scoped_package(package)}"
        await utils.fetch_and_cache(upstream_url, local_path)

    try:
        return await utils.conditional_file_response(
            request, local_path, "application/json"
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Package not found")


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

    # Use is_cache_stale from utils.py for 24h staleness check
    if utils.is_cache_stale(local_path, max_age_hours=config.NPM_METADATA_TTL_HOURS):
        upstream_url = f"{NPM_UPSTREAM}/-/npm/v1/security/advisories/bulk"
        data = await utils.fetch_and_cache(
            upstream_url, local_path, method=HTTPMethod.POST, data=body_bytes
        )
        return data

    try:
        return await utils.conditional_file_response(
            request, local_path, "application/json"
        )
    except FileNotFoundError:
        # Cache miss or staleness check: fetch from upstream
        upstream_url = f"{NPM_UPSTREAM}/-/npm/v1/security/advisories/bulk"
        data = await utils.fetch_and_cache(
            upstream_url, local_path, method=HTTPMethod.POST, data=body_bytes
        )
        return data
