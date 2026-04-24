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

__all__ = ["AmbiguousLocationError", "Wkl"]


class AmbiguousLocationError(ValueError):
    """Raised when a geometry method is called on a ``Wkl`` holding >1 row.

    Subclass of ``ValueError`` so existing ``except ValueError:`` blocks
    keep catching it. The message lists every candidate with its subtype,
    parent name, and id, and points at the dot-based disambiguation paths:

    - subtype modifier: ``wkls.us.ca.mission.locality``
    - 4-level parent narrower: ``wkls.us.pa.adamscounty.franklin``
    - exact pick: ``wkls.by_id('<uuid>')``
    """


# S3 bucket URL for listing Overture Maps releases (HTTP avoids SSL cert
# issues on macOS system Python installs that lack certifi/root certs)
_S3_BUCKET_URL = "http://overturemaps-us-west-2.s3.amazonaws.com/"
_S3_RELEASE_PREFIX = "release/"
_S3_DIVISION_AREA_SUFFIX = "theme=divisions/type=division_area/"

# Module-level state for the active Overture version
_current_overture_version: str | None = None

# True once the remote GeoParquet has been registered as the
# ``overture`` SedonaDB view. Flipped by ``_ensure_overture_loaded``
# (lazy, on first geometry call) or by ``configure()``.
_overture_view_loaded: bool = False


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
    """Initialize the SedonaDB context and register the local metadata view.

    Creates the ``wkls`` SedonaDB view from the bundled local parquet —
    pure local I/O, no network. The remote ``overture`` view is
    registered lazily by ``_ensure_overture_loaded`` on first geometry
    call, so ``import wkls`` stays offline-safe.

    Returns:
        Configured SedonaContext instance.
    """
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

    return sedona


# Initialize the table when the module is imported
sedona = _initialize_table()


def _ensure_overture_loaded() -> None:
    """Register the remote Overture GeoParquet view on first geometry access.

    Resolves the active Overture version (via ``WKLS_OVERTURE_VERSION``,
    module-level cache, or an S3 listing), then registers the remote
    GeoParquet as the ``overture`` SedonaDB view. Idempotent — later
    calls short-circuit on ``_overture_view_loaded``. ``configure()``
    sets the flag too, so a user-driven reload keeps the fast path.

    Raises:
        ConnectionError: If the S3 listing or parquet read fails. The
            message points at the network requirement.
    """
    global _current_overture_version, _overture_view_loaded
    if _overture_view_loaded:
        return
    if _current_overture_version is None:
        _current_overture_version = _resolve_overture_version()
    sedona.read_parquet(
        _overture_uri(_current_overture_version),
        options={
            "aws.skip_signature": True,
            "aws.region": "us-west-2",
        },
    ).to_view("overture", overwrite=True)
    _overture_view_loaded = True


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

# Cache for row-by-id lookups. Keyed by Overture UUID; value is a dict of
# id, country, region, subtype, name_primary, name_en, parent_division_id.
# Populated by Wkl.by_id() and Wkl.parent.
_row_info: dict[str, dict[str, object]] = {}


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


# Methods surfaced by __dir__ at each chain depth.
_DIR_ROOT_METHODS = frozenset(
    {
        "Wkl",
        "by_id",
        "configure",
        "countries",
        "dependencies",
        "overture_releases",
        "overture_version",
        "search",
        "subtypes",
    }
)
# Chain-mode dir surfaces at each depth. ``path`` is available on every
# resolved single-row Wkl from depth 1 onward; ``parent`` is available
# from depth 2 onward (countries raise because they're at the top).
_DIR_COUNTRY_METHODS = frozenset(
    {
        "cities",
        "counties",
        "geojson",
        "path",
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
        "parent",
        "path",
        "search",
        "wkb",
        "wkt",
    }
)
_DIR_CITY_METHODS = frozenset({"geojson", "parent", "path", "wkb", "wkt"})

# Result-mode: DataFrame passthroughs that make sense to surface on a
# multi-row Wkl. Keep this to the common inspection verbs — sedona's
# DataFrame has a wider surface but listing everything would be noise.
_DIR_DATAFRAME_METHODS = frozenset(
    {"count", "head", "limit", "show", "to_arrow_table", "to_dicts"}
)


def _normalize_name(name: str | None) -> str:
    """Lowercase + strip non-alphanumerics. Matches ``__getattr__`` input form."""
    if not name:
        return ""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _chain_attr_for_row(row: dict[str, object]) -> str:
    """Return the canonical chain attribute for a bundle row.

    - country / dependency → lowercased ISO code (``us``, ``pr``)
    - region              → lowercased region suffix (``ca`` for ``US-CA``)
    - anything else       → normalized name_en / name_primary
    """
    subtype = row.get("subtype")
    country = row.get("country") or ""
    region = row.get("region") or ""
    if subtype in ("country", "dependency"):
        return country.lower()
    if subtype == "region":
        # region column is "US-CA"; return "ca"
        if "-" in region:
            return region.split("-", 1)[1].lower()
        return region.lower()
    # county / locality / localadmin: normalized name
    name = row.get("name_en") or row.get("name_primary") or ""
    return _normalize_name(str(name))


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


class Wkl:
    """Administrative boundaries via dot access.

    For full usage (chaining, disambiguation, listing, navigation,
    errors), see ``help(wkls)``.

    Example:
        >>> import wkls
        >>> wkls.us.ca.sanfrancisco.wkt()
    """

    _has_region: bool = True

    # Methods that only make sense on the root `Wkl`. Hidden from chained
    # and result-mode instances so `hasattr(wkls.us, "configure")` is False,
    # matching the pre-unification contract.
    _ROOT_ONLY_METHODS = frozenset(
        {
            "configure",
            "overture_releases",
            "overture_version",
        }
    )

    def __getattribute__(self, name: str) -> Any:
        if name in type(self)._ROOT_ONLY_METHODS:
            # `chain` and `_df` have to be fetched without re-triggering
            # this hook — use object.__getattribute__.
            chain = object.__getattribute__(self, "chain")
            df = object.__getattribute__(self, "_df")
            if chain or df is not None:
                raise AttributeError(
                    f"'{type(self).__name__}' object has no attribute '{name}'"
                )
        return object.__getattribute__(self, name)

    def __init__(
        self,
        chain: list[str] | None = None,
        _df: sedonadb.dataframe.DataFrame | None = None,
        _parent_id: str | None = None,
    ) -> None:
        """Initialize a Wkl instance.

        A single ``Wkl`` serves three roles, distinguished internally:

        - **Root**: empty chain, no cached DataFrame. The module-level
          singleton and any ``Wkl()`` constructed without arguments.
        - **Chain-mode**: non-empty chain, DataFrame cached lazily via
          ``resolve()``. Produced by dot-access (``wkls.us.ca``).
        - **Result-mode**: empty chain, DataFrame provided up front.
          Produced by listing/search methods (``.countries()``,
          ``.search(...)``).

        Args:
            chain: List of location identifiers. Accepts ISO codes
                (``['us', 'ca']``) or human-readable names
                (``['unitedstates', 'california']``). Empty for the
                root instance and for result-mode.
            _df: Internal. Pre-resolved DataFrame cached on this
                instance by listing/search methods. Private because
                users never supply it directly.
            _parent_id: Internal. At chain depth 4 (parent narrower)
                this is the resolved depth-3 row's ``id``; it lets
                ``resolve()`` filter by ``parent_id = ?``. Private
                because users never supply it directly.
        """
        self.chain: list[str] = chain or []
        self._df: sedonadb.dataframe.DataFrame | None = _df
        self._parent_id: str | None = _parent_id
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

    def _ambiguity_message(self, df: sedonadb.dataframe.DataFrame) -> str:
        """Format an ``AmbiguousLocationError`` message for a multi-row result.

        Shows copy-pasteable chains the caller can run next. Detects the
        actual ambiguity class (differ by subtype / differ by parent /
        differ only by id) and emits the narrowers that would resolve
        each specific case — rather than a generic 'try .locality or
        .county' hint that may not apply. Always lists ``by_id(...)``
        for every candidate as the guaranteed fallback.
        """
        where = "wkls." + ".".join(self.chain) if self.chain else "this result"
        table = df.to_arrow_table()
        n = table.num_rows
        sample = min(n, 10)

        candidates: list[dict[str, Any]] = []
        for i in range(sample):
            row = {col: table.column(col)[i].as_py() for col in table.column_names}
            attr = _chain_attr_for_row(row)
            name = row.get("name_en") or row.get("name_primary") or "?"
            parent_id = row.get("parent_id")
            parent_attr: str | None = None
            parent_name: str | None = None
            if parent_id:
                parent = self._fetch_row(str(parent_id))
                if parent:
                    parent_attr = _chain_attr_for_row(parent)
                    parent_name = (
                        parent.get("name_en") or parent.get("name_primary") or None
                    )
            candidates.append(
                {
                    "subtype": row.get("subtype") or "?",
                    "id": row.get("id"),
                    "attr": attr,
                    "name": name,
                    "parent_attr": parent_attr,
                    "parent_name": parent_name,
                }
            )

        # Dots only step through the admin hierarchy. The two cases where
        # a chain narrower can resolve the ambiguity:
        #   1. Candidates have different normalized names (e.g. York vs
        #      YorkCounty) — swap the last chain segment for the
        #      unambiguous form.
        #   2. Candidates share a normalized name but have different
        #      parents (e.g. 18 Franklins in PA counties) — use the
        #      4-level parent narrower.
        # Anything else (same name + same parent, or no chain context)
        # can only be resolved by picking a specific UUID.
        attrs_differ = len({c["attr"] for c in candidates}) > 1
        parents_differ = len({c["parent_attr"] for c in candidates}) > 1

        lines: list[str] = []
        chain_prefix = (
            "wkls." + ".".join(self.chain[:-1]) if len(self.chain) >= 1 else "wkls"
        )

        if self.chain and attrs_differ:
            lines.append(
                f"{n} matches for '{where}'. Use the unambiguous normalized name:"
            )
            lines.append("")
            for c in candidates:
                lines.append(
                    f"  {chain_prefix}.{c['attr']:<18}  # {c['name']} ({c['subtype']})"
                )
        elif self.chain and parents_differ:
            lines.append(
                f"{n} matches for '{where}'. Narrow by parent (4-level chain):"
            )
            lines.append("")
            for c in candidates:
                parent_label = c["parent_name"] or c["parent_attr"] or "?"
                if c["parent_attr"]:
                    lines.append(
                        f"  {chain_prefix}.{c['parent_attr']}.{c['attr']}"
                        f"  # {c['name']} in {parent_label}"
                    )
        else:
            # Same attr + same parent (or no chain to narrow) — dot
            # paths can't distinguish these. by_id is the only way.
            lines.append(
                f"{n} matches for '{where}'. No dot-access narrower "
                "distinguishes these — use by_id:"
            )
            lines.append("")

        # Always show by_id lines with literal UUID + .wkt() call so agents
        # can copy-paste directly.
        lines.append("")
        lines.append("Or pick by id:")
        id_cap = 5
        for c in candidates[:id_cap]:
            parent_note = f", in {c['parent_name']}" if c["parent_name"] else ""
            lines.append(
                f"  wkls.by_id('{c['id']}').wkt()"
                f"  # {c['name']} ({c['subtype']}{parent_note})"
            )
        if len(candidates) > id_cap:
            lines.append(f"  … and {len(candidates) - id_cap} more ids")

        if n > sample:
            lines.append(f"({n - sample} additional candidates truncated)")

        return "\n".join(lines)

    @classmethod
    def by_id(cls, row_id: str) -> Wkl:
        """Resolve a single row by its Overture UUID.

        The escape hatch for the rare case dot access can't disambiguate
        (e.g. 18 Franklin townships in PA that share country + region +
        subtype + name). Get the UUID from ``.search()`` or from an
        ``AmbiguousLocationError`` message, then pick the specific row.

        Args:
            row_id: Overture UUID from the ``id`` column of any resolved
                row or search result.

        Returns:
            A result-mode ``Wkl`` wrapping exactly that row.

        Raises:
            ValueError: If no row with that id exists in the bundle.

        Examples:
            >>> wkls.by_id('273bc9a0-96a1-402c-992c-84f5c2f212cb').wkt()
            'POLYGON (((...)))'
        """
        df = sedona.sql(queries.ROW_BY_ID.format(row_id=sqlescape(row_id)))
        if df.count() == 0:
            raise ValueError(f"No row found with id={row_id!r}.")
        return cls(_df=df)

    @property
    def path(self) -> str:
        """Canonical dot-access path that resolves back to this ``Wkl``.

        Bridges the two ways to reach a location:

        - dot chain (``wkls.us.ca.sanfrancisco``)
        - discovery + pick (``wkls.us.search("san bruno").by_id(uid)``)

        Both produce a ``Wkl`` for the same row. ``.path`` returns the
        short dot-access form so callers can cache, log, or round-trip
        the location in code.

        Behavior:

        - Chain-mode: returns ``"wkls." + ".".join(self.chain)``.
        - Result-mode with exactly one row: walks ``parent_id`` up and
          emits the canonical chain (``wkls.<country>.<region>.<place>``),
          using the same lowercase, space-stripped normalization that
          ``__getattr__`` expects.
        - Root ``Wkl``: returns ``"wkls"``.

        Raises:
            ValueError: On multi-row result (no single path exists).

        Examples:
            >>> wkls.us.ca.sanfrancisco.path
            'wkls.us.ca.sanfrancisco'
            >>> wkls.us.search("san bruno").path
            'wkls.us.ca.sanbruno'
        """
        # Chain-mode: we already have the chain.
        if self.chain:
            return "wkls" + "".join(f".{part}" for part in self.chain)

        # Root (no chain, no df): bare path.
        if self._df is None:
            return "wkls"

        # Result-mode: must be a single row to have a single canonical path.
        row_count = self.resolve().count()
        if row_count != 1:
            raise ValueError(
                f".path requires a single-row Wkl; this one has {row_count} rows."
            )

        # Walk up parent_id collecting chain attributes. Names are
        # normalized to lowercase + no whitespace, matching __getattr__.
        table = self.resolve().head(1).to_arrow_table()
        row = {col: table.column(col)[0].as_py() for col in table.column_names}
        parts: list[str] = []
        # At most 4 hops (country → region → city). Defensive cap in case
        # of a malformed parent chain.
        for _ in range(5):
            parts.append(_chain_attr_for_row(row))
            parent_id = row.get("parent_id")
            if not parent_id:
                break
            parent = self._fetch_row(parent_id)
            if not parent:
                break
            row = parent
        parts.reverse()
        return "wkls" + "".join(f".{p}" for p in parts)

    @property
    def parent(self) -> Wkl:
        """Walk up the admin hierarchy by one level.

        Looks up the row's ``parent_id`` and returns the parent as a
        single-row result-mode ``Wkl``. Dots go down the tree,
        ``.parent`` goes up.

        Raises:
            ValueError: On multi-row results, or rows that have no
                parent (e.g. countries at the top of the tree).

        Examples:
            >>> wkls.us.ca.sanfrancisco.parent          # California
            >>> wkls.us.ca.sanfrancisco.parent.parent   # United States
        """
        # NOTE: must not raise AttributeError from a @property — Python's
        # attribute lookup protocol treats that as "attribute doesn't exist"
        # and falls through to __getattr__, which would drill "parent" as
        # a location name and return an empty Wkl. Use ValueError.
        df = self.resolve()
        row_count = df.count()
        if row_count != 1:
            raise ValueError(
                f".parent requires a single-row Wkl; this one has {row_count} rows."
            )
        table = df.head(1).to_arrow_table()
        if "parent_id" not in table.column_names:
            raise ValueError(
                ".parent requires the bundled metadata to include 'parent_id'."
            )
        parent_id = table.column("parent_id")[0].as_py()
        if not parent_id:
            raise ValueError(
                "This row has no parent (likely a country at the top of the hierarchy)."
            )
        return type(self).by_id(parent_id)

    @staticmethod
    def _fetch_row(row_id: str) -> dict[str, object] | None:
        """Look up a single row by its Overture UUID.

        Populates the module-level ``_row_info`` cache. Returns ``None``
        if no row exists with that id (e.g., parent_division_id pointing
        at a row outside our bundled subtypes).
        """
        if row_id in _row_info:
            return _row_info[row_id]
        df = sedona.sql(queries.ROW_BY_ID.format(row_id=sqlescape(row_id)))
        table = df.to_arrow_table()
        if table.num_rows == 0:
            return None
        row = {col: table.column(col)[0].as_py() for col in table.column_names}
        _row_info[row_id] = row
        return row

    def overture_version(self) -> str:
        """Return the version of the Overture Maps dataset being used.

        This method is only available at the root level (wkls.overture_version()),
        not on chained objects. Resolves the version lazily on first
        call — this is an S3 listing request, so it requires network
        access (but is cheap compared to loading the parquet itself).

        Returns:
            Version string of the Overture Maps dataset.

        Raises:
            ValueError: If called on a chained object.
            ConnectionError: If the version hasn't been resolved yet
                and the S3 listing fails.
        """
        global _current_overture_version
        if self.chain:
            raise ValueError(
                "overture_version() is only available at the root level. Use wkls.overture_version(), not wkls.us.overture_version()."
            )
        if _current_overture_version is None:
            _current_overture_version = _resolve_overture_version()
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
        global _current_overture_version, _overture_view_loaded

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
        _row_info.clear()
        sedona.read_parquet(
            _overture_uri(overture_version),
            options={
                "aws.skip_signature": True,
                "aws.region": "us-west-2",
            },
        ).to_view("overture", overwrite=True)
        _overture_view_loaded = True

    def __getattr__(self, attr: str) -> Any:
        """Handle attribute access.

        Three behaviors, picked by mode:

        - **Root / chain-mode**: drill one level deeper into the admin
          hierarchy (``wkls.us.ca.sanfrancisco``). Raises ``ValueError``
          past chain depth 3.
        - **Result-mode** (empty chain, cached DataFrame — e.g. after
          ``.search(...)`` or ``.countries()``): forward to the cached
          DataFrame so callers can do ``.count()``, ``.to_arrow_table()``,
          ``.head(n)``, etc. Location-name drill doesn't apply here —
          the result set has no tree position.

        Raises:
            AttributeError: For private/dunder attributes, or in
                result-mode when the attribute isn't found on the
                cached DataFrame.
            ValueError: If chain depth would exceed 3.
        """
        # Don't intercept private/dunder attributes - raise AttributeError
        if attr.startswith("_"):
            raise AttributeError(
                f"'{self.__class__.__name__}' object has no attribute '{attr}'"
            )

        # Root-only methods never drill as location names on chain/result-mode
        # instances. __getattribute__ already hides them; this guard prevents
        # __getattr__ from silently interpreting e.g. "configure" as a chain step.
        if attr in type(self)._ROOT_ONLY_METHODS:
            raise AttributeError(
                f"'{self.__class__.__name__}' object has no attribute '{attr}'"
            )

        # Result-mode: pass through to the cached DataFrame so standard
        # ops (count, to_arrow_table, head, show, …) keep working.
        if not self.chain and self._df is not None:
            if hasattr(self._df, attr):
                return getattr(self._df, attr)
            raise AttributeError(
                f"'{self.__class__.__name__}' object has no attribute '{attr}'"
            )

        new_chain = self.chain + [attr.lower()]
        if len(new_chain) > 4:
            raise ValueError(
                "Chain too deep (max = 4). Use .by_id('<uuid>') for specific rows."
            )

        # Depth 4: parent-narrower. The preceding 3-level chain must resolve
        # to exactly one row; we use that row's id to filter children.
        if len(new_chain) == 4:
            df = self._df if self._df is not None else self.resolve()
            count = df.count()
            if count != 1:
                raise ValueError(
                    "4-level chain requires the preceding chain to resolve to a "
                    f"single row; '{'.'.join(self.chain)}' has {count} rows."
                )
            parent_row = df.head(1).to_arrow_table()
            parent_id = parent_row.column("id")[0].as_py()
            new_wkl = Wkl(new_chain, _parent_id=parent_id)
        else:
            new_wkl = Wkl(new_chain)

        new_wkl._df = new_wkl.resolve()
        return new_wkl

    def __dir__(self) -> list[str]:
        """Return contextually valid attributes for this ``Wkl``.

        Includes both ISO codes and normalized names at chain depths
        0 and 1 — both forms work via ``__getattr__``, so both are
        advertised. Region-level and deeper return methods only
        (cities are too numerous to list).

        Result-mode (empty chain, cached DataFrame) branches on row
        count so ``dir()`` reflects what will actually work:

        - **Single row**: geometry methods (``wkt``/``wkb``/``geojson``),
          navigation (``path``/``parent``), DataFrame inspection verbs.
        - **Multi row**: subtype modifiers present in the result (e.g.
          ``.county``, ``.locality``) so callers can narrow, plus the
          DataFrame inspection verbs. Geometry is omitted because it
          would raise ``AmbiguousLocationError``.
        - **Empty**: DataFrame inspection verbs only.
        """
        depth = len(self.chain)
        if depth == 0 and self._df is not None:
            return sorted(self._dir_result_mode())
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

    def _dir_result_mode(self) -> set[str]:
        """Return the attribute surface valid for the current result set.

        Branches on row count: geometry + navigation for single-row,
        subtype modifiers for multi-row, DataFrame verbs for empty.
        See ``__dir__`` for the full contract.
        """
        assert self._df is not None
        row_count = self._df.count()
        base = set(_DIR_DATAFRAME_METHODS)
        if row_count == 0:
            return base
        if row_count == 1:
            return base | _DIR_CITY_METHODS
        # Multi-row: only the DataFrame inspection verbs. Dot access
        # is admin-hierarchy only — there's no in-place subtype filter,
        # so nothing else belongs in the surface here.
        return base

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

    def __getitem__(self, key: Any) -> Wkl | sedonadb.dataframe.DataFrame:
        """[Deprecated] Handle bracket access for location chaining.

        Emits a :class:`DeprecationWarning` pointing at the modern API:

        - For name-based access, use dot notation: ``wkls.india`` instead of
          ``wkls["IN"]``, ``wkls.us.oregon`` instead of ``wkls.us["OR"]``.
        - For wildcard search, use :meth:`search`: ``wkls.us.ca.search("fran")``
          instead of ``wkls.us.ca["%fran%"]``.

        DataFrame-style indexing (list / slice keys) is unaffected —
        it passes through to the cached DataFrame and does not warn.

        The shim is preserved for backward compatibility and will be
        removed in a future major version.
        """
        import warnings

        # DataFrame-style passthrough: list or slice keys operate on
        # the cached DataFrame (result-mode or chain-mode).
        if isinstance(key, (list, slice)):
            df = self.resolve() if self._df is None else self._df
            return df[key]

        key_str = str(key)
        if "%" in key_str:
            cleaned = key_str.strip("%")
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
                f"use dot access (wkls.{chain_prefix}{key_str.lower()}) or the "
                "corresponding name form.",
                DeprecationWarning,
                stacklevel=2,
            )

        new_chain = self.chain + [key_str.lower()]
        if len(new_chain) > 3 and "%" not in key_str:
            raise ValueError("Too many chained attributes (max = 3)")
        # Wildcard pattern: return the raw DataFrame for back-compat.
        if "%" in key_str:
            return Wkl(new_chain).resolve()

        new_wkl = Wkl(new_chain)
        new_wkl._df = new_wkl.resolve()
        return new_wkl

    def __repr__(self) -> str:
        """Return string representation of the underlying data.

        Three shapes:

        - **Root** (empty chain, no DataFrame): short friendly label.
        - **Chain-mode**: a one-line state header (path/chain, rows,
          subtype breakdown), then the DataFrame table, plus a "Did
          you mean?" + ``.search()`` hint when no rows matched.
        - **Result-mode** (empty chain, cached DataFrame from a
          listing/search call): header + DataFrame table.

        The state header is designed so callers — especially AI agents
        doing multi-turn work — can read their own state at a glance
        without an extra ``.count()`` call.
        """
        if not self.chain and self._df is None:
            return "Wkl(root)"

        base_repr = repr(self.resolve())
        header = self._repr_header()

        if self.chain:
            # Detect empty result by scanning the repr for the "header
            # separator immediately followed by footer" pattern that
            # SedonaDB uses for zero-row tables. Avoids an extra count() call.
            is_empty = False
            lines = base_repr.strip().split("\n")
            for i, line in enumerate(lines[:-1]):
                if line.startswith("╞") and lines[i + 1].startswith("└"):
                    is_empty = True
                    break
            if is_empty:
                suggestions = self._get_suggestions(self.chain[-1])
                hint = _build_error_hint(self.chain, suggestions) + "\n"
                return f"{header}\n{hint}{base_repr}"
        return f"{header}\n{base_repr}"

    def _repr_header(self) -> str:
        """One-line state header: path/chain, row count, subtype breakdown.

        Formatted so an agent inspecting a ``Wkl`` can see its mode and
        size at a glance. ``path=`` is used when the chain resolves to a
        single row (round-trippable); ``chain=`` is used otherwise.
        """
        parts: list[str] = []
        if self.chain:
            path_str = "wkls." + ".".join(self.chain)
            key = "path" if self._is_single_row() else "chain"
            parts.append(f"{key}='{path_str}'")

        row_count = self._safe_row_count()
        parts.append(f"rows={row_count}")

        if row_count >= 1:
            subtypes = self._subtype_counts()
            if len(subtypes) == 1:
                st = next(iter(subtypes))
                parts.append(f"subtype='{st}'")
            elif subtypes:
                body = ", ".join(f"{k}: {v}" for k, v in subtypes.items())
                parts.append(f"subtypes={{{body}}}")

        return f"Wkl({', '.join(parts)})"

    def _is_single_row(self) -> bool:
        """True iff the resolved DataFrame holds exactly one row."""
        try:
            df = self._df if self._df is not None else self.resolve()
            return df.count() == 1
        except Exception:
            return False

    def _safe_row_count(self) -> int:
        """Best-effort row count; 0 on failure so repr never throws."""
        try:
            df = self._df if self._df is not None else self.resolve()
            return int(df.count())
        except Exception:
            return 0

    def _subtype_counts(self) -> dict[str, int]:
        """Distinct subtypes with row counts, ordered by count descending."""
        try:
            df = self._df if self._df is not None else self.resolve()
            df.to_view("_wkls_repr_subtypes", overwrite=True)
            tbl = sedona.sql(
                "SELECT subtype, COUNT(*) AS n FROM _wkls_repr_subtypes "
                "GROUP BY subtype ORDER BY n DESC"
            ).to_arrow_table()
            return {
                tbl.column("subtype")[i].as_py(): tbl.column("n")[i].as_py()
                for i in range(tbl.num_rows)
            }
        except Exception:
            return {}

    def resolve(self) -> sedonadb.dataframe.DataFrame:
        """Resolve the location chain to a DataFrame.

        Idempotent: returns ``self._df`` if already populated (either
        eagerly by ``__getattr__`` on chain access, or explicitly by
        listing/search methods in result-mode). Otherwise executes the
        appropriate SQL query based on the chain depth.

        Returns:
            DataFrame containing matching location records.

        Raises:
            ValueError: If the chain is empty and no cached DataFrame
                is available (i.e., called on a root ``Wkl``).
        """
        if self._df is not None:
            return self._df

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

        # Depth 4: parent-narrower. Filter by parent_id of the resolved
        # depth-3 row (passed in via __init__ _parent_id).
        if len(self.chain) > 3:
            if not self._parent_id:
                raise ValueError(
                    "4-level chain requires a resolved parent_id; "
                    "construct via __getattr__ so the parent is known."
                )
            self._df = sedona.sql(
                queries.CHILDREN_BY_PARENT.format(
                    parent_id=sqlescape(self._parent_id),
                    name=sqlescape(self.chain[3]),
                )
            )
            return self._df

        self._df = sedona.sql(
            query.format(**{k: sqlescape(v) for k, v in params.items()})
        )
        return self._df

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
            AmbiguousLocationError: If the resolved DataFrame has >1 row.
            ConnectionError: If the remote Overture data can't be
                registered (first geometry call only; requires S3 access).
        """
        _ensure_overture_loaded()
        df = self.resolve()
        row_count = df.count()
        if row_count == 0:
            # Chain-mode empty: fall back to the "Did you mean?" hint.
            if self.chain:
                suggestions = self._get_suggestions(self.chain[-1])
                hint = _build_error_hint(self.chain, suggestions)
                raise ValueError(hint.strip())
            raise ValueError("No rows to resolve into a geometry.")

        if row_count > 1:
            raise AmbiguousLocationError(self._ambiguity_message(df))

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

    def to_dicts(self) -> list[dict[str, Any]]:
        """Return the rows as a list of plain Python dicts.

        Convenience for quick iteration and filtering — avoids the
        pyarrow.Table API. Equivalent to
        ``self.to_arrow_table().to_pylist()``.

        Example:
            >>> non_us = [
            ...     r for r in wkls.search("franklin").to_dicts()
            ...     if r["country"] != "US"
            ... ]
        """
        return self.resolve().to_arrow_table().to_pylist()

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

    def dependencies(self) -> Wkl:
        """List dependencies (territories, overseas regions, etc.) in scope.

        Scope narrows with chain depth, matching the other listing
        methods. ``wkls.dependencies()`` at root lists every dependency
        in the dataset; at any other depth, results are filtered to
        dependencies within the current country chain. A dependency
        chain (e.g. ``wkls.pr``) returns itself.

        Returns:
            A result-mode ``Wkl`` wrapping the matching rows.
        """
        return self._list_top_level_subtype("dependency")

    def countries(self) -> Wkl:
        """List countries in scope.

        Scope narrows with chain depth, matching the other listing
        methods. ``wkls.countries()`` at root lists every country in
        the dataset; at any other depth, the result is the one country
        that contains the current chain (``wkls.us.countries()`` →
        ``[US]``).

        Returns:
            A result-mode ``Wkl`` wrapping the matching rows.
        """
        return self._list_top_level_subtype("country")

    def _list_top_level_subtype(self, subtype: str) -> Wkl:
        """Shared implementation for ``countries()`` / ``dependencies()``.

        Both list a top-level subtype. At root, return every row of that
        subtype. On any chain, filter by the current country so the
        result is bound to the chain's scope (one row for the
        matching country / dependency, or empty otherwise).
        """
        if not self.chain:
            query = f"""
                SELECT DISTINCT id, country, subtype, name_primary, name_en
                FROM wkls
                WHERE subtype = '{subtype}'
            """
            return Wkl(_df=sedona.sql(query))

        query = f"""
            SELECT DISTINCT id, country, subtype, name_primary, name_en
            FROM wkls
            WHERE subtype = '{subtype}'
              AND country = '{{country}}'
        """
        return Wkl(_df=sedona.sql(query.format(country=sqlescape(self._country_iso))))

    def regions(self) -> Wkl:
        """List regions in the current chain scope.

        Scope follows chain depth:
            - ``wkls.regions()``     — every region worldwide
            - ``wkls.us.regions()``  — every region in the US

        Returns:
            A result-mode ``Wkl`` of region rows.

        Raises:
            ValueError: If called past region level (no regions below regions).
        """
        return self._list_subtype("('region')", "regions")

    def _list_subtype(self, subtype_filter: str, method_name: str) -> Wkl:
        """List rows of the given subtype within the current chain scope.

        Args:
            subtype_filter: SQL subtype filter, e.g. ``"('county')"`` or
                ``"('locality', 'localadmin')"``.
            method_name: Calling method name for error messages.

        Returns:
            A result-mode ``Wkl`` wrapping the matching rows.

        Raises:
            ValueError: If called on a chain that resolves to more than
                one row past region level (can't scope by a single parent).
        """
        depth = len(self.chain)

        if depth == 0:
            query = f"SELECT * FROM wkls WHERE subtype IN {subtype_filter}"
            return Wkl(_df=sedona.sql(query))

        if depth == 1 or not self._has_region:
            # Country-scoped: depth 1, or depth 2 on a no-region country
            # (which addresses a specific city, so scope collapses to country).
            query = f"""
                SELECT * FROM wkls
                WHERE country = '{{country}}'
                  AND subtype IN {subtype_filter}
            """
            return Wkl(
                _df=sedona.sql(query.format(country=sqlescape(self._country_iso)))
            )

        if depth == 2:
            # Region-scoped.
            query = f"""
                SELECT * FROM wkls
                WHERE country = '{{country}}'
                  AND region = '{{region}}'
                  AND subtype IN {subtype_filter}
            """
            return Wkl(
                _df=sedona.sql(
                    query.format(
                        country=sqlescape(self._country_iso),
                        region=sqlescape(self._region_iso),
                    )
                )
            )

        # Depth >= 3: scope is self + direct descendants. The chain
        # must resolve to a single row; we return that row (if its
        # subtype matches the filter) plus any children whose subtype
        # matches. This keeps the pattern consistent with depth 1-2
        # (where ``regions()`` at region level returns the region row
        # itself) — the current row is in-scope for its own subtype.
        df = self.resolve()
        row_count = df.count()
        if row_count != 1:
            raise ValueError(
                f"{method_name}() past region level requires the chain to "
                f"resolve to a single row; '{'.'.join(self.chain)}' has "
                f"{row_count} rows."
            )
        row_id = df.head(1).to_arrow_table().column("id")[0].as_py()
        query = f"""
            SELECT * FROM wkls
            WHERE (id = '{{row_id}}' OR parent_id = '{{row_id}}')
              AND subtype IN {subtype_filter}
        """
        return Wkl(_df=sedona.sql(query.format(row_id=sqlescape(row_id))))

    def counties(self) -> Wkl:
        """List counties in the current chain scope.

        Scope follows chain depth:
            - ``wkls.counties()``         — every county worldwide
            - ``wkls.us.counties()``      — every county in the US
            - ``wkls.us.ca.counties()``   — every county in California

        Returns:
            A result-mode ``Wkl`` of county rows.

        Raises:
            ValueError: If called past region level.
        """
        return self._list_subtype("('county')", "counties")

    def cities(self) -> Wkl:
        """List cities (localities and localadmins) in the current chain scope.

        Scope follows chain depth:
            - ``wkls.cities()``         — every city worldwide
            - ``wkls.us.cities()``      — every city in the US
            - ``wkls.us.ca.cities()``   — every city in California

        Returns:
            A result-mode ``Wkl`` of city rows.

        Raises:
            ValueError: If called past region level.
        """
        return self._list_subtype("('locality', 'localadmin')", "cities")

    def subtypes(self) -> Wkl:
        """List distinct division subtypes in scope.

        Scope narrows with chain depth, matching the other listing
        methods. ``wkls.subtypes()`` at root enumerates every subtype
        in the dataset; at a country or region chain, the result is
        restricted to subtypes present within that scope (e.g.
        ``wkls.fk.subtypes()`` shows the Falklands lack regions).

        Returns:
            A result-mode ``Wkl`` wrapping the distinct subtype rows.

        Raises:
            ValueError: If the chain resolves to more than one row past
                region level (same single-row requirement as
                ``counties()`` / ``cities()``).
        """
        depth = len(self.chain)

        if depth == 0:
            return Wkl(_df=sedona.sql("SELECT DISTINCT subtype FROM wkls"))

        if depth == 1 or not self._has_region:
            query = """
                SELECT DISTINCT subtype FROM wkls
                WHERE country = '{country}'
            """
            return Wkl(
                _df=sedona.sql(query.format(country=sqlescape(self._country_iso)))
            )

        if depth == 2:
            query = """
                SELECT DISTINCT subtype FROM wkls
                WHERE country = '{country}' AND region = '{region}'
            """
            return Wkl(
                _df=sedona.sql(
                    query.format(
                        country=sqlescape(self._country_iso),
                        region=sqlescape(self._region_iso),
                    )
                )
            )

        # Depth >= 3: scope is self + descendants (same semantics as
        # counties()/cities() past region level).
        df = self.resolve()
        row_count = df.count()
        if row_count != 1:
            raise ValueError(
                "subtypes() past region level requires the chain to resolve "
                f"to a single row; '{'.'.join(self.chain)}' has {row_count} rows."
            )
        row_id = df.head(1).to_arrow_table().column("id")[0].as_py()
        query = """
            SELECT DISTINCT subtype FROM wkls
            WHERE id = '{row_id}' OR parent_id = '{row_id}'
        """
        return Wkl(_df=sedona.sql(query.format(row_id=sqlescape(row_id))))

    def search(self, query: str) -> Wkl:
        """Search for locations whose names contain a substring.

        Searches every row within the current ``Wkl``'s scope —
        countries, dependencies, regions, counties, and localities
        alike — and returns matches as a result-mode ``Wkl``. Rows
        carry a ``subtype`` column so callers can tell what they got
        back.

        Scope is determined by the current ``Wkl``:

        - **Root** (``wkls.search(q)``): full dataset.
        - **Chain-mode** (``wkls.us.search(q)`` /
          ``wkls.us.ca.search(q)``): scoped to that country or region.
        - **Result-mode** (the output of a previous search or listing
          call): narrows *within* the current rows, so chained calls
          like ``wkls.us.ca.search('san').search('san francisco')``
          progressively filter the same result set.

        Args:
            query: Search string. Matched against normalized forms of
                ``name_primary`` and ``name_en`` — both sides are
                lowercased and stripped of non-alphanumerics before
                comparison, so ``"san francisco"``, ``"San Francisco"``,
                and ``"sanfrancisco"`` all match the same rows.

        Returns:
            A result-mode ``Wkl`` of matching rows.

        Raises:
            ValueError: If called past city level (chain depth > 2).

        Examples:
            >>> import wkls
            >>> wkls.search("san francisco")              # full dataset
            >>> wkls.us.search("los angeles")             # scoped to US
            >>> wkls.us.ca.search("san")                  # scoped to CA
            >>> wkls.us.ca.search("san").search("fran")   # narrow within
        """
        depth = len(self.chain)
        if depth > 2:
            raise ValueError(
                "search() cannot be called past city level "
                f"(chain has {depth} elements; max searchable depth is 2)."
            )

        # Normalize to the dot-access form so ``search("sanfrancisco")``
        # and ``search("San Francisco")`` both match "San Francisco".
        escaped_query = sqlescape(_normalize_name(query))

        # Result-mode: narrow within the already-resolved rows. The
        # previous search/listing call has already scoped the data; a
        # fresh global scan would ignore that scope entirely.
        if depth == 0 and self._df is not None:
            view_name = "_wkls_search_within"
            self._df.to_view(view_name, overwrite=True)
            sql = queries.SEARCH_WITHIN_VIEW.format(
                view_name=view_name, query=escaped_query
            )
            return Wkl(_df=sedona.sql(sql))

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
        return Wkl(_df=sedona.sql(sql))
