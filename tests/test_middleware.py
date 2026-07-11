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

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.middleware import MaxBodySizeMiddleware


async def _echo(request):
    body = await request.body()
    return PlainTextResponse(f"got {len(body)} bytes")


def _make_app(max_bytes: int) -> Starlette:
    app = Starlette(routes=[Route("/echo", _echo, methods=["POST"])])
    app.add_middleware(MaxBodySizeMiddleware, max_bytes=max_bytes)
    return app


def test_rejects_oversized_body():
    app = _make_app(max_bytes=10)
    with TestClient(app) as client:
        response = client.post("/echo", content=b"x" * 1000)
    assert response.status_code == 413


def test_allows_body_within_limit():
    app = _make_app(max_bytes=1024)
    with TestClient(app) as client:
        response = client.post("/echo", content=b"hello")
    assert response.status_code == 200
    assert response.text == "got 5 bytes"


def test_allows_body_at_exact_limit():
    app = _make_app(max_bytes=5)
    with TestClient(app) as client:
        response = client.post("/echo", content=b"12345")
    assert response.status_code == 200
    assert response.text == "got 5 bytes"


def test_rejects_body_one_byte_over_limit():
    app = _make_app(max_bytes=5)
    with TestClient(app) as client:
        response = client.post("/echo", content=b"123456")
    assert response.status_code == 413


class _NoopApp:
    def __init__(self):
        self.called = False

    async def __call__(self, scope, receive, send):
        self.called = True


async def _noop_receive():
    return {"type": "lifespan.startup"}


async def _noop_send(message):
    pass


@pytest.mark.asyncio
async def test_non_http_scope_passthrough():
    """Non-HTTP scopes (e.g. lifespan/websocket) should be passed through untouched."""
    inner = _NoopApp()
    app = MaxBodySizeMiddleware(app=inner, max_bytes=10)
    await app({"type": "lifespan"}, _noop_receive, _noop_send)
    assert inner.called
