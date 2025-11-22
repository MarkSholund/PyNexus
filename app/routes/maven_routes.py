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

from urllib.parse import quote
from fastapi import APIRouter, HTTPException, Request, Response
import app.config as config
import app.utils as utils
from app.validators import validate_maven_path, safe_join_path, ValidationError

MAVEN_UPSTREAM = "https://repo1.maven.org/maven2"
MAVEN_CACHE = config.CACHE_DIR / "maven"

router = APIRouter(prefix="/maven2", tags=["Maven"])


@router.get("/{path:path}")
async def maven_proxy(path: str, request: Request):
    """
    Proxy Maven repository files, caching them under MAVEN_CACHE.
    Metadata files (maven-metadata.xml, .pom) are refreshed if older than configured max age.
    Artifact files (.jar, .war, etc.) are cached permanently once downloaded.
    """
    # SECURITY: Validate Maven path format
    if not validate_maven_path(path):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid Maven path: {path}"
        )
    
    # Build a safe local cache path
    try:
        local_path = safe_join_path(MAVEN_CACHE, path)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # Determine if this is a metadata file that should be refreshed
    is_metadata = path.endswith(('.xml', '.pom', '.sha1', '.md5'))
    
    # Check if we need to fetch/refresh the file
    should_fetch = False
    if not local_path.exists():
        should_fetch = True
    elif is_metadata and utils.is_cache_stale(local_path, max_age_hours=config.MAVEN_METADATA_TTL_HOURS):
        should_fetch = True
    
    if should_fetch:
        upstream_url = f"{MAVEN_UPSTREAM}/{quote(path, safe='/')}"
        await utils.fetch_and_cache(upstream_url, local_path)
    
    # Serve the file (conditional_file_response re-validates containment before serving)
    try:
        return await utils.conditional_file_response(
            request, local_path, "application/octet-stream"
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404)


# HEAD handler for Maven artifacts (existence/freshness check)
@router.head("/{path:path}")
async def maven_head_proxy(path: str, request: Request):
    """
    Respond to HEAD requests for Maven artifacts, returning headers (ETag, Last-Modified)
    without sending the file body. Used by Maven clients to check existence/freshness.
    """
    if not validate_maven_path(path):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid Maven path: {path}"
        )
    try:
        local_path = safe_join_path(MAVEN_CACHE, path)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    is_metadata = path.endswith((".xml", ".pom", ".sha1", ".md5"))
    should_fetch = False
    if not local_path.exists():
        should_fetch = True
    elif is_metadata and utils.is_cache_stale(local_path, max_age_hours=config.MAVEN_METADATA_TTL_HOURS):
        should_fetch = True

    if should_fetch:
        upstream_url = f"{MAVEN_UPSTREAM}/{quote(path, safe='/')}"
        await utils.fetch_and_cache(upstream_url, local_path)

    try:
        # Only return headers, no body
        response = await utils.conditional_file_response(request, local_path, "application/octet-stream")
        return Response(
            status_code=response.status_code,
            headers=dict(response.headers),
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404)
