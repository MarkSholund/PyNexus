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
import re
from pathlib import Path
from typing import Optional


class ValidationError(ValueError):
    """Custom exception for validation failures"""
    pass


def validate_npm_package_name(package: str) -> bool:
    """
    Validate NPM package name format according to NPM specifications.
    
    Rules:
    - Can be scoped (@scope/name) or unscoped (name)
    - Scope must start with @
    - Names can contain lowercase letters, numbers, hyphens, underscores, dots
    - Length must be <= 214 characters
    - Cannot start with dot or underscore
    
    Args:
        package: Package name to validate
        
    Returns:
        True if valid, False otherwise
        
    Examples:
        >>> validate_npm_package_name("lodash")
        True
        >>> validate_npm_package_name("@types/react")
        True
        >>> validate_npm_package_name("../../../etc/passwd")
        False
    """
    if not package or len(package) > 214:
        return False
    
    # Check for path traversal attempts
    if '..' in package or package.startswith('/') or '\\' in package or '\0' in package:
        return False
    
    # NPM scoped package pattern: @scope/name
    scoped_pattern = r'^@[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*$'
    # NPM unscoped package pattern
    unscoped_pattern = r'^[a-z0-9][a-z0-9._-]*$'
    
    return bool(
        re.match(scoped_pattern, package, re.IGNORECASE) or 
        re.match(unscoped_pattern, package, re.IGNORECASE)
    )


def validate_pypi_package_name(package: str) -> bool:
    """
    Validate PyPI package name format according to PEP 508.
    
    Rules:
    - Letters, numbers, hyphens, underscores, dots allowed
    - Must start with letter or number
    - Length must be <= 214 characters
    - No path traversal characters
    
    Args:
        package: Package name to validate
        
    Returns:
        True if valid, False otherwise
        
    Examples:
        >>> validate_pypi_package_name("requests")
        True
        >>> validate_pypi_package_name("Django-REST-framework")
        True
        >>> validate_pypi_package_name("../etc/passwd")
        False
    """
    if not package or len(package) > 214:
        return False
    
    # Check for path traversal and null bytes
    if '..' in package or package.startswith('/') or '\\' in package or '\0' in package:
        return False
    
    # PyPI allows letters, numbers, hyphens, underscores, dots
    # Must start with alphanumeric
    pattern = r'^[a-zA-Z0-9][a-zA-Z0-9._-]*$'
    return bool(re.match(pattern, package))


def validate_version_string(version: str) -> bool:
    """
    Validate version string for package managers.
    
    Rules:
    - Alphanumeric, dots, hyphens, underscores, plus signs allowed
    - Length must be <= 100 characters
    - No path traversal characters
    
    Args:
        version: Version string to validate
        
    Returns:
        True if valid, False otherwise
        
    Examples:
        >>> validate_version_string("1.2.3")
        True
        >>> validate_version_string("2.0.0-beta.1")
        True
        >>> validate_version_string("../../../etc")
        False
    """
    if not version or len(version) > 100:
        return False
    
    # Check for path traversal
    if '..' in version or '/' in version or '\\' in version or '\0' in version:
        return False
    
    # Allow semantic versioning and common version formats
    pattern = r'^[a-zA-Z0-9._+-]+$'
    return bool(re.match(pattern, version))


def validate_pypi_artifact_path(path: str) -> bool:
    """
    Validate a PyPI package artifact path (the part after /pypi/packages/).

    Rules:
    - No path traversal, absolute paths, or null bytes
    - No control characters (e.g. CR/LF), which could otherwise be used to
      inject characters into the upstream request URL
    - Only the charset actually used by pythonhosted.org paths is allowed:
      letters, numbers, dots, hyphens, underscores, plus signs, slashes
    - Length must be <= 1024 characters

    Args:
        path: Artifact path to validate

    Returns:
        True if valid, False otherwise

    Examples:
        >>> validate_pypi_artifact_path("aa/bb/cc.../requests-2.0.0-py3-none-any.whl")
        True
        >>> validate_pypi_artifact_path("../../../etc/passwd")
        False
    """
    if not path or len(path) > 1024:
        return False

    # Check for path traversal and dangerous characters
    if '..' in path or path.startswith('/') or '\\' in path or '\0' in path:
        return False

    # Only allow the charset actually used in pythonhosted.org paths.
    # This also rejects control characters such as CR/LF.
    pattern = r'^[a-zA-Z0-9._+/-]+$'
    if not re.match(pattern, path):
        return False

    if '//' in path:
        return False

    return True


def validate_maven_path(path: str) -> bool:
    """
    Validate Maven repository path format.
    
    Rules:
    - Must follow Maven repository structure: group/artifact/version/file
    - No absolute paths
    - No path traversal sequences
    - No null bytes or special characters
    - Length must be <= 1024 characters
    
    Args:
        path: Maven path to validate
        
    Returns:
        True if valid, False otherwise
        
    Examples:
        >>> validate_maven_path("org/springframework/spring-core/5.3.0/spring-core-5.3.0.jar")
        True
        >>> validate_maven_path("../../../etc/passwd")
        False
    """
    if not path or len(path) > 1024:
        return False
    
    # Check for path traversal and dangerous characters
    if '..' in path or path.startswith('/') or '\\' in path or '\0' in path:
        return False
    
    # Check for absolute paths (Windows and Unix)
    if path.startswith(('/', '\\', 'C:', 'c:')):
        return False
    
    # Maven paths should only contain alphanumeric, dots, hyphens, underscores, slashes
    # Allow common file extensions
    pattern = r'^[a-zA-Z0-9._/-]+$'
    if not re.match(pattern, path):
        return False
    
    # Additional check: ensure no double slashes
    if '//' in path:
        return False
    
    return True


def validate_tarball_name(filename: str) -> bool:
    """
    Validate tarball filename.
    
    Rules:
    - Must end with .tgz, .tar.gz, or .tar
    - Alphanumeric, dots, hyphens, underscores allowed
    - Length must be <= 255 characters
    - No path separators
    
    Args:
        filename: Tarball filename to validate
        
    Returns:
        True if valid, False otherwise
    """
    if not filename or len(filename) > 255:
        return False
    
    # No path separators or traversal
    if '/' in filename or '\\' in filename or '..' in filename or '\0' in filename:
        return False
    
    # Must have valid tarball extension
    valid_extensions = ('.tgz', '.tar.gz', '.tar', '.tar.bz2', '.tar.xz')
    if not filename.endswith(valid_extensions):
        return False
    
    # Alphanumeric, dots, hyphens, underscores only
    pattern = r'^[a-zA-Z0-9._-]+\.(?:tgz|tar\.gz|tar\.bz2|tar\.xz|tar)$'
    return bool(re.match(pattern, filename))


def safe_join_path(base: Path, *parts: str) -> Path:
    """
    Safely join path components and ensure result is within base directory.
    
    This is a defense-in-depth measure in addition to input validation.
    
    Args:
        base: Base directory path
        *parts: Path components to join
        
    Returns:
        Resolved path within base directory
        
    Raises:
        ValidationError: If resulting path is outside base directory
        
    Examples:
        >>> base = Path("/cache")
        >>> safe_join_path(base, "npm", "lodash")
        Path('/cache/npm/lodash')
        >>> safe_join_path(base, "..", "etc", "passwd")
        ValidationError: Path traversal detected
    """
    # Join all parts
    result = base
    for part in parts:
        if not part:
            continue
        # Basic validation on each part
        if '..' in part or part.startswith('/') or '\\' in part or '\0' in part:
            raise ValidationError(f"Invalid path component: {part}")
        result = result / part
    
    # Resolve to absolute path
    try:
        resolved = result.resolve()
        base_resolved = base.resolve()
    except (OSError, RuntimeError) as e:
        raise ValidationError(f"Path resolution failed: {e}")
    
    # Ensure resolved path is within base directory
    try:
        resolved.relative_to(base_resolved)
    except ValueError:
        raise ValidationError(
            f"Path traversal detected: {resolved} is outside {base_resolved}"
        )
    
    return resolved