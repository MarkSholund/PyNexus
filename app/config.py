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

from pathlib import Path
import os

# Base cache directory
CACHE_DIR = Path(os.environ.get("NEXUS_CACHE_DIR", "cache"))

# Cache TTL Configuration (in hours)
# Set to 0 to disable automatic cache updates (treat as immutable)

# PyPI TTL settings
PYPI_METADATA_TTL_HOURS: int = int(os.environ.get("PYPI_METADATA_TTL_HOURS", "24"))
PYPI_PACKAGE_TTL_HOURS: int = int(os.environ.get("PYPI_PACKAGE_TTL_HOURS", "0"))  # Immutable artifacts

# NPM TTL settings
NPM_METADATA_TTL_HOURS: int = int(os.environ.get("NPM_METADATA_TTL_HOURS", "24"))
NPM_TARBALL_TTL_HOURS: int = int(os.environ.get("NPM_TARBALL_TTL_HOURS", "0"))  # Immutable artifacts
NPM_SECURITY_TTL_HOURS: int = int(os.environ.get("NPM_SECURITY_TTL_HOURS", "6"))

# Maven TTL settings
MAVEN_METADATA_TTL_HOURS: int = int(os.environ.get("MAVEN_METADATA_TTL_HOURS", "24"))
MAVEN_ARTIFACT_TTL_HOURS: int = int(os.environ.get("MAVEN_ARTIFACT_TTL_HOURS", "0"))  # Immutable artifacts
MAVEN_SNAPSHOT_TTL_HOURS: int = int(os.environ.get("MAVEN_SNAPSHOT_TTL_HOURS", "1"))  # Snapshots change frequently

# Upstream registry URLs (configurable)
NPM_REGISTRY: str = os.environ.get("NPM_REGISTRY", "https://registry.npmjs.org")
PYPI_REGISTRY: str = os.environ.get("PYPI_REGISTRY", "https://pypi.org")
MAVEN_CENTRAL: str = os.environ.get("MAVEN_CENTRAL", "https://repo1.maven.org/maven2")

# Network settings
REQUEST_TIMEOUT_SECONDS: int = int(os.environ.get("REQUEST_TIMEOUT_SECONDS", "30"))
MAX_RETRIES: int = int(os.environ.get("MAX_RETRIES", "3"))

# Maximum accepted size (in bytes) of any incoming request body (enforced by
# MaxBodySizeMiddleware). Prevents unauthenticated memory-exhaustion DoS via
# oversized POST bodies (e.g. npm audit/bulk endpoints, which buffer the
# entire body to hash and forward upstream).
MAX_REQUEST_BODY_BYTES: int = int(os.environ.get("MAX_REQUEST_BODY_BYTES", str(5 * 1024 * 1024)))