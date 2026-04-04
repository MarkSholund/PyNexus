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
from fastapi.testclient import TestClient
from pathlib import Path

import app.config as config
from app.routes import ui_routes


@pytest.fixture(autouse=True)
def patch_cache_dir(tmp_path, monkeypatch):
    """Redirect config.CACHE_DIR to a temp directory for every test."""
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    yield tmp_path


@pytest.fixture()
def client():
    from app.main import app
    return TestClient(app)


# ── /ui HTML page ─────────────────────────────────────────────────────────────

def test_ui_index_returns_html(client):
    r = client.get("/ui")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "PyNexus" in r.text
    assert "<html" in r.text


def test_ui_index_trailing_slash(client):
    r = client.get("/ui/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_ui_html_contains_tabs(client):
    r = client.get("/ui")
    assert "PyPI" in r.text
    assert "NPM" in r.text
    assert "Maven" in r.text


def test_ui_html_has_api_calls(client):
    """The embedded JS must reference the API base path and the stats endpoint."""
    r = client.get("/ui")
    assert "/ui/api/" in r.text
    assert "/ui/api/stats" in r.text


# ── /ui/api/stats — empty cache ───────────────────────────────────────────────

def test_stats_empty_cache(client):
    r = client.get("/ui/api/stats")
    assert r.status_code == 200
    d = r.json()
    assert d["pypi"]  == {"count": 0, "total_size": 0}
    assert d["npm"]   == {"count": 0, "total_size": 0}
    assert d["maven"] == {"count": 0, "total_size": 0}


# ── /ui/api/pypi ──────────────────────────────────────────────────────────────

def test_pypi_empty_cache(client):
    r = client.get("/ui/api/pypi")
    assert r.status_code == 200
    d = r.json()
    assert d["repo"] == "pypi"
    assert d["count"] == 0
    assert d["packages"] == []
    assert d["total_size"] == 0


def test_pypi_lists_cached_packages(tmp_path, client):
    simple = tmp_path / "pypi" / "simple"
    pkg = simple / "requests"
    pkg.mkdir(parents=True)
    (pkg / "index.html").write_text("<html>requests</html>")

    r = client.get("/ui/api/pypi")
    d = r.json()
    assert d["count"] == 1
    assert d["packages"][0]["name"] == "requests"
    assert d["packages"][0]["file_count"] == 1
    assert d["packages"][0]["total_size"] > 0
    assert d["packages"][0]["last_modified"] > 0
    assert d["packages"][0]["files"][0]["name"] == "index.html"


def test_pypi_multiple_packages_sorted(tmp_path, client):
    simple = tmp_path / "pypi" / "simple"
    for name in ["numpy", "requests", "aiohttp"]:
        pkg = simple / name
        pkg.mkdir(parents=True)
        (pkg / "index.html").write_text(f"<html>{name}</html>")

    r = client.get("/ui/api/pypi")
    d = r.json()
    names = [p["name"] for p in d["packages"]]
    assert names == sorted(names)
    assert d["count"] == 3


def test_pypi_skips_symlinks(tmp_path, client):
    simple = tmp_path / "pypi" / "simple"
    real_pkg = simple / "requests"
    real_pkg.mkdir(parents=True)
    (real_pkg / "index.html").write_text("<html>requests</html>")

    link_pkg = simple / "requests-link"
    link_pkg.symlink_to(real_pkg)

    r = client.get("/ui/api/pypi")
    d = r.json()
    names = [p["name"] for p in d["packages"]]
    assert "requests" in names
    assert "requests-link" not in names


def test_pypi_total_size_aggregated(tmp_path, client):
    simple = tmp_path / "pypi" / "simple"
    for name, content in [("pkg-a", "aaa"), ("pkg-b", "bb")]:
        p = simple / name
        p.mkdir(parents=True)
        (p / "index.html").write_text(content)

    r = client.get("/ui/api/pypi")
    d = r.json()
    expected = sum(p["total_size"] for p in d["packages"])
    assert d["total_size"] == expected


# ── /ui/api/npm ───────────────────────────────────────────────────────────────

def test_npm_empty_cache(client):
    r = client.get("/ui/api/npm")
    assert r.status_code == 200
    d = r.json()
    assert d["repo"] == "npm"
    assert d["count"] == 0
    assert d["packages"] == []


def test_npm_lists_package_with_metadata(tmp_path, client):
    pkg = tmp_path / "npm" / "lodash"
    pkg.mkdir(parents=True)
    (pkg / "index.json").write_text('{"name":"lodash"}')

    r = client.get("/ui/api/npm")
    d = r.json()
    assert d["count"] == 1
    assert d["packages"][0]["name"] == "lodash"
    assert d["packages"][0]["file_count"] == 1
    assert any(f["name"] == "index.json" for f in d["packages"][0]["files"])


def test_npm_lists_package_with_tarball(tmp_path, client):
    pkg = tmp_path / "npm" / "lodash"
    (pkg / "-").mkdir(parents=True)
    (pkg / "index.json").write_text("{}")
    (pkg / "-" / "lodash-4.17.21.tgz").write_bytes(b"\x00" * 100)

    r = client.get("/ui/api/npm")
    d = r.json()
    assert d["count"] == 1
    pkg_data = d["packages"][0]
    assert pkg_data["file_count"] == 2
    file_names = [f["name"] for f in pkg_data["files"]]
    assert "index.json" in file_names
    assert "lodash-4.17.21.tgz" in file_names


def test_npm_lists_scoped_package(tmp_path, client):
    scoped = tmp_path / "npm" / "@types" / "react"
    scoped.mkdir(parents=True)
    (scoped / "index.json").write_text('{"name":"@types/react"}')

    r = client.get("/ui/api/npm")
    d = r.json()
    assert d["count"] == 1
    assert d["packages"][0]["name"] == "@types/react"


def test_npm_skips_security_dir(tmp_path, client):
    security = tmp_path / "npm" / "security"
    security.mkdir(parents=True)
    (security / "somehash.json").write_text("{}")

    r = client.get("/ui/api/npm")
    d = r.json()
    assert d["count"] == 0


def test_npm_skips_symlinks(tmp_path, client):
    real = tmp_path / "npm" / "lodash"
    real.mkdir(parents=True)
    (real / "index.json").write_text("{}")
    link = tmp_path / "npm" / "lodash-link"
    link.symlink_to(real)

    r = client.get("/ui/api/npm")
    d = r.json()
    names = [p["name"] for p in d["packages"]]
    assert "lodash" in names
    assert "lodash-link" not in names


def test_npm_empty_package_dir_excluded(tmp_path, client):
    """Directories with no files should not appear in the list."""
    (tmp_path / "npm" / "empty-pkg").mkdir(parents=True)

    r = client.get("/ui/api/npm")
    d = r.json()
    assert d["count"] == 0


# ── /ui/api/maven ─────────────────────────────────────────────────────────────

def test_maven_empty_cache(client):
    r = client.get("/ui/api/maven")
    assert r.status_code == 200
    d = r.json()
    assert d["repo"] == "maven"
    assert d["count"] == 0
    assert d["artifacts"] == []


def test_maven_lists_artifact(tmp_path, client):
    version_dir = tmp_path / "maven" / "org" / "springframework" / "spring-core" / "5.3.0"
    version_dir.mkdir(parents=True)
    (version_dir / "spring-core-5.3.0.jar").write_bytes(b"x" * 500)
    (version_dir / "spring-core-5.3.0.pom").write_text("<project/>")

    r = client.get("/ui/api/maven")
    d = r.json()
    assert d["count"] == 1
    a = d["artifacts"][0]
    assert a["group"] == "org.springframework"
    assert a["artifact"] == "spring-core"
    assert a["version"] == "5.3.0"
    assert a["file_count"] == 2
    assert a["total_size"] > 0
    file_names = [f["name"] for f in a["files"]]
    assert "spring-core-5.3.0.jar" in file_names
    assert "spring-core-5.3.0.pom" in file_names


def test_maven_path_uses_forward_slashes(tmp_path, client):
    version_dir = tmp_path / "maven" / "com" / "example" / "mylib" / "1.0"
    version_dir.mkdir(parents=True)
    (version_dir / "mylib-1.0.jar").write_bytes(b"jar")

    r = client.get("/ui/api/maven")
    d = r.json()
    assert "/" in d["artifacts"][0]["path"]
    assert "\\" not in d["artifacts"][0]["path"]


def test_maven_multiple_artifacts_sorted(tmp_path, client):
    for group, artifact, version in [
        ("org/b", "z-lib", "1.0"),
        ("org/a", "a-lib", "2.0"),
        ("org/a", "a-lib", "1.0"),
    ]:
        d = tmp_path / "maven" / group / artifact / version
        d.mkdir(parents=True)
        (d / "file.jar").write_bytes(b"x")

    r = client.get("/ui/api/maven")
    d = r.json()
    assert d["count"] == 3
    groups = [a["group"] for a in d["artifacts"]]
    assert groups == sorted(groups)


def test_maven_skips_dirs_without_files(tmp_path, client):
    """Directories that contain only subdirectories should not appear."""
    (tmp_path / "maven" / "org" / "example").mkdir(parents=True)

    r = client.get("/ui/api/maven")
    d = r.json()
    assert d["count"] == 0


def test_maven_skips_symlinks_in_version_dir(tmp_path, client):
    version_dir = tmp_path / "maven" / "com" / "example" / "lib" / "1.0"
    version_dir.mkdir(parents=True)
    real_file = version_dir / "lib-1.0.jar"
    real_file.write_bytes(b"real")
    link_file = version_dir / "lib-1.0-link.jar"
    link_file.symlink_to(real_file)

    r = client.get("/ui/api/maven")
    d = r.json()
    assert d["count"] == 1
    file_names = [f["name"] for f in d["artifacts"][0]["files"]]
    assert "lib-1.0.jar" in file_names
    assert "lib-1.0-link.jar" not in file_names


# ── /ui/api/stats — populated cache ──────────────────────────────────────────

def test_stats_populated(tmp_path, client):
    # PyPI package
    pypi_pkg = tmp_path / "pypi" / "simple" / "requests"
    pypi_pkg.mkdir(parents=True)
    (pypi_pkg / "index.html").write_text("<html/>")

    # NPM package
    npm_pkg = tmp_path / "npm" / "lodash"
    npm_pkg.mkdir(parents=True)
    (npm_pkg / "index.json").write_text("{}")

    # Maven artifact
    mvn = tmp_path / "maven" / "org" / "example" / "lib" / "1.0"
    mvn.mkdir(parents=True)
    (mvn / "lib-1.0.jar").write_bytes(b"x" * 200)

    r = client.get("/ui/api/stats")
    d = r.json()
    assert d["pypi"]["count"] == 1
    assert d["npm"]["count"] == 1
    assert d["maven"]["count"] == 1
    assert d["pypi"]["total_size"] > 0
    assert d["npm"]["total_size"] > 0
    assert d["maven"]["total_size"] == 200


# ── Scan helpers — unit tests ─────────────────────────────────────────────────

def test_find_leaf_dirs_yields_dirs_with_files(tmp_path):
    leaf = tmp_path / "a" / "b"
    leaf.mkdir(parents=True)
    (leaf / "file.txt").write_text("hello")
    middle = tmp_path / "a"
    results = list(ui_routes._find_leaf_dirs(tmp_path))
    assert leaf in results
    assert middle not in results  # middle has no direct files


def test_find_leaf_dirs_skips_symlinks(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "f.txt").write_text("x")
    link = tmp_path / "link"
    link.symlink_to(real)

    results = list(ui_routes._find_leaf_dirs(tmp_path))
    assert real in results
    assert link not in results


def test_find_leaf_dirs_empty(tmp_path):
    assert list(ui_routes._find_leaf_dirs(tmp_path)) == []


def test_file_info_returns_correct_fields(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("hello")
    info = ui_routes._file_info(f)
    assert info["name"] == "test.txt"
    assert info["size"] == 5
    assert info["mtime"] > 0
