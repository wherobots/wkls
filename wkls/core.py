"""wkls — Well-Known Locations.

A Python library for accessing global administrative boundaries using
chainable syntax. Fetches geometries from Overture Maps Foundation
GeoParquet data via Apache SedonaDB.

Example usage:
    >>> import wkls
    >>> wkls.us.ca.sanfrancisco.wkt()
    'MULTIPOLYGON (((-122.5279985 37.8155806...)))'

    >>> from wkls import Wkl
    >>> wkl = Wkl()
    >>> wkl.us.ca.sanfrancisco.wkt()

    >>> wkls.countries()       # List all countries
    >>> wkls.us.regions()      # List US states/regions
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

# Cache for country identifier -> (canonical ISO, has_region).
# Keyed by the lowercased raw identifier (ISO or name); populated once per
# country-shaped access, shared across all Wkl instantiations in the
# process. Static per Overture version, safe to cache indefinitely.
_country_info: dict[str, tuple[str, bool]] = {}

# Cache for (country_iso, region_identifier) -> canonical region ISO.
# The identifier key is lowercased raw input (ISO suffix like "mh",
# full region ISO like "in-mh", or name like "maharashtra").
# Canonical value is the full region ISO ("IN-MH").
_region_info: dict[tuple[str, str], str] = {}

# Cache for __dir__ results, keyed by chain tuple.
# ()  -> root-level country identifiers
# ("US",) -> US region identifiers
# Values are sorted lists of ISO codes + normalized names.
_dir_cache: dict[tuple[str, ...], list[str]] = {}


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
        "search",
    }
)

# Methods surfaced by __dir__ at each chain depth.
_DIR_ROOT_METHODS = frozenset(
    {
        "Wkl",
        "ChainableDataFrame",
        "configure",
        "countries",
        "dependencies",
        "overture_releases",
        "overture_version",
        "search",
        "subtypes",
    }
)
_DIR_COUNTRY_METHODS = frozenset(
    {
        "cities",
        "counties",
        "geojson",
        "regions",
        "search",
        "wkb",
        "wkt",
    }
)
_DIR_REGION_METHODS = frozenset(
    {
        "cities",
        "counties",
        "geojson",
        "search",
        "wkb",
        "wkt",
    }
)
_DIR_CITY_METHODS = frozenset({"geojson", "wkb", "wkt"})


def _build_error_hint(chain: list[str], suggestions: list[str]) -> str:
    """Build error hint message with suggestions and a search() tip."""
    chain_str = ".".join(chain)
    failed_name = chain[-1]
    chain_prefix = ".".join(chain[:-1])

    if chain_prefix:
        search_example = f"wkls.{chain_prefix}.search('{failed_name}')"
    else:
        search_example = f"wkls.search('{failed_name}')"

    if suggestions:
        suggestion_hint = f"Did you mean: {', '.join(suggestions)}?\n"
    else:
        suggestion_hint = ""

    return (
        f"No results found for: {chain_str}\n"
        f"{suggestion_hint}"
        f"Tip: Use {search_example} to search by name.\n"
    )


class ChainableDataFrame:
    """A location-aware wrapper around sedonadb.dataframe.DataFrame.

    Returned by dot-access chaining (e.g., ``wkls.us.ca`` or ``wkl.us.ca``).
    Supports:

    - Continued chaining: ``wkls.us.ca.sanfrancisco``
    - Geometry access: ``.wkt()``, ``.wkb()``, ``.geojson()``
    - Listing: ``.regions()``, ``.cities()``, ``.counties()``

    The underlying DataFrame is accessible via ``._df``:

        >>> wkls.us.ca._df.to_arrow_table()  # pyarrow.Table

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
        """[Deprecated] Bracket access for location chaining or DataFrame indexing.

        See :meth:`Wkl.__getitem__` for migration guidance. DataFrame-style
        indexing (list or slice keys) is not deprecated and does not warn.
        """
        import warnings

        if isinstance(key, str):
            if "%" in key:
                cleaned = key.strip("%")
                warnings.warn(
                    "Bracket access with wildcards is deprecated; "
                    f"use .search({cleaned!r}) instead.",
                    DeprecationWarning,
                    stacklevel=2,
                )
            else:
                chain_prefix = ".".join(self._chain) + "." if self._chain else ""
                warnings.warn(
                    "Bracket access is deprecated; "
                    f"use dot access (wkls.{chain_prefix}{key.lower()}) or the "
                    "corresponding name form.",
                    DeprecationWarning,
                    stacklevel=2,
                )

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

    def __dir__(self) -> list[str]:
        """Delegate to Wkl.__dir__ for chain-aware attribute listing."""
        return Wkl(self._chain).__dir__()

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
    """Well-Known Locations — access global administrative boundaries.

    Provides chainable access to Overture Maps administrative boundary
    geometries via Apache SedonaDB. Chain country → region → place using
    dot notation with names or ISO codes.

    Quick Start (two ways to use):

        >>> import wkls                               # ergonomic
        >>> wkls.us.ca.sanfrancisco.wkt()
        >>> wkls.india.maharashtra.wkt()

        >>> from wkls import Wkl                      # explicit
        >>> wkl = Wkl()
        >>> wkl.us.ca.sanfrancisco.wkt()
        >>> wkl.us.california.sanfrancisco.wkt()      # full name works too

    Chaining (3 levels max):

        Level 1 — Country:  ``wkls.us``  OR  ``wkls.unitedstates``
        Level 2 — Region:   ``wkls.us.ca``  OR  ``wkls.us.california``
        Level 3 — Place:    ``wkls.us.ca.sanfrancisco``

    Name Rules:
        Names are lowercase, spaces removed:

        - "San Francisco" → ``sanfrancisco``
        - "New York"      → ``newyork``
        - "United States" → ``unitedstates``
        - ISO codes also work: ``us``, ``ca``, ``gb``, ``de``, ``jp``…

        Names that contain diacritics or punctuation in ``name_en``
        (e.g., Côte d'Ivoire, São Paulo) are not Python-typable — fall
        back to the ISO code: ``wkls.ci``, ``wkls.br.sp``.

    Geometry Formats:
        - ``.wkt()``     → str   (Well-Known Text)
        - ``.wkb()``     → bytes (Well-Known Binary)
        - ``.geojson()`` → str   (GeoJSON)

    Discovery:
        - ``wkls.countries()``         DataFrame of all 219 countries
        - ``wkls.us.regions()``        DataFrame of US regions
        - ``wkls.us.ca.cities()``      DataFrame of CA cities

    Configuration:
        - ``wkls.overture_version()``  Current Overture version
        - ``wkls.overture_releases()`` All available versions
        - ``wkls.configure(overture_version="2025-12-17.0")``

    Environment Variables:
        - ``WKLS_DEBUG=true``  Print all SQL queries to stdout

    Arrow Interop:
        >>> import pyarrow as pa
        >>> pa.array(wkls.us.ca.sanfrancisco)  # geoarrow.wkb array

    Attributes:
        chain: List of location identifiers in the chain.
    """

    _has_region: bool = True

    def __init__(self, chain: list[str] | None = None) -> None:
        """Initialize a Wkl instance.

        Args:
            chain: List of location identifiers. Accepts ISO codes
                (``['us', 'ca']``) or human-readable names
                (``['unitedstates', 'california']``). Empty for the
                root instance.
        """
        self.chain: list[str] = chain or []
        self._country_iso: str = ""
        if not self.chain:
            return

        key = self.chain[0].lower()
        if key not in _country_info:
            iso, has_region = self._lookup_country(key)
            _country_info[key] = (iso, has_region)
            # Alias the ISO form so `wkls.us` and `wkls.unitedstates` share
            # the same cached entry on whichever access comes second.
            _country_info[iso.lower()] = (iso, has_region)

        self._country_iso, self._has_region = _country_info[key]

    def _lookup_country(self, identifier: str) -> tuple[str, bool]:
        """Resolve any country identifier to (canonical ISO, has_region).

        Accepts ISO codes (``'us'``, ``'US'``) and names
        (``'unitedstates'``, ``'United States'``). If the identifier
        matches no known country, returns ``(identifier.upper(), False)``
        so downstream ``resolve()`` produces an empty DataFrame with the
        standard "Did you mean?" hint.

        Args:
            identifier: User-supplied country identifier, any case.

        Returns:
            Tuple of (canonical ISO, has_region). For unknown inputs,
            the uppercased original string and ``False``.
        """
        df = sedona.sql(queries.COUNTRY_LOOKUP.format(identifier=sqlescape(identifier)))
        table = df.to_arrow_table()
        if table.num_rows == 0:
            return identifier.upper(), False
        iso = table.column("iso")[0].as_py()
        df_has = sedona.sql(queries.COUNTRY_HAS_REGIONS.format(country=sqlescape(iso)))
        return iso, df_has.count() > 0

    @property
    def _region_iso(self) -> str:
        """Resolve ``self.chain[1]`` to its canonical region ISO (e.g. ``'IN-MH'``).

        Lazy + cached. Handles both ISO suffixes (``'mh'`` or ``'IN-MH'``)
        and region names (``'maharashtra'``). Returns an upper-cased naive
        form (``'IN-MAHARASHTRA'``) if no match is found so downstream
        queries return empty DataFrames and trigger the "Did you mean?" path.
        """
        raw = self.chain[1]
        key = (self._country_iso, raw.lower())
        if key not in _region_info:
            _region_info[key] = self._lookup_region(raw)
        return _region_info[key]

    def _lookup_region(self, identifier: str) -> str:
        """Resolve a region identifier to its canonical ISO (e.g. 'IN-MH').

        Accepts bare suffix ('mh'), full ISO ('IN-MH'), or name ('maharashtra').
        """
        full_iso = (
            identifier
            if "-" in identifier
            else f"{self._country_iso}-{identifier.upper()}"
        )
        df = sedona.sql(
            queries.REGION_LOOKUP.format(
                country=sqlescape(self._country_iso),
                identifier=sqlescape(full_iso),
                name=sqlescape(identifier),
            )
        )
        table = df.to_arrow_table()
        if table.num_rows == 0:
            return full_iso.upper()
        return table.column("iso")[0].as_py()

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
        _country_info.clear()
        _region_info.clear()
        _dir_cache.clear()
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

    def __dir__(self) -> list[str]:
        """Return contextually valid attributes for the current chain level.

        Includes both ISO codes and normalized names — both forms work via
        ``__getattr__``, so both are advertised. Region-level and deeper
        return methods only (cities are too numerous to list).
        """
        depth = len(self.chain)
        if depth == 0:
            methods: frozenset[str] = _DIR_ROOT_METHODS
            locations = self._dir_countries()
        elif depth == 1:
            methods = _DIR_COUNTRY_METHODS
            locations = self._dir_regions()
        elif depth == 2:
            methods = _DIR_REGION_METHODS
            locations = []
        else:
            methods = _DIR_CITY_METHODS
            locations = []
        return sorted(set(methods) | set(locations))

    def _dir_countries(self) -> list[str]:
        """Return cached country-level dir entries (ISO codes + names)."""
        key: tuple[str, ...] = ()
        if key not in _dir_cache:
            _dir_cache[key] = self._collect_dir_entries(
                sedona.sql(queries.DIR_COUNTRIES)
            )
        return _dir_cache[key]

    def _dir_regions(self) -> list[str]:
        """Return cached region-level dir entries (ISO suffixes + names)."""
        key: tuple[str, ...] = (self._country_iso,)
        if key not in _dir_cache:
            df = sedona.sql(
                queries.DIR_REGIONS.format(country=sqlescape(self._country_iso))
            )
            _dir_cache[key] = self._collect_dir_entries(df)
        return _dir_cache[key]

    @staticmethod
    def _collect_dir_entries(df: sedonadb.dataframe.DataFrame) -> list[str]:
        """Collect ISO and name values from a dir() query result."""
        table = df.to_arrow_table()
        result: set[str] = set()
        for i in range(table.num_rows):
            iso = table.column("iso")[i].as_py()
            name = table.column("name")[i].as_py()
            if iso:
                result.add(iso)
            if name:
                result.add(name)
        return sorted(result)

    def __getitem__(
        self, key: str
    ) -> ChainableDataFrame | sedonadb.dataframe.DataFrame:
        """[Deprecated] Handle bracket access for location chaining.

        Emits a :class:`DeprecationWarning` pointing at the modern API:

        - For name-based access, use dot notation: ``wkls.india`` instead of
          ``wkls["IN"]``, ``wkls.us.oregon`` instead of ``wkls.us["OR"]``.
        - For wildcard search, use :meth:`search`: ``wkls.us.ca.search("fran")``
          instead of ``wkls.us.ca["%fran%"]``.

        The old behavior is preserved for backward compatibility and will be
        removed in a future major version.
        """
        import warnings

        if "%" in str(key):
            cleaned = str(key).strip("%")
            warnings.warn(
                "Bracket access with wildcards is deprecated; "
                f"use .search({cleaned!r}) instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        else:
            chain_prefix = ".".join(self.chain) + "." if self.chain else ""
            warnings.warn(
                "Bracket access is deprecated; "
                f"use dot access (wkls.{chain_prefix}{key.lower()}) or the "
                "corresponding name form.",
                DeprecationWarning,
                stacklevel=2,
            )

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
        country_iso = self._country_iso
        raw_country = self.chain[0]

        query = queries.COUNTRY_DEPENDENCY
        params["country"] = raw_country

        if len(self.chain) > 1:
            if self._has_region:
                query = queries.REGION
                params["country"] = country_iso
                region_iso = country_iso + "-" + self.chain[1].upper()
                params["region"] = region_iso
                params["region_name"] = self.chain[1]
            else:
                query = queries.CITY_NO_REGION
                params["country"] = country_iso
                params["city"] = self.chain[1].lower()

        if len(self.chain) > 2:
            query = queries.CITY
            params["country"] = country_iso
            params["region"] = self._region_iso
            params["city"] = self.chain[2]

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
            country_iso = self._country_iso
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
            country_iso = self._country_iso
            query = queries.SUGGEST_CITY.format(
                country=sqlescape(country_iso),
                region_filter=f"AND region = '{sqlescape(self._region_iso)}'",
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
            GeoJSON string representation of the geometry.

        Raises:
            ValueError: If no results found for the location chain.
        """
        return self._get_geom_expr("ST_AsGeoJSON(geometry)")

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
        """List regions in the current chain scope.

        Scope follows chain depth:
            - ``wkls.regions()``     — every region worldwide
            - ``wkls.us.regions()``  — every region in the US

        Returns:
            DataFrame of region rows.

        Raises:
            ValueError: If called past region level (no regions below regions).
        """
        return self._list_subtype("('region')", "regions")

    def _list_subtype(
        self, subtype_filter: str, method_name: str
    ) -> sedonadb.dataframe.DataFrame:
        """List rows of the given subtype within the current chain scope.

        Args:
            subtype_filter: SQL subtype filter, e.g. ``"('county')"`` or
                ``"('locality', 'localadmin')"``.
            method_name: Calling method name for error messages.

        Returns:
            DataFrame containing matching rows.

        Raises:
            ValueError: If called past region level (chain depth > 2).
        """
        depth = len(self.chain)
        if depth > 2:
            raise ValueError(
                f"{method_name}() cannot be called past region level "
                f"(chain has {depth} elements; max list depth is 2)."
            )

        if depth == 0:
            query = f"SELECT * FROM wkls WHERE subtype IN {subtype_filter}"
            return sedona.sql(query)

        if depth == 1 or not self._has_region:
            # Country-scoped: depth 1, or depth 2 on a no-region country
            # (which addresses a specific city, so scope collapses to country).
            query = f"""
                SELECT * FROM wkls
                WHERE country = '{{country}}'
                  AND subtype IN {subtype_filter}
            """
            return sedona.sql(query.format(country=sqlescape(self._country_iso)))

        # depth == 2 with regions: region-scoped
        query = f"""
            SELECT * FROM wkls
            WHERE country = '{{country}}'
              AND region = '{{region}}'
              AND subtype IN {subtype_filter}
        """
        return sedona.sql(
            query.format(
                country=sqlescape(self._country_iso),
                region=sqlescape(self._region_iso),
            )
        )

    def counties(self) -> sedonadb.dataframe.DataFrame:
        """List counties in the current chain scope.

        Scope follows chain depth:
            - ``wkls.counties()``         — every county worldwide
            - ``wkls.us.counties()``      — every county in the US
            - ``wkls.us.ca.counties()``   — every county in California

        Returns:
            DataFrame of county rows.

        Raises:
            ValueError: If called past region level.
        """
        return self._list_subtype("('county')", "counties")

    def cities(self) -> sedonadb.dataframe.DataFrame:
        """List cities (localities and localadmins) in the current chain scope.

        Scope follows chain depth:
            - ``wkls.cities()``         — every city worldwide
            - ``wkls.us.cities()``      — every city in the US
            - ``wkls.us.ca.cities()``   — every city in California

        Returns:
            DataFrame of city rows.

        Raises:
            ValueError: If called past region level.
        """
        return self._list_subtype("('locality', 'localadmin')", "cities")

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

    def search(self, query: str) -> sedonadb.dataframe.DataFrame:
        """Search for locations whose names contain a substring.

        Searches every row within the current chain's scope — countries,
        dependencies, regions, counties, and localities alike — and returns
        matches as a DataFrame. Rows carry a ``subtype`` column so callers
        can tell what they got back.

        The scope narrows with chain depth:

        - ``wkls.search(q)``        — full dataset
        - ``wkls.us.search(q)``     — everything under US
        - ``wkls.us.ca.search(q)``  — everything under California

        Args:
            query: Search string. Matched against ``name_primary`` and
                ``name_en`` with ``ILIKE '%query%'``.

        Returns:
            DataFrame with ``id, country, region, subtype, name_primary, name_en``.

        Raises:
            ValueError: If called past city level (chain depth > 2).

        Examples:
            >>> import wkls
            >>> wkls.search("san francisco")     # finds the city from root
            >>> wkls.us.search("los angeles")    # scoped to US
            >>> wkls.us.ca.search("san fran")    # scoped to California
        """
        depth = len(self.chain)
        if depth > 2:
            raise ValueError(
                "search() cannot be called past city level "
                f"(chain has {depth} elements; max searchable depth is 2)."
            )

        escaped_query = sqlescape(query)

        if depth == 0:
            sql = queries.SEARCH_ROOT.format(query=escaped_query)
        elif depth == 1 or not self._has_region:
            # Depth 1, or depth 2 on a country that doesn't use regions.
            sql = queries.SEARCH_COUNTRY.format(
                country=sqlescape(self._country_iso), query=escaped_query
            )
        else:  # depth == 2 with regions
            sql = queries.SEARCH_REGION.format(
                country=sqlescape(self._country_iso),
                region=sqlescape(self._region_iso),
                query=escaped_query,
            )
        return sedona.sql(sql)
