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
from app.validators import (
    validate_npm_package_name,
    validate_pypi_package_name,
    validate_pypi_artifact_path,
    validate_maven_path,
    validate_version_string,
    safe_join_path,
    ValidationError
)
from pathlib import Path


class TestNPMValidation:
    def test_valid_npm_packages(self):
        assert validate_npm_package_name("lodash")
        assert validate_npm_package_name("@types/react")
        assert validate_npm_package_name("express-validator")
        assert validate_npm_package_name("@babel/core")
    
    def test_invalid_npm_packages(self):
        assert not validate_npm_package_name("../../../etc/passwd")
        assert not validate_npm_package_name("/etc/passwd")
        assert not validate_npm_package_name("package\0name")
        assert not validate_npm_package_name("..\\windows\\system32")
        assert not validate_npm_package_name("")
        assert not validate_npm_package_name("a" * 215)  # Too long


class TestPyPIValidation:
    def test_valid_pypi_packages(self):
        assert validate_pypi_package_name("requests")
        assert validate_pypi_package_name("Django")
        assert validate_pypi_package_name("scikit-learn")
        assert validate_pypi_package_name("Pillow")
    
    def test_invalid_pypi_packages(self):
        assert not validate_pypi_package_name("../../../etc/passwd")
        assert not validate_pypi_package_name("/etc/passwd")
        assert not validate_pypi_package_name("package\0name")
        assert not validate_pypi_package_name("")


class TestPyPIArtifactPathValidation:
    def test_valid_artifact_paths(self):
        assert validate_pypi_artifact_path(
            "aa/bb/cccccccccccccccccccccccccccccccc/requests-2.0.0-py3-none-any.whl")
        assert validate_pypi_artifact_path("somepackage/file.whl")
        assert validate_pypi_artifact_path(
            "requests/requests-2.0.0+local.tar.gz")

    def test_rejects_traversal_and_absolute(self):
        assert not validate_pypi_artifact_path("../../../etc/passwd")
        assert not validate_pypi_artifact_path("/etc/passwd")
        assert not validate_pypi_artifact_path("a/../../b")
        assert not validate_pypi_artifact_path("..\\windows\\system32")

    def test_rejects_null_byte(self):
        assert not validate_pypi_artifact_path("file\0name.whl")

    def test_rejects_control_characters(self):
        # CR/LF and other control chars must not be smuggled into the
        # upstream request URL.
        assert not validate_pypi_artifact_path("file\r\nX-Injected: 1")
        assert not validate_pypi_artifact_path("file\nname.whl")

    def test_rejects_double_slash(self):
        assert not validate_pypi_artifact_path("a//b")

    def test_rejects_empty_and_too_long(self):
        assert not validate_pypi_artifact_path("")
        assert not validate_pypi_artifact_path("a" * 1025)

    def test_rejects_disallowed_characters(self):
        assert not validate_pypi_artifact_path("a/b?query=1")
        assert not validate_pypi_artifact_path("a/b#frag")
        assert not validate_pypi_artifact_path("a/b c")


class TestMavenValidation:
    def test_valid_maven_paths(self):
        assert validate_maven_path("org/springframework/spring-core/5.3.0/spring-core-5.3.0.jar")
        assert validate_maven_path("com/google/guava/guava/30.1-jre/guava-30.1-jre.pom")
    
    def test_invalid_maven_paths(self):
        assert not validate_maven_path("../../../etc/passwd")
        assert not validate_maven_path("/etc/passwd")
        assert not validate_maven_path("C:\\Windows\\System32")
        assert not validate_maven_path("path//with//double//slashes")


class TestSafeJoinPath:
    def test_safe_paths(self, tmp_path):
        result = safe_join_path(tmp_path, "npm", "lodash")
        assert result.is_relative_to(tmp_path)
    
    def test_path_traversal_blocked(self, tmp_path):
        with pytest.raises(ValidationError):
            safe_join_path(tmp_path, "..", "etc", "passwd")
        
        with pytest.raises(ValidationError):
            safe_join_path(tmp_path, "npm", "..", "..", "etc")