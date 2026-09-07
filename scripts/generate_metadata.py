#!/usr/bin/env python3
"""Generate the wkls metadata parquet file from Overture Maps data.

This script queries the Overture Maps division_area GeoParquet on S3,
extracts the metadata columns (no geometry), and writes a compact
ZSTD-compressed parquet file for bundling with the wkls package.

The Overture version used is embedded in the parquet file-level metadata
under the key ``overture_version`` for traceability.

Usage:
    # Auto-detect latest Overture release
    uv run python scripts/generate_metadata.py

    # Pin a specific version
    uv run python scripts/generate_metadata.py --version 2026-08-19.0

    # List available releases
    uv run python scripts/generate_metadata.py --list

Requirements:
    sedonadb, pyarrow (both are wkls dependencies)
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import sedonadb

# S3 bucket constants (same as wkls/core.py)
_S3_BUCKET_URL = "http://overturemaps-us-west-2.s3.amazonaws.com/"
_S3_RELEASE_PREFIX = "release/"
_S3_DIVISION_AREA_SUFFIX = "theme=divisions/type=division_area/"
_S3_DIVISION_SUFFIX = "theme=divisions/type=division/"

# Output path
_OUTPUT_PATH = (
    Path(__file__).resolve().parent.parent / "wkls" / "data" / "overture.zstd18.parquet"
)

# Compression settings
_COMPRESSION = "zstd"
_COMPRESSION_LEVEL = 18
_DATA_PAGE_SIZE = 1 << 20  # 1 MB


def list_s3_releases() -> list[str]:
    """List available Overture Maps releases on S3.

    Returns:
        Sorted list of version strings.

    Raises:
        ConnectionError: If the S3 listing request fails.
    """
    url = f"{_S3_BUCKET_URL}?list-type=2&prefix={_S3_RELEASE_PREFIX}&delimiter=/"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            xml_data = response.read()
    except (urllib.error.URLError, TimeoutError) as e:
        raise ConnectionError(
            f"Failed to list Overture Maps releases from S3: {e}"
        ) from e

    root = ET.fromstring(xml_data)
    ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}

    versions = []
    for prefix_elem in root.findall("s3:CommonPrefixes/s3:Prefix", ns):
        prefix = prefix_elem.text or ""
        version = prefix.removeprefix(_S3_RELEASE_PREFIX).rstrip("/")
        if version:
            versions.append(version)

    return sorted(versions)


def overture_uri(version: str) -> str:
    """Build the S3 URI for a given Overture Maps division_area version."""
    return f"s3://overturemaps-us-west-2/{_S3_RELEASE_PREFIX}{version}/{_S3_DIVISION_AREA_SUFFIX}"


def division_uri(version: str) -> str:
    """Build the S3 URI for the division (hierarchy metadata) type."""
    return f"s3://overturemaps-us-west-2/{_S3_RELEASE_PREFIX}{version}/{_S3_DIVISION_SUFFIX}"


def _extract_en(names: dict | None) -> str | None:
    """Pull the English name from Overture's ``names`` struct.

    ``names.common`` is ``Map(key -> value)`` but pyarrow serializes it as a
    list of ``{"key": ..., "value": ...}`` entries once converted to Python.
    Handle both shapes defensively.
    """
    if not names:
        return None
    common = names.get("common")
    if not common:
        return None
    if isinstance(common, list):
        for entry in common:
            if not entry:
                continue
            if isinstance(entry, dict):
                k, v = entry.get("key"), entry.get("value")
            else:
                k, v = entry
            if k == "en":
                return v
        return None
    if isinstance(common, dict):
        return common.get("en")
    return None


def generate_metadata(version: str) -> None:
    """Generate the metadata parquet file for a given Overture version.

    Args:
        version: Overture Maps release version string.
    """
    uri = overture_uri(version)
    print(f"Overture version: {version}")
    print(f"Source URI:       {uri}")
    print(f"Output path:     {_OUTPUT_PATH}")
    print()

    # Connect to SedonaDB
    print("Connecting to SedonaDB...")
    sedona = sedonadb.connect()
    sedona.sql("SET datafusion.execution.parquet.pushdown_filters = true")

    # Read the Overture division_area GeoParquet (polygons) and the companion
    # division type (hierarchy metadata). We join on division_area.division_id
    # = division.id to bring parent_division_id onto each row.
    print("Reading Overture division_area from S3 (this may take a minute)...")
    sedona.read_parquet(
        uri,
        options={
            "aws.skip_signature": True,
            "aws.region": "us-west-2",
        },
    ).to_view("overture")

    print("Reading Overture division (hierarchy) from S3...")
    sedona.read_parquet(
        division_uri(version),
        options={
            "aws.skip_signature": True,
            "aws.region": "us-west-2",
        },
    ).to_view("division")

    # Query: filter to relevant subtypes + is_land. Resolve each row's
    # parent into our own bundle's primary key (``id`` = division_area.id)
    # via a double-join: division_area → division (for parent_division_id)
    # → division_area (to get the parent's own division_area.id).
    #
    # Bundling `parent_id` as a direct self-reference means:
    #   - `_fetch_row(id)` is a straight `WHERE id = ?` lookup.
    #   - No extra UUID columns in the bundle (saves ~7 MB vs. keeping
    #     both division_id and parent_division_id).
    #
    # Sorted by (country, subtype, region) for row-group stats + predicate
    # pushdown on the common filter patterns.
    print("Filtering and extracting metadata columns...")
    query = """
        WITH parent_map AS (
            -- One row per division.id; pick any matching division_area.id as
            -- the canonical parent_id. MAX gives a deterministic pick.
            SELECT division_id, MAX(id) AS da_id
            FROM overture
            WHERE is_land = true
            GROUP BY division_id
        )
        SELECT
            da.id,
            da.country,
            da.region,
            da.subtype,
            da.names.primary AS name_primary,
            da.names AS names_full,
            pm.da_id AS parent_id
        FROM overture da
        LEFT JOIN division d ON da.division_id = d.id
        LEFT JOIN parent_map pm ON d.parent_division_id = pm.division_id
        WHERE da.subtype IN ('country', 'region', 'locality', 'localadmin', 'county', 'dependency')
          AND da.is_land = true
        ORDER BY da.country ASC, da.subtype ASC, da.region ASC
    """
    df = sedona.sql(query)

    # Convert to PyArrow and extract name_en from the names.common map in Python
    # (sedonadb lacks element_at / map access for struct-of-map types).
    print("Converting to Arrow table...")
    table = df.to_arrow_table()
    name_en = [
        _extract_en(table.column("names_full")[i].as_py())
        for i in range(table.num_rows)
    ]
    table = table.drop_columns(["names_full"]).append_column(
        "name_en", pa.array(name_en, pa.string())
    )
    print(f"Rows: {table.num_rows:,}")
    print(f"Columns: {table.column_names}")
    print()

    # Embed the Overture version in parquet file-level metadata for traceability
    existing_metadata = table.schema.metadata or {}
    metadata = {
        **existing_metadata,
        b"overture_version": version.encode(),
    }
    table = table.replace_schema_metadata(metadata)

    # Write with ZSTD level 18 compression
    print(
        f"Writing parquet (compression={_COMPRESSION}, level={_COMPRESSION_LEVEL})..."
    )
    _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        table,
        str(_OUTPUT_PATH),
        compression=_COMPRESSION,
        compression_level=_COMPRESSION_LEVEL,
        data_page_size=_DATA_PAGE_SIZE,
    )

    size_mb = os.path.getsize(_OUTPUT_PATH) / (1024 * 1024)
    print(f"Done! File size: {size_mb:.1f} MB")

    # Verify the embedded metadata
    meta = pq.read_metadata(str(_OUTPUT_PATH))
    stored_version = meta.metadata.get(b"overture_version", b"").decode()
    print(f"Embedded overture_version: {stored_version}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate the wkls metadata parquet file from Overture Maps data."
    )
    parser.add_argument(
        "--version",
        help="Overture Maps release version to use (default: latest)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available Overture Maps releases and exit",
    )
    args = parser.parse_args()

    if args.list:
        releases = list_s3_releases()
        print("Available Overture Maps releases:")
        for release in releases:
            print(f"  {release}")
        return

    # Resolve version
    if args.version:
        available = list_s3_releases()
        if args.version not in available:
            print(
                f"Error: Version '{args.version}' not available.\n"
                f"Available: {', '.join(available)}",
                file=sys.stderr,
            )
            sys.exit(1)
        version = args.version
    else:
        print("Auto-detecting latest Overture Maps release...")
        available = list_s3_releases()
        if not available:
            print("Error: No releases found on S3.", file=sys.stderr)
            sys.exit(1)
        version = available[-1]

    generate_metadata(version)


if __name__ == "__main__":
    main()
