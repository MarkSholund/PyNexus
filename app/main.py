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

from fastapi import FastAPI
from app.routes import pypi_routes, maven_routes, npm_routes
from app.middleware import MaxBodySizeMiddleware
import app.config as config
import logging
from contextlib import asynccontextmanager

logger = logging.getLogger('uvicorn')


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"CACHE_DIR => {config.CACHE_DIR}")
    yield
    logger.info("Shutting down FastAPI app")

app = FastAPI(lifespan=lifespan)
app.add_middleware(MaxBodySizeMiddleware,
                    max_bytes=config.MAX_REQUEST_BODY_BYTES)
app.include_router(pypi_routes.router)
app.include_router(maven_routes.router)
app.include_router(npm_routes.router)


@app.get("/")
async def root():
    return {"status": "ok", "message": "Local caching proxy for PyPI and Maven (no compression)"}
