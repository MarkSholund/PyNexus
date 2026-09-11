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
import contextlib
import hashlib
import json
import logging
import os
import tempfile
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from http import HTTPMethod
from pathlib import Path

import aiofiles
import httpx2
from fastapi import HTTPException, Request, Response

from app import config

logger = logging.getLogger("uvicorn")


# ----------------------------------------------------------------------
# Path safety utilities
# ----------------------------------------------------------------------
def safe_cache_path(cache_root: Path, *parts: Iterable[str]) -> Path:
    """
    Build a safe path within cache_root from user-supplied components.
    Rejects absolute paths, drive letters, UNC paths, and traversal attempts.
    Works cross-platform (POSIX/Windows).
    """
    cache_root = cache_root.resolve()

    segments: list[str] = []
    for p in parts:
        if p is None:
            continue

        if not isinstance(p, str):
            p = str(p)

        # --- Windows-specific checks ---
        if len(p) >= 2 and p[1] == ":":
            raise ValueError(f"Drive letter path not allowed: {p}")
        if p.startswith("\\\\"):
            raise ValueError(f"UNC path not allowed: {p}")
        if p.startswith("\\"):
            raise ValueError(f"Absolute path not allowed: {p}")

        # --- General absolute path checks ---
        p_obj = Path(p)
        if p_obj.is_absolute():
            raise ValueError(f"Absolute path not allowed: {p}")

        # --- Reject traversal segments ---
        for seg in p_obj.parts:
            if seg in ("..", "/", "\\"):
                raise ValueError(f"Path traversal not allowed: {p}")
            segments.append(seg)

    candidate = cache_root.joinpath(*segments)

    try:
        candidate_abs = candidate.resolve(strict=False)
    except (FileNotFoundError, TypeError):
        parent_resolved = candidate.parent.resolve()
        candidate_abs = parent_resolved.joinpath(candidate.name)

    try:
        candidate_abs.relative_to(cache_root)
    except ValueError:
        raise ValueError(f"Refused unsafe path outside cache: {candidate_abs}")

    return candidate_abs


# ----------------------------------------------------------------------
# Network fetch + local caching (atomic, safe)
# ----------------------------------------------------------------------

# Hop-by-hop / connection-specific headers must never be forwarded verbatim
# to an upstream server: httpx2 computes its own framing (content-length,
# connection handling) for the outgoing request, so passing through a
# caller's original values risks request smuggling / mismatched framing
# upstream. Centralized here since fetch_and_cache is the single chokepoint
# every outbound registry request (pypi/npm/maven) goes through, so any
# caller that forwards client-supplied headers is protected automatically.
_HOP_BY_HOP_HEADERS = frozenset(
    {
        "host",
        "content-length",
        "transfer-encoding",
        "connection",
        "keep-alive",
        "upgrade",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
    }
)


def strip_hop_by_hop_headers(headers: dict) -> dict:
    """Remove hop-by-hop headers before forwarding to an upstream server."""
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP_HEADERS}


async def fetch_and_cache(
    url: str,
    dest: Path,
    method: HTTPMethod = HTTPMethod.GET,
    data: bytes | None = None,
    headers: dict | None = None,
    return_json: bool = False,
    timeout: float = 60.0,
    force_refresh: bool = False,
):
    """
    Fetch from upstream (GET or POST), save to local cache atomically, optionally return JSON.
    """
    cache_root = config.CACHE_DIR.resolve()
    try:
        dest_abs = dest.resolve(strict=False)
        dest_abs.relative_to(cache_root)
    except Exception:
        logger.warning("Refused fetch_and_cache for dest outside cache: %s", dest)
        raise HTTPException(status_code=400, detail="Invalid cache destination")

    if dest.exists() and not force_refresh:
        if return_json:
            async with aiofiles.open(dest, "r", encoding="utf-8") as f:
                content = await f.read()
            return json.loads(content)
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)

    request_headers = strip_hop_by_hop_headers(headers) if headers else {}
    request_headers.setdefault("user-agent", "fastapi-nexus-proxy/1.0")

    async with httpx2.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        try:
            if method == HTTPMethod.POST:
                resp = await client.post(url, content=data, headers=request_headers)
            else:
                resp = await client.get(url, headers=request_headers)
            resp.raise_for_status()
        except httpx2.HTTPStatusError as e:
            raise HTTPException(
                status_code=e.response.status_code,
                detail=f"Upstream error: {url}",
            ) from e
        except httpx2.HTTPError as e:
            # covers timeouts, connection errors, etc. — not just bad status codes
            raise HTTPException(
                status_code=502,
                detail=f"Failed to reach upstream: {url}",
            ) from e

    if return_json:
        text = resp.text
        payload = text.encode("utf-8")
    else:
        payload = resp.content

    await asyncio.to_thread(_atomic_write, dest, payload)

    if return_json:
        return json.loads(text)
    return dest


def _atomic_write(dest: Path, data: bytes) -> None:
    """Write `data` to `dest` atomically via a temp file + os.replace. Runs in a worker thread."""
    tmpname = None
    try:
        fd, tmpname = tempfile.mkstemp(dir=dest.parent)
        with os.fdopen(fd, "wb") as tf:
            tf.write(data)
            tf.flush()
            os.fsync(tf.fileno())
        os.replace(tmpname, dest)
        tmpname = None
    finally:
        if tmpname is not None:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmpname)


# ----------------------------------------------------------------------
# Cached file operations
# ----------------------------------------------------------------------
def open_cached_file(path: Path) -> bytes:
    """
    Read bytes from cached file.
    Caller should have validated containment; refuses non-files.
    """
    if path.exists() and path.is_file():
        return path.read_bytes()
    raise FileNotFoundError(path)


def make_etag_and_last_modified(path: Path):
    stat = path.stat()
    etag = hashlib.sha256(
        f"{path.name}-{stat.st_mtime}-{stat.st_size}".encode()
    ).hexdigest()
    last_modified = datetime.fromtimestamp(stat.st_mtime, tz=UTC).strftime(
        "%a, %d %b %Y %H:%M:%S GMT"
    )
    return etag, last_modified


def file_headers(path: Path) -> dict:
    etag, last_modified = make_etag_and_last_modified(path)
    return {"ETag": etag, "Last-Modified": last_modified}


# ----------------------------------------------------------------------
# Conditional file responses with validation
# ----------------------------------------------------------------------
async def conditional_file_response(
    request: Request,
    path: Path,
    media_type: str,
    attachment: bool | None = False,
) -> Response:
    """
    Return a Response with ETag/Last-Modified headers and conditional GET support.

    Additional safety:
    - Re-verify path is inside configured cache directory before serving.
    - Refuse serving symlinks: nothing in this app creates symlinks under
      the cache dir, so any symlink found there is unexpected.
    """
    if not path.exists():
        raise FileNotFoundError(path)

    # SECURITY: check the pre-resolution path for a symlink; path.resolve()
    # below follows symlinks to their real target, so checking is_symlink()
    # on the *resolved* path would always be False and miss this case.
    if path.is_symlink():
        logger.warning("Refused to serve symlink in cache: %s", path)
        raise FileNotFoundError(path)

    cache_root = config.CACHE_DIR.resolve()
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(cache_root)
    except Exception:
        logger.warning("Refused to serve file outside cache: %s", path)
        raise FileNotFoundError(path)

    etag, last_modified = make_etag_and_last_modified(resolved)
    if_none_match = request.headers.get("if-none-match") or request.headers.get(
        "If-None-Match"
    )
    if_modified_since = request.headers.get("if-modified-since") or request.headers.get(
        "If-Modified-Since"
    )

    if if_none_match == etag or if_modified_since == last_modified:
        return Response(status_code=304)

    headers = file_headers(resolved)
    if attachment:
        headers["Content-Disposition"] = f'attachment; filename="{resolved.name}"'

    content = open_cached_file(resolved)
    return Response(content=content, headers=headers, media_type=media_type)


def is_cache_stale(path: Path, max_age_hours: int = 24) -> bool:
    """
    Check if cached file is stale and needs refreshing.

    Args:
        path: Path to cached file
        max_age_hours: Maximum age in hours before considering stale

    Returns:
        True if file doesn't exist or is older than max_age_hours
    """
    try:
        if not path.exists():
            return True
        file_age_seconds = time.time() - path.stat().st_mtime
        max_age_seconds = max_age_hours * 3600
        return file_age_seconds > max_age_seconds
    except (OSError, FileNotFoundError):
        # If we can't stat the file (e.g., parent dir doesn't exist), consider it stale
        return True


async def fetch_and_serve_json(
    url: str, local_path: Path, request: Request
) -> Response:
    """
    Helper to fetch JSON from upstream into a given local cache path and return
    a conditional JSON response. Caller is responsible for validating/constructing
    `local_path` (use `safe_join_path` in route handlers).

    Args:
        url: Upstream URL to fetch JSON from
        local_path: Destination Path inside configured cache
        request: FastAPI Request object (for conditional response headers)

    Returns:
        fastapi.Response containing JSON content (or raises HTTPException/FileNotFoundError)
    """
    # Ensure parent dir exists
    local_path.parent.mkdir(parents=True, exist_ok=True)

    if not local_path.exists():
        await fetch_and_cache(url, local_path, return_json=False)

    return await conditional_file_response(request, local_path, "application/json")
