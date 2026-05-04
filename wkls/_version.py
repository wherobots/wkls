"""S3 version discovery for Overture Maps releases."""

from __future__ import annotations

import os
import urllib.request
import xml.etree.ElementTree as ET

# S3 bucket URL for listing Overture Maps releases (HTTP avoids SSL cert
# issues on macOS system Python installs that lack certifi/root certs)
_S3_BUCKET_URL = "http://overturemaps-us-west-2.s3.amazonaws.com/"
_S3_RELEASE_PREFIX = "release/"
_S3_DIVISION_AREA_SUFFIX = "theme=divisions/type=division_area/"


def _list_s3_releases() -> list[str]:
    """List all available Overture Maps releases on S3.

    Queries the public S3 bucket listing to discover available release
    versions. The bucket is unauthenticated and returns XML with
    CommonPrefixes for each release directory.

    Returns:
        Sorted list of version strings (e.g., ["2025-12-17.0", "2026-01-21.0"]).

    Raises:
        ConnectionError: If the S3 bucket listing request fails.
    """
    url = f"{_S3_BUCKET_URL}?list-type=2&prefix={_S3_RELEASE_PREFIX}&delimiter=/"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            xml_data = response.read()
    except (urllib.error.URLError, TimeoutError) as e:
        raise ConnectionError(
            f"Failed to list Overture Maps releases from S3: {e}\n"
            "Check your network connection. Geometry lookups require "
            "internet access to the Overture Maps S3 bucket."
        ) from e

    root = ET.fromstring(xml_data)
    # XML namespace for S3 ListBucketResult
    ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}

    versions = []
    for prefix_elem in root.findall("s3:CommonPrefixes/s3:Prefix", ns):
        prefix = prefix_elem.text or ""
        # Extract version from "release/2025-12-17.0/"
        version = prefix.removeprefix(_S3_RELEASE_PREFIX).rstrip("/")
        if version:
            versions.append(version)

    return sorted(versions)


def _resolve_overture_version() -> str:
    """Resolve which Overture Maps version to use.

    Priority:
        1. ``WKLS_OVERTURE_VERSION`` environment variable
        2. Auto-detect the latest release from S3

    Returns:
        Resolved version string.

    Raises:
        ValueError: If the env var specifies an unavailable version.
        ConnectionError: If S3 listing fails.
    """
    env_version = os.environ.get("WKLS_OVERTURE_VERSION")
    if env_version:
        available = _list_s3_releases()
        if env_version not in available:
            raise ValueError(
                f"Overture Maps version '{env_version}' is not available on S3.\n"
                f"Available versions: {', '.join(available)}\n"
                "Set WKLS_OVERTURE_VERSION to a valid version or remove it "
                "to auto-detect the latest."
            )
        return env_version

    available = _list_s3_releases()
    if not available:
        raise ConnectionError(
            "No Overture Maps releases found on S3. "
            "The S3 bucket may be temporarily unavailable."
        )
    return available[-1]


def _overture_uri(version: str) -> str:
    """Build the S3 URI for a given Overture Maps version.

    Args:
        version: Overture Maps release version string.

    Returns:
        Full S3 URI to the division_area GeoParquet data.
    """
    return f"s3://overturemaps-us-west-2/{_S3_RELEASE_PREFIX}{version}/{_S3_DIVISION_AREA_SUFFIX}"
