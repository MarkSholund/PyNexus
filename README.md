# PyNexus

A simple **FastAPI-based proxy server** for caching and serving Maven and PyPI artifacts locally. This service acts as a local cache for upstream repositories, reducing external requests and improving artifact availability.

---

## Features

- **Maven Proxy**
  - Serves artifacts from a local cache.
  - Fetches missing artifacts from [Maven Central](https://repo1.maven.org/maven2).
  - Returns appropriate HTTP status codes for missing or failed artifacts.

- **PyPI Proxy**
  - Serves package index (`simple/`) and JSON metadata (`/json`) from a local cache.
  - Caches actual package files under `packages/`.
  - Fetches missing packages from [PyPI](https://pypi.org).
  - Supports conditional GET with ETag and Last-Modified headers.

- **NPM Proxy**
  - Serves package metadata (registry JSON) and on-demand tarballs.
  - Metadata is cached under `cache/npm/...`; tarballs are fetched and cached when requested.
  - Fetches metadata and tarballs from `https://registry.npmjs.org`.
  - Scoped packages (e.g. `@scope/name`) are encoded when composing upstream URLs.

---

## Preparation

Before Nexus can be installed on an offline system, the dependencies need to be available.
On your UNCLASS system:

```bash
bash update_requirements.sh # creates a new requirements.txt file and downloads all dependencies
```

## Installation

1. **Clone the repository**

```bash
git clone <your-repo-url>
cd <repo-directory>
```

2. **Create a Python virtual environment**

```bash
python3 -m venv venv
source venv/bin/activate
```

3. **Install dependencies**

```bash
bash install_requirements.sh
```

---

## Configuration

- **Upstream URLs**

```python
MAVEN_UPSTREAM = "https://repo1.maven.org/maven2"
PYPI_UPSTREAM = "https://pypi.org"
```

- **Local cache directories**

```python
from pathlib import Path
CACHE_DIR = Path(os.environ.get("NEXUS_CACHE_DIR", "cache"))
MAVEN_CACHE = CACHE_DIR / "maven"
PYPI_CACHE = CACHE_DIR / "pypi"
NPM_CACHE = config.CACHE_DIR / "npm"
```

Ensure these directories exist and are writable by the application.

---

## Usage

Run the FastAPI server using **Uvicorn**:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Endpoints

- **Maven proxy**

```
GET /maven2/{groupId}/{artifactId}/{version}/{artifact}.jar
```

- **PyPI proxy**

```
GET /pypi/simple/                   # Root HTML index
GET /pypi/simple/{package}/          # Package index HTML
GET /pypi/{package}/json             # Package JSON metadata
GET /pypi/{package}/{version}/json   # Version-specific JSON
GET /pypi/packages/{path}            # Package files (wheel, tar, zip, etc.)
```

- **NPM proxy**

```
GET /npm/{package}                   # Package metadata JSON (cached)
GET /npm/{package}/-/{tarball}       # Package tarball (fetched on-demand)
POST /npm/-/npm/v1/security/advisories/bulk  # Proxies npm audit bulk requests (cached by body hash)
```

---

## Caching Behavior

### Cache Expiration (TTL)

- **Metadata files** (indexes, JSON manifests) are automatically refreshed if older than configured TTL (default: 24 hours)
  - PyPI: `/simple/` indexes and `/pypi/{package}/json` endpoints
  - NPM: `/npm/{package}` metadata and security advisories
  - Maven: `.xml`, `.pom`, `.sha1`, `.md5` files
  
  Override with `PYPI_METADATA_TTL_HOURS`, `NPM_METADATA_TTL_HOURS`, `MAVEN_METADATA_TTL_HOURS` environment variables.

- **Artifact files** (JARs, wheels, tarballs, packages) are cached indefinitely once downloaded
  - No automatic refresh; delete from cache manually to re-download

- Set TTL to `0` to disable automatic cache updates and treat all cached files as immutable.

### Response Behavior

- Files are served from the local cache if they exist and are fresh (within TTL).
- Missing or stale files are fetched from upstream, cached, and served.
- Supports **ETag** and **Last-Modified** headers for conditional GET requests (returns 304 Not Modified when appropriate).
- Upstream errors return proper HTTP status codes:
  - `404` for not found
  - `502` or upstream status code for other errors
- Network timeouts and transient errors automatically retry up to `MAX_RETRIES` times (default: 3).

### Registry-Specific Notes

- **NPM**: Package metadata and security advisories are cached by package name/content hash; tarballs are fetched on-demand.
- **PyPI**: Simple indexes are HTML with rewritten links to route through the proxy.
- **Maven**: Metadata and artifacts are distinguished by file extension; only metadata is refreshed.

---

## Environment Variables

### Cache and Storage

- `NEXUS_CACHE_DIR` — Override local cache directory (default: `cache`)

```bash
export NEXUS_CACHE_DIR=/path/to/cache
```

### Network and Timeout Configuration

- `REQUEST_TIMEOUT_SECONDS` — HTTP request timeout in seconds (default: `30`)
  - Controls timeout for all upstream HTTP requests to Maven, PyPI, and NPM registries

```bash
export REQUEST_TIMEOUT_SECONDS=60
```

- `MAX_RETRIES` — Number of retries for transient failures (default: `3`)
  - Automatic retries on network timeouts and transient errors

```bash
export MAX_RETRIES=5
```

### Cache TTL Configuration

Metadata files (package indexes, JSON manifests) are automatically refreshed if older than the configured TTL. Artifact files (JARs, wheels, tarballs) are cached indefinitely once downloaded.

- `PYPI_METADATA_TTL_HOURS` — PyPI metadata cache TTL (default: `24`)
  - Applies to `/pypi/simple/` indexes and `/pypi/{package}/json` endpoints

```bash
export PYPI_METADATA_TTL_HOURS=12
```

- `NPM_METADATA_TTL_HOURS` — NPM metadata cache TTL (default: `24`)
  - Applies to `/npm/{package}` metadata and security advisories

```bash
export NPM_METADATA_TTL_HOURS=24
```

- `MAVEN_METADATA_TTL_HOURS` — Maven metadata cache TTL (default: `24`)
  - Applies to `.xml`, `.pom`, `.sha1`, `.md5` files

```bash
export MAVEN_METADATA_TTL_HOURS=48
```

Set TTL to `0` to disable automatic cache refresh (treat cached metadata as immutable).

### Upstream Registry Overrides (Optional)

- `NPM_REGISTRY` — Override NPM registry URL (default: `https://registry.npmjs.org`)
- `PYPI_REGISTRY` — Override PyPI registry URL (default: `https://pypi.org`)
- `MAVEN_CENTRAL` — Override Maven Central URL (default: `https://repo1.maven.org/maven2`)

---

---

## 🧪 Running Tests

Tests are written using **pytest** and include coverage reporting.

### 1. Install test dependencies

```bash
pip install -r test-requirements.txt
```

Your `test-requirements.txt` should include:

```text
-r requirements.txt
pytest
pytest-asyncio
pytest-cov
pytest-mock
```

### 2. Run all tests

```bash
PYTHONPATH=. pytest
```

### 3. Run tests with coverage summary

```bash
PYTHONPATH=. pytest --cov=app --cov-report=term-missing -v
```

### 4. Generate an HTML coverage report

```bash
PYTHONPATH=. pytest --cov=app --cov-report=html
```

Then open `htmlcov/index.html` in your browser to view detailed results.

---

## Security Notes

### Cache Ownership and Safety

- The server enforces strict path containment for all cache operations. Key helpers are in `app/utils.py`:
  - `safe_cache_path` validates and builds cache paths from user input.
  - `fetch_and_cache` verifies destinations are under the configured cache root, writes atomically (temp file + `os.replace`), and retries on transient failures.
  - `conditional_file_response` re-resolves files before serving and supports conditional GETs (ETag / Last-Modified).

- For safety, run the service with a `NEXUS_CACHE_DIR` owned by the same user as the process and not writable by untrusted local users. This reduces symlink or replacement race risks.

---

## License

This project is licensed under the **GNU General Public License v3.0 (GPL-3.0)**.

- A copy of the full license is included in the `LICENSE` file.
- Include the following header in each source file:

```python
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
# along with this program. If not, see <https://www.gnu.org/licenses/>
```

---

## Notes

- Recommended for internal artifact caching and proxying.
- Ensure cache directories have sufficient disk space for artifacts.
- Request timeouts are configurable via `REQUEST_TIMEOUT_SECONDS` environment variable (default: 30 seconds).
- Transient failures automatically retry up to `MAX_RETRIES` times (default: 3).
- Supports conditional GETs to reduce unnecessary bandwidth.
- The app uses standard Python `logging` for cache hits, upstream fetches, and errors.
