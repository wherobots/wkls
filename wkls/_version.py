"""S3 version discovery for Overture Maps releases."""

from __future__ import annotations

import functools
import importlib.resources
import os
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import pyarrow.parquet

from . import data

# S3 bucket URL for listing Overture Maps releases (HTTP avoids SSL cert
# issues on macOS system Python installs that lack certifi/root certs)
_S3_BUCKET_URL = "http://overturemaps-us-west-2.s3.amazonaws.com/"
_S3_RELEASE_PREFIX = "release/"
_S3_DIVISION_AREA_SUFFIX = "theme=divisions/type=division_area/"

# A release's prefix lists on S3 as soon as its first object lands, and the
# divisions files take several minutes more to arrive. Adopt a release only
# once every division_area object has been in place this long.
_SETTLE_SECONDS = 3600


@functools.lru_cache(maxsize=1)
def _bundled_overture_version() -> str:
    """Overture release the bundled metadata parquet was generated from."""
    path = importlib.resources.files(data) / "overture.zstd18.parquet"
    # Open a file object rather than passing a path: pyarrow's LocalFileSystem
    # is unusable on hosts where sedonadb loaded an Arrow-linked GDAL.
    with path.open("rb") as fh:
        metadata = pyarrow.parquet.read_schema(fh).metadata or {}
    return metadata.get(b"overture_version", b"").decode()


def _release_is_settled(version: str, now: datetime | None = None) -> bool:
    """True once every division_area object of ``version`` has settled on S3.

    Lists the release's division_area prefix and compares the newest
    ``LastModified`` against ``_SETTLE_SECONDS``. A release with no objects
    is not settled. A listing failure counts as settled so a transient S3
    error cannot change which release is chosen.
    """
    url = (
        f"{_S3_BUCKET_URL}?list-type=2&prefix={_S3_RELEASE_PREFIX}{version}/"
        f"{_S3_DIVISION_AREA_SUFFIX}"
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            xml_data = response.read()
    except (urllib.error.URLError, TimeoutError):
        return True
    root = ET.fromstring(xml_data)
    ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
    stamps = [
        datetime.fromisoformat(elem.text.replace("Z", "+00:00"))
        for elem in root.findall("s3:Contents/s3:LastModified", ns)
        if elem.text
    ]
    if not stamps:
        return False
    now = now or datetime.now(timezone.utc)
    return now - max(stamps) >= timedelta(seconds=_SETTLE_SECONDS)


def _list_s3_releases() -> list[str]:
    """List all available Overture Maps releases on S3.

    Queries the public S3 bucket listing to discover available release
    versions. The bucket is unauthenticated and returns XML with
    CommonPrefixes for each release directory.

    Returns:
        Sorted list of version strings (e.g., ["2026-08-19.0", "2026-09-23.0"]).

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
        # Extract version from "release/2026-08-19.0/"
        version = prefix.removeprefix(_S3_RELEASE_PREFIX).rstrip("/")
        if version:
            versions.append(version)

    return sorted(versions)


def _resolve_overture_version() -> str:
    """Resolve which Overture Maps version to use.

    Priority:
        1. ``WKLS_OVERTURE_VERSION`` environment variable
        2. The newest release on S3, unless its divisions upload is still in
           progress, in which case the release before it. The release the
           bundled metadata was built from is complete by construction and
           needs no check.

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
    newest = available[-1]
    if (
        newest != _bundled_overture_version()
        and len(available) > 1
        and not _release_is_settled(newest)
    ):
        return available[-2]
    return newest


def _overture_uri(version: str) -> str:
    """Build the S3 URI for a given Overture Maps version.

    Args:
        version: Overture Maps release version string.

    Returns:
        Full S3 URI to the division_area GeoParquet data.
    """
    return f"s3://overturemaps-us-west-2/{_S3_RELEASE_PREFIX}{version}/{_S3_DIVISION_AREA_SUFFIX}"
