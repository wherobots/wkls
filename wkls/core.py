"""
wkls - Well-Known Locations

A Python library for accessing global administrative boundaries using chainable syntax.
Fetches geometries from Overture Maps Foundation GeoParquet data.

Example usage:
    >>> import wkls
    >>> wkls.us.ca.sanfrancisco.wkt()
    'MULTIPOLYGON (((-122.5279985 37.8155806...)))'

    >>> wkls.countries()  # List all countries
    >>> wkls.us.regions()  # List US states/regions
"""

from __future__ import annotations

import importlib.resources
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Callable

import sedonadb
import sqlescapy

from . import data, queries

__all__ = ["Wkl", "ChainableDataFrame"]

# S3 bucket URL for listing Overture Maps releases (HTTP avoids SSL cert
# issues on macOS system Python installs that lack certifi/root certs)
_S3_BUCKET_URL = "http://overturemaps-us-west-2.s3.amazonaws.com/"
_S3_RELEASE_PREFIX = "release/"
_S3_DIVISION_AREA_SUFFIX = "theme=divisions/type=division_area/"

# Module-level state for the active Overture version
_current_overture_version: str | None = None


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


def _log_and_query(
    exec_fn: Callable[[str], sedonadb.dataframe.DataFrame], query: str
) -> sedonadb.dataframe.DataFrame:
    """Execute a SQL query with optional debug logging.

    Args:
        exec_fn: Function to execute the SQL query.
        query: SQL query string to execute.

    Returns:
        DataFrame containing the query results.
    """
    if os.environ.get("WKLS_DEBUG", "false").lower() in ["true", "yes", "1"]:
        print(query)
    return exec_fn(query)


def _initialize_table() -> sedonadb.SedonaContext:
    """Initialize the wkls table and Overture data views.

    Creates SedonaDB views for the local metadata table and remote
    Overture Maps GeoParquet data. Auto-detects the latest Overture
    release unless overridden by the ``WKLS_OVERTURE_VERSION`` env var.

    Returns:
        Configured SedonaContext instance.
    """
    global _current_overture_version

    sedona = sedonadb.connect()

    # Enable interactive mode for auto-display
    sedona.options.interactive = True

    # Monkey-patch `.sql()` for debug mode.
    sedona_sql = sedona.sql
    sedona.sql = lambda q: _log_and_query(sedona_sql, q)

    sedona.sql(queries.INITIALIZATION)
    sedona.read_parquet(
        f"{importlib.resources.files(data)}/overture.zstd18.parquet"
    ).to_view("wkls")

    _current_overture_version = _resolve_overture_version()
    sedona.read_parquet(
        _overture_uri(_current_overture_version),
        options={
            "aws.skip_signature": True,
            "aws.region": "us-west-2",
        },
    ).to_view("overture")
    return sedona


# Initialize the table when the module is imported
sedona = _initialize_table()

# Cache for country region checks (country_iso -> has_region)
# This is static per Overture dataset version, so safe to cache indefinitely
_country_has_region_cache: dict[str, bool] = {}


def sqlescape(v: str) -> str:
    """Escape a string for safe SQL interpolation.

    Escapes special characters while preserving % for LIKE operators.

    Args:
        v: String value to escape.

    Returns:
        SQL-safe escaped string.
    """
    # SQL escape, but maintain the use of % for the LIKE operator.
    return sqlescapy.sqlescape(v).replace("\\%", "%")


# Methods that ChainableDataFrame delegates to Wkl
_WKL_DELEGATED_METHODS = frozenset(
    {
        "wkt",
        "wkb",
        "hexwkb",
        "geojson",
        "svg",
        "dependencies",
        "countries",
        "regions",
        "counties",
        "cities",
        "subtypes",
    }
)


def _build_error_hint(chain: list[str], suggestions: list[str]) -> str:
    """Build error hint message with suggestions and wildcard tip.

    Args:
        chain: List of location identifiers in the chain.
        suggestions: List of suggested location names.

    Returns:
        Formatted hint string with suggestions and wildcard search tip.
    """
    chain_str = ".".join(chain)
    failed_name = chain[-1]
    chain_prefix = ".".join(chain[:-1])

    # Build wildcard example - handle root level specially
    if chain_prefix:
        wildcard_example = f"wkls.{chain_prefix}['%{failed_name}%']"
    else:
        wildcard_example = f"wkls['%{failed_name}%']"

    if suggestions:
        suggestion_hint = f"Did you mean: {', '.join(suggestions)}?\n"
    else:
        suggestion_hint = ""

    return (
        f"No results found for: {chain_str}\n"
        f"{suggestion_hint}"
        f"Tip: Use {wildcard_example} to perform a wildcard search.\n"
    )


class ChainableDataFrame:
    """A DataFrame that maintains chaining capability for the wkls library.

    This class wraps SedonaDB DataFrames to allow attribute-based chaining
    (e.g., `wkls.us.ca.sanfrancisco`) while preserving DataFrame functionality.

    Attributes:
        _chain: List of chained attribute names representing the location path.
    """

    _metadata = ["_chain"]

    def __init__(
        self, df: sedonadb.dataframe.DataFrame, chain: list[str] | None = None
    ) -> None:
        """Initialize a ChainableDataFrame.

        Args:
            df: Source SedonaDB DataFrame to wrap.
            chain: List of chained attribute names (e.g., ['us', 'ca']).
        """
        object.__setattr__(self, "_df", df)
        object.__setattr__(self, "_chain", chain or [])

    def __getattr__(self, attr: str) -> ChainableDataFrame:
        """Handle attribute access for location chaining and method delegation.

        Args:
            attr: Attribute name to access (e.g., 'ca' for California).

        Returns:
            New ChainableDataFrame with the attribute added to the chain,
            or a bound method if attr is a delegated Wkl method.

        Raises:
            AttributeError: For internal attributes or root-only methods.
            ValueError: If chain exceeds maximum depth of 3.
        """
        # Avoid infinite recursion for internal attributes
        if attr.startswith("_") or attr in ["_chain"]:
            raise AttributeError(
                f"'{self.__class__.__name__}' object has no attribute '{attr}'"
            )

        # Block root-level only methods
        if attr in ("overture_version", "overture_releases", "configure"):
            raise AttributeError(
                f"'{attr}' is only available at the root level. Use wkls.{attr}(), not on chained objects."
            )

        # Delegate Wkl methods dynamically
        if attr in _WKL_DELEGATED_METHODS:
            wkl = Wkl(self._chain)
            return getattr(wkl, attr)

        # Continue chaining
        new_wkl = Wkl(self._chain + [attr.lower()])
        # Validate chain length immediately
        if len(new_wkl.chain) > 3:
            raise ValueError("Too many chained attributes (max = 3)")
        if len(new_wkl.chain) <= 3:
            df = new_wkl.resolve()
            return ChainableDataFrame(df, new_wkl.chain)
        return new_wkl

    def __getitem__(
        self, key: Any
    ) -> ChainableDataFrame | sedonadb.dataframe.DataFrame:
        """Handle bracket access for location chaining or DataFrame indexing.

        Supports both DataFrame-style indexing and location chaining with
        search patterns (using % wildcards).

        Args:
            key: Column name, list of columns, slice, or location string.

        Returns:
            ChainableDataFrame for location chaining, or DataFrame for indexing.

        Raises:
            ValueError: If chain exceeds maximum depth of 3.
        """
        # If we have a chain, continue chaining (location access mode)
        if self._chain:
            new_wkl = Wkl(self._chain + [key.lower()])
            # Validate chain length immediately
            if len(new_wkl.chain) > 3 and "%" not in str(key):
                raise ValueError("Too many chained attributes (max = 3)")
            if "%" in str(key):
                return new_wkl.resolve()
            # Return ChainableDataFrame to get hint logic in __repr__
            df = new_wkl.resolve()
            return ChainableDataFrame(df, new_wkl.chain)

        # No chain - this is DataFrame-style indexing or starting a new chain
        # If it contains %, it's a search pattern
        if isinstance(key, str) and "%" in key:
            new_wkl = Wkl([key.lower()])
            return new_wkl.resolve()

        # Regular DataFrame indexing operation - use parent class
        if isinstance(key, (list, slice)):
            return super().__getitem__(key)

        # String key without chain - this shouldn't happen on ChainableDataFrame
        # but handle it as DataFrame indexing for safety
        return super().__getitem__(key)

    def __arrow_c_array__(self, requested_schema=None):
        return Wkl(self._chain).__arrow_c_array__(requested_schema=requested_schema)

    @property
    def _constructor(self) -> type[ChainableDataFrame]:
        """Return the constructor for DataFrame operations.

        Returns:
            ChainableDataFrame class.
        """
        return ChainableDataFrame

    def __repr__(self) -> str:
        """Return string representation with hint for empty results.

        Returns:
            String representation of the DataFrame, with a hint if empty.
        """
        base_repr = self._df.__repr__()
        # Check for empty results by examining the repr output (avoids extra count() query)
        # SedonaDB empty DataFrames show header row followed immediately by footer
        # Pattern: ╞══...══╡ (separator) followed by └──...──┘ (footer) with no data rows
        is_empty = False
        if self._chain:
            lines = base_repr.strip().split("\n")
            # Empty table has separator line (╞) immediately followed by footer (└)
            for i, line in enumerate(lines[:-1]):
                if line.startswith("╞") and lines[i + 1].startswith("└"):
                    is_empty = True
                    break

        if is_empty:
            # Get suggestions using Wkl's method
            wkl = Wkl(self._chain)
            suggestions = wkl._get_suggestions(self._chain[-1])
            hint = _build_error_hint(self._chain, suggestions) + "\n"
            return hint + base_repr
        return base_repr


class Wkl:
    """Well-Known Location resolver for administrative boundaries.

    This class handles the resolution of chained location attributes to
    database queries and geometry retrieval from Overture Maps data.

    The chain supports up to 3 levels:
        1. Country/Dependency (ISO 3166-1 alpha-2 code)
        2. Region (region code suffix)
        3. Place (county, locality, or neighborhood name)

    Example:
        >>> wkl = Wkl(['us', 'ca', 'sanfrancisco'])
        >>> wkl.wkt()
        'MULTIPOLYGON (((-122.5279985 37.8155806...)))'

    Attributes:
        chain: List of location identifiers in the chain.
    """

    _has_region: bool = True

    def __init__(self, chain: list[str] | None = None) -> None:
        """Initialize a Wkl instance.

        Args:
            chain: List of location identifiers (e.g., ['us', 'ca']).
        """
        if chain and len(chain) >= 1:
            country_iso = chain[0].upper()
            # Check cache first, query only if not cached
            if country_iso not in _country_has_region_cache:
                df_check = sedona.sql(
                    queries.COUNTRY_HAS_REGIONS.format(country=sqlescape(country_iso))
                )
                # Country has regions if there are any subtype='region' entries
                _country_has_region_cache[country_iso] = df_check.count() > 0
            self._has_region = _country_has_region_cache[country_iso]
        self.chain: list[str] = chain or []

    def overture_version(self) -> str:
        """Return the version of the Overture Maps dataset being used.

        This method is only available at the root level (wkls.overture_version()),
        not on chained objects.

        Returns:
            Version string of the Overture Maps dataset.

        Raises:
            ValueError: If called on a chained object.
        """
        if self.chain:
            raise ValueError(
                "overture_version() is only available at the root level. Use wkls.overture_version(), not wkls.us.overture_version()."
            )
        return _current_overture_version

    def overture_releases(self) -> list[str]:
        """List all available Overture Maps releases on S3.

        This method is only available at the root level
        (``wkls.overture_releases()``), not on chained objects.

        Returns:
            Sorted list of available version strings.

        Raises:
            ValueError: If called on a chained object.
            ConnectionError: If S3 listing fails.
        """
        if self.chain:
            raise ValueError(
                "overture_releases() is only available at the root level. "
                "Use wkls.overture_releases(), not wkls.us.overture_releases()."
            )
        return _list_s3_releases()

    def configure(self, overture_version: str) -> None:
        """Configure the Overture Maps dataset version.

        Validates the requested version against available S3 releases,
        then re-creates the ``overture`` SedonaDB view pointing to the
        new version's GeoParquet data.

        This method is only available at the root level
        (``wkls.configure(overture_version="...")``).

        Args:
            overture_version: Version string to use (e.g., ``"2025-12-17.0"``).

        Raises:
            ValueError: If called on a chained object or version is unavailable.
            ConnectionError: If S3 listing fails.

        Example:
            >>> import wkls
            >>> wkls.overture_releases()
            ['2025-12-17.0', '2026-01-21.0']
            >>> wkls.configure(overture_version="2025-12-17.0")
            >>> wkls.overture_version()
            '2025-12-17.0'
        """
        global _current_overture_version

        if self.chain:
            raise ValueError(
                "configure() is only available at the root level. "
                "Use wkls.configure(), not wkls.us.configure()."
            )

        available = _list_s3_releases()
        if overture_version not in available:
            raise ValueError(
                f"Overture Maps version '{overture_version}' is not available on S3.\n"
                f"Available versions: {', '.join(available)}\n"
                "Use wkls.overture_releases() to list all available versions."
            )

        _current_overture_version = overture_version
        sedona.read_parquet(
            _overture_uri(overture_version),
            options={
                "aws.skip_signature": True,
                "aws.region": "us-west-2",
            },
        ).to_view("overture", overwrite=True)

    def __getattr__(self, attr: str) -> ChainableDataFrame | Wkl:
        """Handle attribute access for location chaining.

        Args:
            attr: Attribute name representing a location identifier.

        Returns:
            ChainableDataFrame if chain is complete, otherwise Wkl.

        Raises:
            AttributeError: For private/dunder attributes.
            ValueError: If chain exceeds maximum depth of 3.
        """
        # Don't intercept private/dunder attributes - raise AttributeError
        if attr.startswith("_"):
            raise AttributeError(
                f"'{self.__class__.__name__}' object has no attribute '{attr}'"
            )

        new_wkl = Wkl(self.chain + [attr.lower()])
        # Validate chain length immediately
        if len(new_wkl.chain) > 3:
            raise ValueError("Too many chained attributes (max = 3)")

        if len(new_wkl.chain) <= 3:
            df = new_wkl.resolve()
            return ChainableDataFrame(df, new_wkl.chain)
        return new_wkl

    def __getitem__(
        self, key: str
    ) -> ChainableDataFrame | sedonadb.dataframe.DataFrame:
        """Handle bracket access for location chaining.

        Supports search patterns with % wildcards for fuzzy matching.

        Args:
            key: Location identifier or search pattern.

        Returns:
            ChainableDataFrame or DataFrame for search patterns.

        Raises:
            ValueError: If chain exceeds maximum depth of 3.
        """
        new_wkl = Wkl(self.chain + [key.lower()])
        # Validate chain length immediately
        if len(new_wkl.chain) > 3 and "%" not in key:
            raise ValueError("Too many chained attributes (max = 3)")
        # If this looks like a search pattern (contains %), return DataFrame directly
        if "%" in key:
            return new_wkl.resolve()
        # Return ChainableDataFrame to get hint logic in __repr__
        df = new_wkl.resolve()
        return ChainableDataFrame(df, new_wkl.chain)

    def __repr__(self) -> str:
        """Return string representation of the resolved DataFrame.

        Returns:
            String representation of the underlying data.
        """
        return repr(self.resolve())

    def resolve(self) -> sedonadb.dataframe.DataFrame:
        """Resolve the location chain to a DataFrame.

        Executes the appropriate SQL query based on the chain depth
        to retrieve matching location records.

        Returns:
            DataFrame containing matching location records.

        Raises:
            ValueError: If the chain is empty.
        """
        if not self.chain:
            raise ValueError(
                "No attributes in the chain. Use wkls.<country> or wkls.<country>.<region>, etc."
            )

        params: dict[str, str] = {}
        country_iso = self.chain[0].upper()
        query = queries.COUNTRY_DEPENDENCY
        params["country"] = country_iso

        if len(self.chain) > 1:
            if self._has_region:
                query = queries.REGION
                region_iso = country_iso + "-" + self.chain[1].upper()
                params["region"] = region_iso
            else:
                query = queries.CITY_NO_REGION
                city = self.chain[1].lower()
                params["city"] = city

        if len(self.chain) > 2:
            query = queries.CITY
            region_iso = country_iso + "-" + self.chain[1].upper()
            city = self.chain[2]
            params["region"] = region_iso
            params["city"] = city

        return sedona.sql(query.format(**{k: sqlescape(v) for k, v in params.items()}))

    def _get_suggestions(
        self, failed_name: str, n: int = 5, max_distance: int = 15
    ) -> list[str]:
        """Get similar location names for "did you mean" suggestions.

        For country/region codes (2-char ISO codes), uses simple prefix matching.
        For city names, uses Levenshtein distance for fuzzy matching.

        Args:
            failed_name: The name that wasn't found.
            n: Maximum number of suggestions to return.
            max_distance: Maximum Levenshtein score for city-level (default 15).

        Returns:
            List of chainable location names (lowercase, no spaces/special chars),
            or empty list if none found.
        """
        if len(self.chain) == 0:
            return []

        # Normalize search term to match how chainable names are stored
        search_term = re.sub(r"[^a-zA-Z0-9]", "", failed_name.lower())

        # Determine which query to use based on chain level
        use_distance_filter = False

        if len(self.chain) == 1:
            # Country-level suggestions (prefix match on ISO codes)
            query = queries.SUGGEST_COUNTRY.format(
                search_term=sqlescape(search_term),
                limit=n,
            )
        elif len(self.chain) == 2:
            country_iso = self.chain[0].upper()
            if self._has_region:
                # Region-level suggestions (prefix match on region codes)
                query = queries.SUGGEST_REGION.format(
                    country=sqlescape(country_iso),
                    search_term=sqlescape(search_term),
                    limit=n,
                )
            else:
                # City-level for countries without regions (e.g., wkls.fk.stoney)
                query = queries.SUGGEST_CITY.format(
                    country=sqlescape(country_iso),
                    region_filter="",
                    search_term=sqlescape(search_term),
                    limit=n * 2,
                )
                use_distance_filter = True
        else:
            # City-level with region (e.g., wkls.us.ca.sanfran)
            country_iso = self.chain[0].upper()
            region = country_iso + "-" + self.chain[1].upper()
            query = queries.SUGGEST_CITY.format(
                country=sqlescape(country_iso),
                region_filter=f"AND region = '{sqlescape(region)}'",
                search_term=sqlescape(search_term),
                limit=n * 2,
            )
            use_distance_filter = True

        result = sedona.sql(query)

        table = result.to_arrow_table()
        if table.num_rows == 0:
            return []

        # For city-level queries, filter by max_distance
        if use_distance_filter:
            distances = table.column("distance")
            names = table.column("chainable_name")
            return [
                names[i].as_py()
                for i in range(table.num_rows)
                if distances[i].as_py() <= max_distance
            ][:n]

        # For country/region prefix matches, just return the results
        return [
            table.column("chainable_name")[i].as_py()
            for i in range(min(table.num_rows, n))
        ]

    def _get_geom_expr(self, expr: str) -> Any:
        """Retrieve geometry using a SQL expression.

        Resolves the location chain against the local metadata table, then
        queries the remote Overture GeoParquet using attribute-based filters
        that leverage Parquet predicate pushdown on low-cardinality columns
        (country, subtype, region, is_land) for fast row group pruning.

        For country/region/dependency subtypes, the combination of
        country + region + subtype is unique, so no further disambiguation
        is needed.

        For city/county/localadmin subtypes, the final disambiguation uses
        ``(id = '<gers_id>' OR names.primary = '<name>')``. This makes the
        lookup resilient to either identifier changing across Overture
        releases: if GERS IDs stabilize (as OMF intends), the ID match is
        the fast path; if the ID drifts, the name still resolves correctly.

        Args:
            expr: SQL expression to apply to the geometry column.

        Returns:
            Result of the geometry expression (type depends on expression).

        Raises:
            ValueError: If no results found or no geometry exists.
        """
        df = self.resolve()
        if df.count() == 0:
            suggestions = self._get_suggestions(self.chain[-1])
            hint = _build_error_hint(self.chain, suggestions)
            raise ValueError(hint.strip())

        row = df.head(1).to_arrow_table()
        gers_id = row.column("id")[0].as_py()
        country = row.column("country")[0].as_py()
        region = row.column("region")[0].as_py()
        subtype = row.column("subtype")[0].as_py()
        name_primary = row.column("name_primary")[0].as_py()

        # Build WHERE clause from resolved attributes.
        # Country/region/dependency are unique by country+region+subtype.
        # City/county/localadmin use (id OR name) for resilient disambiguation.
        conditions = [
            f"country = '{sqlescape(country)}'",
            f"subtype = '{sqlescape(subtype)}'",
            "is_land = true",
        ]
        if region:
            conditions.append(f"region = '{sqlescape(region)}'")
        if subtype in ("county", "locality", "localadmin"):
            conditions.append(
                f"(id = '{sqlescape(gers_id)}' OR names.primary = '{sqlescape(name_primary)}')"
            )

        where_clause = " AND ".join(conditions)
        query = f"SELECT {expr} FROM overture WHERE {where_clause} LIMIT 1"

        result_df = sedona.sql(query)
        if result_df.count() == 0:
            chain_str = ".".join(self.chain)
            raise ValueError(
                f"No geometry found for: {chain_str} "
                f"(country={country}, region={region}, subtype={subtype}, "
                f"id={gers_id}, name={name_primary})"
            )
        return result_df.head(1).to_arrow_table().column(0)[0].as_py()

    def wkt(self) -> str:
        """Get Well-Known Text (WKT) geometry for the first result.

        Returns:
            WKT string representation of the geometry.

        Raises:
            ValueError: If no results found for the location chain.
        """
        return self._get_geom_expr("ST_AsText(geometry)")

    def wkb(self) -> bytes:
        """Get Well-Known Binary (WKB) geometry for the first result.

        Returns:
            Binary WKB representation of the geometry.

        Raises:
            ValueError: If no results found for the location chain.
        """
        return self._get_geom_expr("ST_AsWKB(geometry)")

    def hexwkb(self) -> str:
        """Get hex-encoded WKB geometry for the first result.

        Returns:
            Hex-encoded WKB string.

        Raises:
            NotImplementedError: This format is not yet supported in SedonaDB.
        """
        # return self._get_geom_expr("ST_AsHEXWKB(geometry)")
        raise NotImplementedError("ST_AsHEXWKB() isn't implemented yet")

    def geojson(self) -> str:
        """Get GeoJSON geometry for the first result.

        Returns:
            GeoJSON string representation.

        Raises:
            NotImplementedError: This format is not yet supported in SedonaDB.
        """
        # return self._get_geom_expr("ST_AsGeoJSON(geometry)")
        raise NotImplementedError("ST_AsGeoJSON() isn't implemented yet")

    def svg(self, relative: bool = False, precision: int = 15) -> str:
        """Get SVG path geometry for the first result.

        Args:
            relative: Use relative coordinates if True.
            precision: Decimal precision for coordinates.

        Returns:
            SVG path string.

        Raises:
            NotImplementedError: This format is not yet supported in SedonaDB.
        """
        # return self._get_geom_expr(
        #     f"ST_AsSVG(geometry, {str(relative).lower()}, {precision})"
        # )
        raise NotImplementedError("ST_AsSVG() isn't implemented yet")

    def __arrow_c_array__(self, requested_schema=None):
        """Implement the Arrow PyCapsule protocol

        Resolves this Wkl as a GeoArrow array of length one with the
        appropriate extension type such that this object is recognized
        as geometry with the appropriate CRS when interacting with
        arrow-based APIs (e.g., pyarrow.array() or sedonadb.sql()
        parameters.
        """
        import geoarrow.pyarrow as ga

        wkb_bytes = self.wkb()
        pyarrow_wkb_array = ga.with_crs([wkb_bytes], crs=ga.OGC_CRS84)
        return pyarrow_wkb_array.__arrow_c_array__(requested_schema=requested_schema)

    def dependencies(self) -> sedonadb.dataframe.DataFrame:
        """Get all dependencies (territories, overseas regions, etc.).

        Returns:
            DataFrame containing all dependency records with id, country,
            subtype, name_primary, and name_en columns.

        Raises:
            ValueError: If called on a chained object instead of root.
        """
        if self.chain:
            raise ValueError(
                "dependencies() can only be called on the root object. Use wkls.dependencies() instead of chaining."
            )

        query = """
            SELECT DISTINCT id, country, subtype, name_primary, name_en
            FROM wkls
            WHERE subtype = 'dependency'
        """
        return sedona.sql(query)

    def countries(self) -> sedonadb.dataframe.DataFrame:
        """Get all countries.

        Returns:
            DataFrame containing all country records with id, country,
            subtype, name_primary, and name_en columns.

        Raises:
            ValueError: If called on a chained object instead of root.
        """
        if self.chain:
            raise ValueError(
                "countries() can only be called on the root object. Use wkls.countries() instead of chaining."
            )

        query = """
            SELECT DISTINCT id, country, subtype, name_primary, name_en
            FROM wkls
            WHERE subtype = 'country'
        """
        return sedona.sql(query)

    def regions(self) -> sedonadb.dataframe.DataFrame:
        """Get regions for the current country.

        Must be called on a single-level chain (e.g., `wkls.us.regions()`).

        Returns:
            DataFrame containing all region records for the country.

        Raises:
            ValueError: If called at wrong chain level or country has no regions.
        """
        if not self.chain or len(self.chain) > 1:
            raise ValueError(
                "regions() requires exactly one level of chaining. "
                "Use wkls.<country>.regions() to get regions for a country."
            )

        country_iso = self.chain[0].upper()

        if not self._has_region:
            raise ValueError(
                f"The country '{country_iso}' does not have regions in the dataset. "
                f"Please directly call wkls['{country_iso.lower()}'].counties() or "
                f"wkls['{country_iso.lower()}'].cities() to access its counties or cities."
            )

        query = """
            SELECT * FROM wkls
            WHERE country = '{country}'
                AND subtype = 'region'
        """
        return sedona.sql(query.format(country=sqlescape(country_iso)))

    def _get_subdivisions(
        self, subtype_filter: str, method_name: str
    ) -> sedonadb.dataframe.DataFrame:
        """Helper for counties() and cities() methods.

        Args:
            subtype_filter: SQL subtype filter (e.g., "'county'" or "('locality', 'localadmin')").
            method_name: Name of the calling method for error messages.

        Returns:
            DataFrame containing matching subdivision records.

        Raises:
            ValueError: If called at wrong chain level.
        """
        if not self.chain or len(self.chain) > 2:
            raise ValueError(
                f"{method_name}() requires exactly one or two levels of chaining. "
                f"Use wkls.<country>.<region>.{method_name}() to get {method_name} for a region."
            )

        country_iso = self.chain[0].upper()

        if len(self.chain) == 1:
            if self._has_region:
                raise ValueError(
                    f"{method_name}() cannot be called on a country alone. "
                    f"Use wkls.<country>.<region>.{method_name}() to get {method_name} for a region."
                )
            query = f"""
                SELECT * FROM wkls
                WHERE country = '{{country}}'
                  AND subtype IN {subtype_filter}
            """
            return sedona.sql(query.format(country=sqlescape(country_iso)))

        # len(self.chain) == 2
        region_iso = country_iso + "-" + self.chain[1].upper()
        query = f"""
            SELECT * FROM wkls
            WHERE country = '{{country}}'
              AND region = '{{region}}'
              AND subtype IN {subtype_filter}
        """
        return sedona.sql(
            query.format(country=sqlescape(country_iso), region=sqlescape(region_iso))
        )

    def counties(self) -> sedonadb.dataframe.DataFrame:
        """Get counties for the current region.

        Must be called on a two-level chain (e.g., `wkls.us.ca.counties()`),
        or single-level for countries without regions.

        Returns:
            DataFrame containing all county records for the region.

        Raises:
            ValueError: If called at wrong chain level.
        """
        return self._get_subdivisions("('county')", "counties")

    def cities(self) -> sedonadb.dataframe.DataFrame:
        """Get cities for the current region.

        Must be called on a two-level chain (e.g., `wkls.us.ca.cities()`),
        or single-level for countries without regions.

        Returns:
            DataFrame containing all city records for the region.

        Raises:
            ValueError: If called at wrong chain level.
        """
        return self._get_subdivisions("('locality', 'localadmin')", "cities")

    def subtypes(self) -> sedonadb.dataframe.DataFrame:
        """Get all distinct division subtypes in the dataset.

        Returns:
            DataFrame containing all unique subtype values.

        Raises:
            ValueError: If called on a chained object instead of root.
        """
        if self.chain:
            raise ValueError(
                "subtypes() can only be called on the root object. Use wkls.subtypes() instead of chaining."
            )

        query = """SELECT DISTINCT subtype FROM wkls"""
        return sedona.sql(query)
