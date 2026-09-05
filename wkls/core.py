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

import difflib
import re
from collections.abc import Callable, Iterator
from typing import Any

import pyarrow as pa
import sedonadb

from . import _bootstrap, queries
from ._geometry import _GeometryMixin
from ._version import _list_s3_releases, _resolve_overture_version

__all__ = ["AmbiguousLocationError", "Wkl"]

# Backward-compat aliases — mutable containers share identity with _bootstrap,
# so .clear() / mutation on either module's reference affects both.
_dir_cache = _bootstrap._dir_cache
_country_info = _bootstrap._country_info
_region_info = _bootstrap._region_info
_row_info = _bootstrap._row_info

# Scalar state and the sedona context are re-exported so that existing
# code (including tests) that reads/patches ``core.<name>`` keeps working.
sedona = _bootstrap.sedona
_current_overture_version = _bootstrap._current_overture_version
_overture_view_loaded = _bootstrap._overture_view_loaded


def _ensure_overture_loaded(*, force: bool = False) -> None:
    """Thin wrapper around ``_bootstrap._ensure_overture_loaded``.

    Kept in ``core`` so ``monkeypatch.setattr(core, ...)`` in tests can
    swap the flag or the function without touching ``_bootstrap``.
    """
    global _current_overture_version, _overture_view_loaded
    if _overture_view_loaded and not force:
        return
    # Delegate to _bootstrap for the heavy lifting, but sync scalar
    # state back so both modules stay consistent.
    _bootstrap._overture_view_loaded = _overture_view_loaded
    _bootstrap._current_overture_version = _current_overture_version
    _bootstrap._ensure_overture_loaded(force=force)
    _current_overture_version = _bootstrap._current_overture_version
    _overture_view_loaded = _bootstrap._overture_view_loaded


class AmbiguousLocationError(ValueError):
    """Raised when a geometry method is called on a ``Wkl`` holding >1 row.

    Subclass of ``ValueError`` so existing ``except ValueError:`` blocks
    keep catching it. The message lists every candidate with its subtype,
    parent name, and id, and points at the dot-based disambiguation paths:

    - subtype filter on the result: ``wkls.us.ca.search('mission').cities()``
    - 4-level parent narrower: ``wkls.us.pa.adamscounty.franklin``
    - exact pick: ``wkls.by_id('<uuid>')``

    ``candidates`` holds the multi-row ``Wkl`` that raised, so callers can
    recover programmatically (``err.candidates.to_dicts()``,
    ``err.candidates[0].wkt()``) instead of parsing the message.
    """

    def __init__(self, message: str, candidates: Wkl | None = None) -> None:
        super().__init__(message)
        self.candidates = candidates


# Methods surfaced by __dir__ at each chain depth.
_DIR_ROOT_METHODS = frozenset(
    {
        "Wkl",
        "by_id",
        "cities",
        "configure",
        "counties",
        "countries",
        "dependencies",
        "regions",
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
        "to_arrow_table",
        "to_dicts",
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
        "to_arrow_table",
        "to_dicts",
        "wkb",
        "wkt",
    }
)
_DIR_CITY_METHODS = frozenset(
    {
        "cities",
        "geojson",
        "parent",
        "path",
        "search",
        "to_arrow_table",
        "to_dicts",
        "wkb",
        "wkt",
    }
)

# Result-mode: DataFrame passthroughs that make sense to surface on a
# Redirect tables — priority: S1 > S2 > S3.
# Checked in order by __getattr__; first match wins.
_S1_REDIRECTS: dict[str, str] = {
    "filter": (
        "To find locations by name, use 'wkl.search(\"query\")'. "
        "For arbitrary column filters, use 'wkl.to_arrow_table()' "
        "and your engine of choice."
    ),
    "where": (
        "To find locations by name, use 'wkl.search(\"query\")'. "
        "For arbitrary column filters, use 'wkl.to_arrow_table()' "
        "and your engine of choice."
    ),
    "query": (
        "To find locations by name, use 'wkl.search(\"query\")'. "
        "For arbitrary column filters, use 'wkl.to_arrow_table()' "
        "and your engine of choice."
    ),
    "resolve": "Renamed in v1.2.0. Use 'wkl.to_arrow_table()'.",
}

_S2_REDIRECTS: dict[str, str] = {
    "count": "Use 'len(wkl)'.",
    "head": "Use 'wkl[:n]'.",
    "limit": "Use 'wkl[:n]'.",
    "take": "Use 'wkl[:n]'.",
    "show": "Use 'wkl[:n]'.",
    "collect": "Use 'list(wkl)'.",
    "to_pylist": "Use 'list(wkl)'.",
    "tail": "Use 'wkl[-n:]'.",
    "first": "Use 'wkl[0]'.",
    "last": "Use 'wkl[-1]'.",
    "sample": "Use 'wkl.to_arrow_table()' and your engine of choice.",
}

_S3_REDIRECTS: dict[str, str] = {
    "select": "Use 'wkl.to_arrow_table()' and your engine of choice.",
    "with_columns": "Use 'wkl.to_arrow_table()' and your engine of choice.",
    "join": "Use 'wkl.to_arrow_table()' and your engine of choice.",
    "group_by": "Use 'wkl.to_arrow_table()' and your engine of choice.",
    "agg": "Use 'wkl.to_arrow_table()' and your engine of choice.",
    "sort": "Use 'wkl.to_arrow_table()' and your engine of choice.",
    "order_by": "Use 'wkl.to_arrow_table()' and your engine of choice.",
    "to_pandas": "Use 'wkl.to_arrow_table().to_pandas()'.",
    "to_geopandas": "Use 'gpd.GeoDataFrame.from_arrow(wkl.to_arrow_table())'.",
    "to_polars": "Use 'pl.from_arrow(wkl.to_arrow_table())'.",
    "to_arrow": "Use 'wkl.to_arrow_table()' (the public name follows SedonaDB/DuckDB convention).",
    "drop": "Use 'wkl.to_arrow_table()' and your engine of choice.",
    "iloc": "Use 'wkl[i]' for positional access.",
    "loc": "Use 'wkl[i]' for positional access.",
    "compute": "Use 'wkl.to_arrow_table()' and your engine of choice.",
    "distinct": "Use 'wkl.to_arrow_table()' and your engine of choice.",
    "unique": "Use 'wkl.to_arrow_table()' and your engine of choice.",
}

_GENERIC_FALLBACK = (
    "Row fields: wkl.to_dicts()[0]['name_en'] (keys: id, country, region, "
    "subtype, name_primary, name_en, parent_id). Geometry: .wkt(), .wkb(), "
    ".geojson(). Anything else: wkl.to_arrow_table() and your engine of choice."
)

# Colloquial country codes that are not ISO 3166-1 alpha-2. Applied at the
# root only, where nothing but countries and dependencies resolve, so the
# Russian locality "Uk" and the Japanese county "Usa" stay reachable through
# their own chains.
_COUNTRY_ALIASES: dict[str, str] = {"uk": "gb", "usa": "us", "uae": "ae"}

# Row-field names agents reach for as attributes on a single-row Wkl. None of
# them normalizes to a place name in the bundle, so redirecting is safe.
_FIELD_REDIRECTS: frozenset[str] = frozenset(
    {
        "name",
        "names",
        "name_primary",
        "name_en",
        "subtype",
        "country",
        "region",
        "parent_id",
        "geometry",
    }
)


def _field_redirect(attr: str) -> str:
    """Explain how to read a row field that was accessed as an attribute."""
    if attr == "geometry":
        return (
            "Geometry comes from .wkt(), .wkb() or .geojson() on a single-row "
            "Wkl, or from .to_arrow_table() for many rows."
        )
    key = {"name": "name_en", "names": "name_primary"}.get(attr, attr)
    return (
        f"Read row fields with wkl.to_dicts()[0]['{key}'] "
        "(keys: id, country, region, subtype, name_primary, name_en, parent_id)."
    )


# Listing methods that narrow a multi-row result to a single subtype
# (or inspect what subtypes are present). Surfaced via ``dir()`` on a
# multi-row result-mode ``Wkl`` so agents discover the subtype-filter
# path instead of falling straight to ``by_id`` when ``.wkt()`` raises.
_DIR_RESULT_NARROW_METHODS = frozenset(
    {"cities", "counties", "countries", "dependencies", "regions", "subtypes"}
)

# Rows sedonadb's DataFrame repr prints before cutting off (its ``show``
# default). Used to append an explicit "more rows" marker.
_REPR_ROWS = 10

# Surface 2 error messages. Kept at module scope so tests can pattern-match
# a stable substring without coupling to the exact full text.
_ROOT_NO_ROWS_MSG = (
    "Root Wkl has no rows to inspect. Use dot access "
    "(wkls.us, wkls.india.maharashtra) or a listing/search method "
    "(wkls.countries(), wkls.us.search('...')) to produce a result first."
)

_STR_SUBSCRIPT_MSG = (
    "Wkl does not support string subscript access.\n"
    "  - For chain drill: use dot access — wkls.us.ca.sanfrancisco\n"
    "  - For name search: use .search() — wkls.us.search('francisco')\n"
    "  - For a specific row's column: use .to_dicts() and index the dict\n"
    "  - For arbitrary DataFrame ops: call .to_arrow_table() and use your engine of choice"
)

# Maps admin subtype → listing method name. Used by
# _ambiguity_message to suggest subtype-narrowing calls.
_METHOD_FOR_SUBTYPE: dict[str, str] = {
    "country": "countries",
    "dependency": "dependencies",
    "region": "regions",
    "county": "counties",
    "locality": "cities",
    "localadmin": "cities",
}


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


def _group_candidates(
    table: pa.Table,
    fetch_row: Callable[[str], dict[str, object] | None],
    sample: int = 10,
) -> list[dict[str, Any]]:
    """Extract and annotate candidate rows for an ambiguity message.

    Args:
        table: Arrow table of candidate rows.
        fetch_row: Callable to look up a parent row by id.
        sample: Max candidates to process.

    Returns:
        List of dicts with keys: subtype, id, attr, name, parent_attr, parent_name.
    """
    n = min(table.num_rows, sample)
    candidates: list[dict[str, Any]] = []
    for i in range(n):
        row = {col: table.column(col)[i].as_py() for col in table.column_names}
        attr = _chain_attr_for_row(row)
        name = row.get("name_en") or row.get("name_primary") or "?"
        parent_id = row.get("parent_id")
        parent_attr: str | None = None
        parent_name: str | None = None
        if parent_id:
            parent = fetch_row(str(parent_id))
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
    return candidates


class Wkl(_GeometryMixin):
    """Administrative boundaries via dot access.

    For full usage (chaining, disambiguation, listing, navigation,
    errors), see ``help(wkls)``.

    Example:
        >>> import wkls
        >>> wkls.us.ca.sanfrancisco.wkt()

    Python collection protocol (Surface 2) — ``Wkl`` implements
    ``collections.abc.Sequence`` on the rows of the resolved result:

    - ``len(wkl)``         row count
    - ``bool(wkl)``        True iff non-empty
    - ``for row in wkl``   iterate; each row is itself a single-row Wkl
    - ``wkl[i]``           positional index; supports negatives
    - ``wkl[a:b]``         slice; returns a multi-row Wkl
    - ``uuid in wkl``      membership check against the ``id`` column

    Iteration is backed by a cached pyarrow Table — no SQL per row. For
    DataFrame operations outside admin-boundary lookup (``.filter``,
    ``.join``, ``.group_by``, …), call ``.to_arrow_table()``.
    """

    _has_region: bool = True

    # Methods that only make sense on the root `Wkl`. Hidden from chained
    # instances so `hasattr(wkls.us, "configure")` is False — matching the
    # pre-unification contract. ``_LISTING_ROOT_METHODS`` is the subset
    # that, while hidden on chain-mode, *is* allowed through on
    # result-mode (empty chain + cached DataFrame) so it can narrow
    # within the prior result. Config methods stay strictly root-only.
    _ROOT_ONLY_METHODS = frozenset(
        {
            "configure",
            "overture_releases",
            "overture_version",
        }
    )
    _LISTING_ROOT_METHODS = frozenset({"countries", "dependencies", "subtypes"})

    def __getattribute__(self, name: str) -> Any:
        if name in type(self)._ROOT_ONLY_METHODS:
            # `chain` and `_df` have to be fetched without re-triggering
            # this hook — use object.__getattribute__.
            chain = object.__getattribute__(self, "chain")
            df = object.__getattribute__(self, "_df")
            if chain:
                raise AttributeError(
                    f"'{type(self).__name__}' object has no attribute '{name}'"
                )
            # Result-mode (df set, no chain): listing methods pass
            # through to narrow; config methods stay blocked.
            if df is not None and name not in type(self)._LISTING_ROOT_METHODS:
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
        # Cache of the resolved Arrow table for Surface 2 dunders. Populated
        # lazily on first ``_materialize()`` call; safe because ``Wkl`` is
        # immutable (no re-resolve path in v1.2).
        self._arrow_table: pa.Table | None = None
        if not self.chain:
            return

        key = self.chain[0].lower()
        if key not in _bootstrap._country_info:
            iso, has_region = self._lookup_country(key)
            _bootstrap._country_info[key] = (iso, has_region)
            # Alias the ISO form so `wkls.us` and `wkls.unitedstates` share
            # the same cached entry on whichever access comes second.
            _bootstrap._country_info[iso.lower()] = (iso, has_region)

        self._country_iso, self._has_region = _bootstrap._country_info[key]

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
        df = _bootstrap.sedona.sql(
            queries.COUNTRY_LOOKUP, params={"identifier": identifier}
        )
        table = df.to_arrow_table()
        if table.num_rows == 0:
            return identifier.upper(), False
        iso = table.column("iso")[0].as_py()
        df_has = _bootstrap.sedona.sql(
            queries.COUNTRY_HAS_REGIONS, params={"country": iso}
        )
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
        if key not in _bootstrap._region_info:
            _bootstrap._region_info[key] = self._lookup_region(raw)
        return _bootstrap._region_info[key]

    def _lookup_region(self, identifier: str) -> str:
        """Resolve a region identifier to its canonical ISO (e.g. 'IN-MH').

        Accepts bare suffix ('mh'), full ISO ('IN-MH'), or name ('maharashtra').
        """
        full_iso = (
            identifier
            if "-" in identifier
            else f"{self._country_iso}-{identifier.upper()}"
        )
        df = _bootstrap.sedona.sql(
            queries.REGION_LOOKUP,
            params={
                "country": self._country_iso,
                "identifier": full_iso,
                "name": identifier,
            },
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
        table = df.to_arrow_table()
        n = table.num_rows
        if self.chain:
            subject = f"{n} matches for 'wkls.{'.'.join(self.chain)}'"
        else:
            subject = f"{n} rows in this result"
        sample = min(n, 10)

        candidates = _group_candidates(table, self._fetch_row, sample)

        # Dots only step through the admin hierarchy. The two cases where
        # a chain narrower can resolve the ambiguity:
        #   1. Candidates have different normalized names (e.g. York vs
        #      YorkCounty) — swap the last chain segment for the
        #      unambiguous form.
        #   2. Candidates share a normalized name but have different
        #      parents (e.g. 18 Franklins in PA counties) — use the
        #      4-level parent narrower.
        # A third narrower applies when candidates span multiple
        # subtypes (e.g. a search result with both a city and a
        # county): call the subtype method on the result.
        # Anything else can only be resolved by picking a specific UUID.
        attrs_differ = len({c["attr"] for c in candidates}) > 1
        parents_differ = len({c["parent_attr"] for c in candidates}) > 1

        lines: list[str] = []
        chain_prefix = (
            "wkls." + ".".join(self.chain[:-1]) if len(self.chain) >= 1 else "wkls"
        )

        if self.chain and attrs_differ:
            lines.append(f"{subject}. Use the unambiguous normalized name:")
            lines.append("")
            for c in candidates:
                lines.append(
                    f"  {chain_prefix}.{c['attr']:<18}  # {c['name']} ({c['subtype']})"
                )
        elif self.chain and parents_differ:
            lines.append(f"{subject}. Narrow by parent (4-level chain):")
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
            # paths can't distinguish these.
            lines.append(f"{subject}. Narrow with one of:")

        # Subtype narrowing — applies whenever candidates span multiple
        # subtype *groups* (two rows both in ``locality`` don't get a
        # suggestion since ``.cities()`` wouldn't reduce anything). Shown
        # alongside the primary narrower, before ``by_id``.
        by_method: dict[str, list[str]] = {}
        for c in candidates:
            method = _METHOD_FOR_SUBTYPE.get(c["subtype"])
            if method:
                by_method.setdefault(method, []).append(c["subtype"])
        if len(by_method) > 1:
            lines.append("")
            lines.append("Filter by subtype on this result:")
            for method, subtypes in by_method.items():
                label = "/".join(sorted(set(subtypes)))
                lines.append(f"  .{method}()  # {len(subtypes)} {label} row(s)")

        # Always show by_id lines with literal UUID + .wkt() call so agents
        # can copy-paste directly.
        lines.append("")
        lines.append("Pick by id:")
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
        df = _bootstrap.sedona.sql(queries.ROW_BY_ID, params={"row_id": row_id})
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
        row_count = self._resolve().count()
        if row_count != 1:
            raise ValueError(
                f".path requires a single-row Wkl; this one has {row_count} rows."
            )

        # Walk up parent_id collecting chain attributes. Names are
        # normalized to lowercase + no whitespace, matching __getattr__.
        table = self._resolve().head(1).to_arrow_table()
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
        df = self._resolve()
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
        if row_id in _bootstrap._row_info:
            return _bootstrap._row_info[row_id]
        df = _bootstrap.sedona.sql(queries.ROW_BY_ID, params={"row_id": row_id})
        table = df.to_arrow_table()
        if table.num_rows == 0:
            return None
        row = {col: table.column(col)[0].as_py() for col in table.column_names}
        _bootstrap._row_info[row_id] = row
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
        if _bootstrap._current_overture_version is None:
            _bootstrap._current_overture_version = _resolve_overture_version()
        return _bootstrap._current_overture_version

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
        return _list_s3_releases()

    def configure(self, overture_version: str) -> None:
        """Configure the Overture Maps dataset version.

        Validates the requested version against available S3 releases,
        then re-creates the ``overture`` SedonaDB view pointing to the
        new version's GeoParquet data.

        This method is only available at the root level
        (``wkls.configure(overture_version="...")``).

        Args:
            overture_version: Version string to use (e.g., ``"2026-07-22.0"``).

        Raises:
            ValueError: If called on a chained object or version is unavailable.
            ConnectionError: If S3 listing fails.

        Example:
            >>> import wkls
            >>> wkls.overture_releases()
            ['2026-07-22.0', '2026-08-19.0']
            >>> wkls.configure(overture_version="2026-07-22.0")
            >>> wkls.overture_version()
            '2026-07-22.0'
        """
        available = _list_s3_releases()
        if overture_version not in available:
            raise ValueError(
                f"Overture Maps version '{overture_version}' is not available on S3.\n"
                f"Available versions: {', '.join(available)}\n"
                "Use wkls.overture_releases() to list all available versions."
            )

        _bootstrap._current_overture_version = overture_version
        global _current_overture_version
        _current_overture_version = overture_version
        _bootstrap._country_info.clear()
        _bootstrap._region_info.clear()
        _bootstrap._dir_cache.clear()
        _bootstrap._row_info.clear()
        _bootstrap._seed_country_info(_bootstrap.sedona)
        _ensure_overture_loaded(force=True)

    def __getattr__(self, attr: str) -> Wkl:
        """Handle attribute access — chain drill or redirect.

        Resolution order:

        1. Private/dunder attributes → ``AttributeError``.
        2. Root-only methods → ``AttributeError``.
        3. Row-field names and redirect tables (S1 → S2 → S3) →
           ``AttributeError`` naming the right call.
        4. Chain drill (location identifier, underscores stripped, root
           aliases ``uk``/``usa``/``uae`` applied) → new ``Wkl``.
        5. Generic fallback → ``AttributeError``.

        Raises:
            AttributeError: For private/dunder attributes, root-only
                methods, redirect-table matches, or any unknown
                attribute.
            ValueError: If chain depth would exceed 4.
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

        # Row fields are not attributes; say where they live instead of
        # drilling "name_primary" into the chain as a place name.
        if attr in _FIELD_REDIRECTS:
            raise AttributeError(
                f"'{attr}' is not a Wkl attribute. {_field_redirect(attr)}"
            )

        # Redirect check — must fire before chain drill so that known
        # non-location attrs (e.g. .filter) don't silently extend the chain.
        for table in (_S1_REDIRECTS, _S2_REDIRECTS, _S3_REDIRECTS):
            if attr in table:
                raise AttributeError(
                    f"'{attr}' is not part of the Wkl API. {table[attr]}"
                )

        # Chain drill: only attempt for names that could be location
        # identifiers (alphanumeric). Result-mode Wkls (no chain, have
        # _df) skip drill — they have no tree position.
        if self.chain or self._df is None:
            # Attribute names can't hold spaces, so agents write
            # ``san_francisco``; the bundle's normalized form has no
            # separators at all. Strip underscores before matching.
            token = attr.replace("_", "").lower()
            if not self.chain:
                token = _COUNTRY_ALIASES.get(token, token)
            if token.isalnum():
                new_chain = self.chain + [token]
                if len(new_chain) > 4:
                    raise ValueError(
                        "Chain too deep (max = 4). Use .by_id('<uuid>') for specific rows."
                    )

                # Depth 4: parent-narrower. The preceding 3-level chain must
                # resolve to exactly one row.
                if len(new_chain) == 4:
                    df = self._df if self._df is not None else self._resolve()
                    count = df.count()
                    if count != 1:
                        raise ValueError(
                            "4-level chain requires the preceding chain to resolve to a "
                            f"single row; '{'.'.join(self.chain)}' has {count} rows."
                        )
                    parent_row = df.head(1).to_arrow_table()
                    parent_id = parent_row.column("id")[0].as_py()
                    return Wkl(new_chain, _parent_id=parent_id)
                return Wkl(new_chain)

        raise AttributeError(
            f"'{attr}' is not part of the Wkl API. {_GENERIC_FALLBACK}"
        )

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Explain a call on a ``Wkl`` instead of a bare "not callable".

        A misspelled method (``wkls.us.wktt()``) is read as a place name
        first, so the parentheses land here. Name the closest public
        method so the caller can fix the typo in one step.
        """
        methods = sorted(
            n
            for n in dir(type(self))
            if not n.startswith("_") and callable(getattr(type(self), n))
        )
        token = self.chain[-1] if self.chain else ""
        where = "wkls." + ".".join(self.chain) if self.chain else "this Wkl"
        close = difflib.get_close_matches(token, methods, n=1, cutoff=0.6)
        if close:
            raise TypeError(
                f"'{token}' is not a Wkl method; it was read as a place name "
                f"({where}). Did you mean .{close[0]}()? "
                f"Wkl methods: {', '.join(methods)}."
            )
        raise TypeError(
            f"Wkl is not callable; drop the parentheses ({where}). "
            f"Wkl methods: {', '.join(methods)}."
        )

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
        listing narrowers for multi-row, DataFrame verbs for empty.
        See ``__dir__`` for the full contract.
        """
        assert self._df is not None
        row_count = self._df.count()
        base = {"to_arrow_table", "to_dicts"}
        if row_count == 0:
            return base
        if row_count == 1:
            return base | _DIR_CITY_METHODS
        # Multi-row: surface the subtype-filter methods alongside the
        # DataFrame verbs so agents can discover the narrow-by-subtype
        # path (e.g. ``search(...).cities()``) without reading docs.
        return base | _DIR_RESULT_NARROW_METHODS

    def _dir_countries(self) -> list[str]:
        """Return cached country-level dir entries (ISO codes + names)."""
        key: tuple[str, ...] = ()
        if key not in _bootstrap._dir_cache:
            _bootstrap._dir_cache[key] = self._collect_dir_entries(
                _bootstrap.sedona.sql(queries.DIR_COUNTRIES)
            )
        return _bootstrap._dir_cache[key]

    def _dir_regions(self) -> list[str]:
        """Return cached region-level dir entries (ISO suffixes + names)."""
        key: tuple[str, ...] = (self._country_iso,)
        if key not in _bootstrap._dir_cache:
            df = _bootstrap.sedona.sql(
                queries.DIR_REGIONS, params={"country": self._country_iso}
            )
            _bootstrap._dir_cache[key] = self._collect_dir_entries(df)
        return _bootstrap._dir_cache[key]

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

    # --- Surface 2: Python collection protocol ------------------------------
    #
    # Wkl implements collections.abc.Sequence on the rows of the resolved
    # result set. Backed by a cached pyarrow Table (_arrow_table) so that
    # len/iter/getitem avoid a sedona round-trip per call. The old bracket
    # access shim (pre-v1.2 DeprecationWarning) is replaced by the new
    # protocol — string subscripts now raise TypeError with a pointer at
    # dot access / .search() / .resolve().

    def _materialize(self) -> pa.Table:
        """Resolve and materialize rows as a pyarrow Table, caching the result.

        All Surface 2 dunders go through this to avoid multiple sedona
        round-trips on the same ``Wkl``. Immutable after first call.
        """
        if self._arrow_table is None:
            self._arrow_table = self._resolve().to_arrow_table()
        return self._arrow_table

    def __len__(self) -> int:
        """Number of rows in the resolved result.

        Raises:
            TypeError: If called on a root ``Wkl`` (no chain, no ``_df``).
        """
        try:
            return self._materialize().num_rows
        except ValueError as e:
            raise TypeError(_ROOT_NO_ROWS_MSG) from e

    def __bool__(self) -> bool:
        """True iff the resolved result has at least one row.

        Raises:
            TypeError: If called on a root ``Wkl`` (no chain, no ``_df``).
        """
        return len(self) > 0

    def __iter__(self) -> Iterator[Wkl]:
        """Yield one single-row ``Wkl`` per row in the resolved result.

        Each yielded ``Wkl`` is result-mode with ``_df`` set to a one-row
        slice of the parent's Arrow table, rewrapped via
        ``sedona.create_data_frame``. No SQL round-trip per step.

        Raises:
            TypeError: If called on a root ``Wkl``. Raised eagerly at
                ``iter()`` call time (not deferred to the first
                ``next()``) so callers see the error at the binding
                site.
        """
        # Materialize eagerly so a root-Wkl TypeError surfaces at
        # iter() call time, matching user expectation. A plain generator
        # function would defer the raise to the first .__next__().
        try:
            table = self._materialize()
        except ValueError as e:
            raise TypeError(_ROOT_NO_ROWS_MSG) from e

        def _gen() -> Iterator[Wkl]:
            for i in range(table.num_rows):
                yield _wkl_from_arrow_slice(table.slice(i, 1))

        return _gen()

    def __getitem__(self, key: int | slice) -> Wkl:
        """Index or slice into the resolved result set.

        Args:
            key: ``int`` (supports negatives) for a single-row ``Wkl``, or
                ``slice`` (step must be 1) for a multi-row ``Wkl``.

        Raises:
            TypeError: If ``key`` is not ``int`` or ``slice``. String keys
                point at dot access / ``.search()`` / ``.resolve()`` (see
                ``_STR_SUBSCRIPT_MSG``). Sliced steps other than 1 are
                rejected with a pointer at ``.resolve()``.
            IndexError: If an ``int`` key is outside ``[-len, len)``.
            TypeError: If called on a root ``Wkl`` (no chain, no ``_df``).
        """
        # Type-check the key BEFORE resolving, so that string / list keys
        # on a root Wkl get the specific bracket-removal message rather
        # than the generic "root Wkl has no rows" one.
        if isinstance(key, str):
            raise TypeError(_STR_SUBSCRIPT_MSG)

        if isinstance(key, bool):
            # bool is a subclass of int in Python; reject to avoid
            # wkl[True] silently being wkl[1].
            raise TypeError(
                f"Wkl indices must be int or slice, got {type(key).__name__}"
            )

        if not isinstance(key, int | slice):
            raise TypeError(
                f"Wkl indices must be int or slice, got {type(key).__name__}"
            )

        try:
            table = self._materialize()
        except ValueError as e:
            raise TypeError(_ROOT_NO_ROWS_MSG) from e

        if isinstance(key, int):
            n = table.num_rows
            idx = key + n if key < 0 else key
            if idx < 0 or idx >= n:
                raise IndexError(f"Wkl index {key} out of range (rows={n})")
            return _wkl_from_arrow_slice(table.slice(idx, 1))

        # key is a slice at this point (the int path above returned).
        assert isinstance(key, slice)
        start, stop, step = key.indices(table.num_rows)
        if step != 1:
            raise TypeError(
                f"Wkl does not support sliced steps (got step={step}). "
                "Call .to_arrow_table() for arbitrary DataFrame slicing."
            )
        length = max(0, stop - start)
        return _wkl_from_arrow_slice(table.slice(start, length))

    def __contains__(self, item: object) -> bool:
        """True iff ``item`` (as a string) equals any row's ``id`` column.

        Narrow on purpose: the admin-boundary use case is
        ``uuid in search_result``. Column-scanning for arbitrary values
        is out of scope — use ``.to_dicts()`` or ``.resolve()`` for that.
        Returns ``False`` on any failure (root ``Wkl``, missing ``id``
        column) because ``in`` is a soft-check idiom and shouldn't raise.
        """
        if not isinstance(item, str):
            return False
        try:
            table = self._materialize()
        except ValueError:
            return False
        if "id" not in table.column_names:
            return False
        id_col = table.column("id")
        for i in range(id_col.length()):
            if id_col[i].as_py() == item:
                return True
        return False

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

        df = self._resolve()
        subtypes = self._subtype_counts()
        header = self._repr_header(subtypes)
        row_count = sum(subtypes.values())

        if row_count == 0:
            # No table frame for an empty result: the header already says
            # rows=0, and the hint says what to try next.
            if self.chain:
                suggestions = self._get_suggestions(self.chain[-1])
                hint = _build_error_hint(self.chain, suggestions)
                return f"{header}\n{hint}".rstrip()
            return header

        # Drop parent_id from repr — it's a raw UUID that adds noise.
        # Users who need it can call .to_dicts() or .to_arrow_table().
        cols = [c for c in df.columns if c != "parent_id"]
        col_list = ", ".join(cols)
        view_name = "_wkls_repr_tmp"
        df.to_view(view_name, overwrite=True)
        display_df = _bootstrap.sedona.sql(f"SELECT {col_list} FROM {view_name}")
        # Width comes from ``sedona.options.width`` (set in _bootstrap) so
        # UUID columns aren't truncated. Going through ``repr()`` keeps us
        # on the public sedonadb API.
        base_repr = repr(display_df)

        if row_count > _REPR_ROWS:
            # sedonadb prints the first _REPR_ROWS rows with no marker; say
            # so, or a reader assumes the table is complete.
            return (
                f"{header}\n{base_repr}\n"
                f"… {row_count - _REPR_ROWS} more rows not shown. "
                "Use wkl[i], wkl[:n], .to_dicts() or .search() to narrow."
            )
        return f"{header}\n{base_repr}"

    def _repr_header(self, subtypes: dict[str, int] | None = None) -> str:
        """One-line state header: path/chain, row count, subtype breakdown.

        Formatted so an agent inspecting a ``Wkl`` can see its mode and
        size at a glance. ``path=`` is used when the chain resolves to a
        single row (round-trippable); ``chain=`` is used otherwise.
        ``subtypes`` lets ``__repr__`` share one count query.
        """
        if subtypes is None:
            subtypes = self._subtype_counts()
        row_count = sum(subtypes.values())

        parts: list[str] = []
        if self.chain:
            path_str = "wkls." + ".".join(self.chain)
            key = "path" if row_count == 1 else "chain"
            parts.append(f"{key}='{path_str}'")

        parts.append(f"rows={row_count}")

        if len(subtypes) == 1:
            st = next(iter(subtypes))
            parts.append(f"subtype='{st}'")
        elif subtypes:
            body = ", ".join(f"{k}: {v}" for k, v in subtypes.items())
            parts.append(f"subtypes={{{body}}}")

        return f"Wkl({', '.join(parts)})"

    def _subtype_counts(self) -> dict[str, int]:
        """Distinct subtypes with row counts, ordered by count descending.

        One query serves the full repr header — total rows is the sum of
        the values, single-row is ``sum == 1``. Empty dict on failure so
        ``__repr__`` never throws.
        """
        try:
            df = self._df if self._df is not None else self._resolve()
            df.to_view("_wkls_repr_subtypes", overwrite=True)
            tbl = _bootstrap.sedona.sql(
                "SELECT subtype, COUNT(*) AS n FROM _wkls_repr_subtypes "
                "GROUP BY subtype ORDER BY n DESC"
            ).to_arrow_table()
            return {
                tbl.column("subtype")[i].as_py(): tbl.column("n")[i].as_py()
                for i in range(tbl.num_rows)
            }
        except Exception:
            return {}

    def _build_query(self) -> tuple[str, dict[str, str]]:
        """Return (query_template, params) for the current chain.

        Does NOT execute the query. Does NOT set ``self._df``.
        Caller is responsible for formatting and executing the query.

        Raises:
            ValueError: If chain is empty.
        """
        if not self.chain:
            raise ValueError(
                "No attributes in the chain. Use wkls.<country> or "
                "wkls.<country>.<region>, etc."
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

        if len(self.chain) > 3:
            if not self._parent_id:
                raise ValueError(
                    "4-level chain requires a resolved parent_id; "
                    "construct via __getattr__ so the parent is known."
                )
            query = queries.CHILDREN_BY_PARENT
            params["parent_id"] = self._parent_id
            params["name"] = self.chain[3]

        return query, params

    def _resolve(self) -> sedonadb.dataframe.DataFrame:
        """Resolve the location chain to a SedonaDB DataFrame.

        Idempotent: returns ``self._df`` if already populated (by a
        prior ``_resolve()`` on this instance, or explicitly by
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

        query, params = self._build_query()
        sql = query.format(table="wkls", columns="*")
        self._df = _bootstrap.sedona.sql(sql, params=params)
        return self._df

    def _get_suggestions(
        self, failed_name: str, n: int = 5, max_distance: int = 12
    ) -> list[str]:
        """Get similar location names for "did you mean" suggestions.

        For country/region codes (2-char ISO codes), uses simple prefix matching.
        For city names, uses Levenshtein distance for fuzzy matching.

        Args:
            failed_name: The name that wasn't found.
            n: Maximum number of suggestions to return.
            max_distance: Maximum score for city-level suggestions (default
                12: exact, prefix and substring matches plus up to two
                edits; anything farther is noise, not a suggestion).

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
            query = queries.SUGGEST_COUNTRY.format(limit=n)
            params = {"search_term": search_term}
        elif len(self.chain) == 2:
            country_iso = self._country_iso
            if self._has_region:
                # Region-level suggestions (prefix match on region codes)
                query = queries.SUGGEST_REGION.format(limit=n)
                params = {"country": country_iso, "search_term": search_term}
            else:
                # City-level for countries without regions (e.g., wkls.fk.stoney)
                query = queries.SUGGEST_CITY.format(
                    region_filter="",
                    limit=n * 2,
                )
                params = {"country": country_iso, "search_term": search_term}
                use_distance_filter = True
        else:
            # City-level with region (e.g., wkls.us.ca.sanfran)
            country_iso = self._country_iso
            query = queries.SUGGEST_CITY.format(
                region_filter="AND region = $region",
                limit=n * 2,
            )
            params = {
                "country": country_iso,
                "region": self._region_iso,
                "search_term": search_term,
            }
            use_distance_filter = True

        result = _bootstrap.sedona.sql(query, params=params)

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

    def to_dicts(self) -> list[dict[str, Any]]:
        """Return rows as plain dicts — metadata only, never geometry.

        The cheap, programmatic-inspection complement to ``to_arrow_table()``.
        No query against the geometry view, no Arrow extension types — just
        admin-metadata columns as Python primitives.

        For geometry, use ``to_arrow_table()`` (multi-row, GeoArrow WKB)
        or the single-row terminals (``.wkt()`` / ``.geojson()`` / ``.wkb()``).

        Examples:
            >>> hits = wkls.search("franklin").to_dicts()
            >>> [r for r in hits if r["country"] != "US"]
        """
        return self._resolve().to_arrow_table().to_pylist()

    def to_arrow_table(self) -> pa.Table:
        """Materialize this Wkl as a pyarrow.Table — the Surface 3 escape.

        Returns a standalone pyarrow.Table suitable for handoff to any
        Arrow-aware engine (sedona, DuckDB, GeoPandas, Polars).
        Geometry is included as a GeoArrow WKB extension column with
        CRS = OGC:CRS84.

        Chain-mode Wkls (e.g. ``wkls.us.ca.cities()``) issue a single
        query directly against the Overture parquet — no local-then-remote
        two-pass. Result-mode Wkls (from ``.search()``, ``.by_id()``)
        use an id-based lookup against Overture.

        For metadata-only inspection (no geometry), use ``to_dicts()``
        instead.

        Returns:
            pyarrow.Table with metadata columns plus a 'geometry' column
            typed as ``geoarrow.wkb<OGC:CRS84>``.

        Raises:
            ValueError: If the chain is empty and no cached DataFrame is
                available (root Wkl).

        Examples:
            >>> tbl = wkls.us.ca.cities().to_arrow_table()
            >>> import geopandas as gpd
            >>> gdf = gpd.GeoDataFrame.from_arrow(tbl)

            >>> # DuckDB
            >>> import duckdb
            >>> duckdb.from_arrow(tbl)
        """
        _ensure_overture_loaded()

        if self._df is not None:
            return self._to_arrow_table_from_df()

        if not self.chain:
            raise ValueError(
                "No attributes in the chain. Use wkls.<country> or "
                "wkls.<country>.<region>, etc."
            )

        # Resolve IDs locally, then fetch full rows from overture
        # with structural filters for row-group pruning.
        resolved = self._resolve()
        meta_tbl = resolved.to_arrow_table()
        ids = meta_tbl.column("id").to_pylist()

        if not ids:
            return self._empty_overture_table()

        where, params = self._build_overture_filter(ids)
        df = _bootstrap.sedona.sql(
            f"SELECT {queries.GEOMETRY_COLUMNS} FROM overture WHERE {where}",
            params=params,
        )
        return self._apply_geoarrow_encoding(df.to_arrow_table())

    def _to_arrow_table_from_df(self) -> pa.Table:
        """IN-list fallback for result-mode Wkls.

        Result-mode Wkls come from listing/search methods and may span
        multiple countries/regions, so we can only filter by ID — no
        structural pruning is possible.
        """
        _ensure_overture_loaded()

        meta_tbl = self._df.to_arrow_table()
        ids = meta_tbl.column("id").to_pylist()

        if not ids:
            return self._empty_overture_table()

        placeholders = ", ".join(f"$id_{i}" for i in range(len(ids)))
        id_params = {f"id_{i}": v for i, v in enumerate(ids)}
        df = _bootstrap.sedona.sql(
            f"SELECT {queries.GEOMETRY_COLUMNS} "
            f"FROM overture WHERE id IN ({placeholders})",
            params=id_params,
        )
        return self._apply_geoarrow_encoding(df.to_arrow_table())

    def _build_overture_filter(self, ids: list[str]) -> tuple[str, dict[str, str]]:
        """Build a WHERE clause for querying the remote Overture table.

        Combines structural filters (country, region) for row-group
        pruning with an ID IN (...) list for correctness.
        """
        clauses: list[str] = []
        params: dict[str, str] = {}

        country_iso = self._country_iso
        if country_iso:
            clauses.append("country = $country")
            params["country"] = country_iso

        if len(self.chain) >= 2 and self._has_region:
            region_iso = country_iso + "-" + self.chain[1].upper()
            clauses.append("region = $region")
            params["region"] = region_iso

        # Always include ID list for correctness.
        placeholders = ", ".join(f"$id_{i}" for i in range(len(ids)))
        for i, v in enumerate(ids):
            params[f"id_{i}"] = v
        clauses.append(f"id IN ({placeholders})")

        return " AND ".join(clauses), params

    @staticmethod
    def _empty_overture_table() -> pa.Table:
        """Return an empty table with id and GeoArrow geometry columns."""
        import geoarrow.pyarrow as ga  # noqa: F811

        wkb_col = ga.with_crs(pa.array([], type=pa.binary()), crs=ga.OGC_CRS84)
        return pa.table({"id": pa.array([], type=pa.string()), "geometry": wkb_col})

    @staticmethod
    def _apply_geoarrow_encoding(tbl: pa.Table) -> pa.Table:
        """Cast the geometry column to GeoArrow WKB extension type.

        Handles the binary_view → binary cast that SedonaDB's
        ST_AsBinary emits, and wraps with OGC:CRS84 CRS metadata.
        """
        import geoarrow.pyarrow as ga  # noqa: F811

        geom_idx = tbl.schema.get_field_index("geometry")
        wkb_col = tbl.column(geom_idx)

        # ST_AsBinary surfaces as binary_view in pyarrow; ga.with_crs
        # rejects binary_view, so cast to plain binary first.
        if wkb_col.type == pa.binary_view():
            wkb_col = wkb_col.cast(pa.binary())

        geo_col = ga.with_crs(wkb_col, crs=ga.OGC_CRS84)
        tbl = tbl.set_column(geom_idx, "geometry", geo_col)

        # SedonaDB returns string_view columns; many pyarrow.compute
        # kernels don't support string_view yet, so cast to string.
        for i, field in enumerate(tbl.schema):
            if field.type == pa.string_view():
                tbl = tbl.set_column(i, field.name, tbl.column(i).cast(pa.string()))

        return tbl

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

        Scope narrows with the current Wkl's mode:

        - **Root** (``wkls.dependencies()``): every dependency worldwide.
        - **Chain-mode** (``wkls.us.dependencies()``): dependencies
          within the current country chain (empty for country chains
          like US; ``[self]`` on a dependency chain like PR).
        - **Result-mode** (chained after ``.search(...)`` etc.): the
          subset of the prior rows whose subtype is ``'dependency'``.

        Returns:
            A result-mode ``Wkl`` wrapping the matching rows.
        """
        return self._list_top_level_subtype("('dependency')")

    def countries(self) -> Wkl:
        """List countries in scope.

        Scope narrows with the current Wkl's mode:

        - **Root** (``wkls.countries()``): every country worldwide.
        - **Chain-mode** (``wkls.us.countries()``): the one country
          that contains the current chain.
        - **Result-mode** (chained after ``.search(...)`` etc.): the
          subset of the prior rows whose subtype is ``'country'``.

        Returns:
            A result-mode ``Wkl`` wrapping the matching rows.
        """
        return self._list_top_level_subtype("('country')")

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

    def _narrow_result_mode_by_subtypes(self, subtype_filter: str) -> Wkl:
        """Filter a result-mode ``Wkl``'s cached DataFrame by subtype.

        Shared helper for ``countries()`` / ``dependencies()`` /
        ``regions()`` / ``counties()`` / ``cities()`` to implement
        the "listing method narrows within the prior result" behavior
        uniformly. Callers must ensure they're in result-mode (empty
        chain, ``_df`` set) before invoking.

        Args:
            subtype_filter: SQL subtype filter in IN-list form,
                e.g. ``"('county')"`` or ``"('locality', 'localadmin')"``.

        Returns:
            A result-mode ``Wkl`` wrapping the filtered rows.
        """
        assert self._df is not None and not self.chain
        self._df.to_view("_wkls_list_within", overwrite=True)
        sql = f"SELECT * FROM _wkls_list_within WHERE subtype IN {subtype_filter}"
        return Wkl(_df=_bootstrap.sedona.sql(sql))

    def _list_top_level_subtype(self, subtype_filter: str) -> Wkl:
        """List country/dependency rows scoped at most to the country level.

        Unlike ``_list_subtype``, this never scopes below country level —
        country and dependency rows don't carry region data, so region-
        or parent-scoped queries would always return empty.

        Args:
            subtype_filter: SQL subtype filter, e.g. ``"('country')"``.

        Returns:
            A result-mode ``Wkl`` wrapping the matching rows.
        """
        # Result-mode: narrow within the cached DataFrame.
        if not self.chain and self._df is not None:
            return self._narrow_result_mode_by_subtypes(subtype_filter)

        if not self.chain:
            query = f"SELECT * FROM wkls WHERE subtype IN {subtype_filter}"
            return Wkl(_df=_bootstrap.sedona.sql(query))

        # Any chain depth: scope to the country.
        query = f"""
            SELECT * FROM wkls
            WHERE country = $country
              AND subtype IN {subtype_filter}
        """
        return Wkl(
            _df=_bootstrap.sedona.sql(query, params={"country": self._country_iso})
        )

    def _list_subtype(self, subtype_filter: str, method_name: str) -> Wkl:
        """List rows of the given subtype within the current scope.

        Scope is determined by the current Wkl's mode:

        - **Result-mode** (empty chain, cached DataFrame): filter the
          prior rows. Lets callers chain, e.g.
          ``wkls.us.search('san').counties()``.
        - **Root** (empty chain, no DataFrame): every row of the
          requested subtype worldwide.
        - **Chain-mode**: scoped by country (depth 1) / region
          (depth 2), or by parent_id + self (depth ≥ 3).

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
        # Result-mode: narrow within the cached DataFrame. Shared with
        # countries/dependencies/subtypes via _narrow_result_mode_by_subtypes.
        if not self.chain and self._df is not None:
            return self._narrow_result_mode_by_subtypes(subtype_filter)

        depth = len(self.chain)

        if depth == 0:
            query = f"SELECT * FROM wkls WHERE subtype IN {subtype_filter}"
            return Wkl(_df=_bootstrap.sedona.sql(query))

        if depth == 1 or not self._has_region:
            # Country-scoped: depth 1, or depth 2 on a no-region country
            # (which addresses a specific city, so scope collapses to country).
            query = f"""
                SELECT * FROM wkls
                WHERE country = $country
                  AND subtype IN {subtype_filter}
            """
            return Wkl(
                _df=_bootstrap.sedona.sql(query, params={"country": self._country_iso})
            )

        if depth == 2:
            # Region-scoped.
            query = f"""
                SELECT * FROM wkls
                WHERE country = $country
                  AND region = $region
                  AND subtype IN {subtype_filter}
            """
            return Wkl(
                _df=_bootstrap.sedona.sql(
                    query,
                    params={
                        "country": self._country_iso,
                        "region": self._region_iso,
                    },
                )
            )

        # Depth >= 3: scope is self + direct descendants. The chain
        # must resolve to a single row; we return that row (if its
        # subtype matches the filter) plus any children whose subtype
        # matches. This keeps the pattern consistent with depth 1-2
        # (where ``regions()`` at region level returns the region row
        # itself) — the current row is in-scope for its own subtype.
        df = self._resolve()
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
            WHERE (id = $row_id OR parent_id = $row_id)
              AND subtype IN {subtype_filter}
        """
        return Wkl(_df=_bootstrap.sedona.sql(query, params={"row_id": row_id}))

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

        Scope is determined by the current Wkl's mode:

        - **Root** (``wkls.subtypes()``): every subtype in the dataset.
        - **Chain-mode** (``wkls.us.subtypes()`` / ``wkls.us.ca.subtypes()``):
          subtypes present within that chain's subtree (e.g.
          ``wkls.fk.subtypes()`` shows the Falklands lack regions).
        - **Result-mode** (chained after ``.search(...)`` etc.):
          distinct subtypes present in the prior rows.

        Raises:
            ValueError: If the chain resolves to more than one row past
                region level (same single-row requirement as
                ``counties()`` / ``cities()``).
        """
        # Result-mode: narrow within the cached DataFrame.
        if not self.chain and self._df is not None:
            self._df.to_view("_wkls_list_within", overwrite=True)
            return Wkl(
                _df=_bootstrap.sedona.sql(
                    "SELECT DISTINCT subtype FROM _wkls_list_within"
                )
            )

        depth = len(self.chain)

        if depth == 0:
            return Wkl(_df=_bootstrap.sedona.sql("SELECT DISTINCT subtype FROM wkls"))

        if depth == 1 or not self._has_region:
            query = """
                SELECT DISTINCT subtype FROM wkls
                WHERE country = $country
            """
            return Wkl(
                _df=_bootstrap.sedona.sql(query, params={"country": self._country_iso})
            )

        if depth == 2:
            query = """
                SELECT DISTINCT subtype FROM wkls
                WHERE country = $country AND region = $region
            """
            return Wkl(
                _df=_bootstrap.sedona.sql(
                    query,
                    params={
                        "country": self._country_iso,
                        "region": self._region_iso,
                    },
                )
            )

        # Depth >= 3: scope is self + descendants (same semantics as
        # counties()/cities() past region level).
        df = self._resolve()
        row_count = df.count()
        if row_count != 1:
            raise ValueError(
                "subtypes() past region level requires the chain to resolve "
                f"to a single row; '{'.'.join(self.chain)}' has {row_count} rows."
            )
        row_id = df.head(1).to_arrow_table().column("id")[0].as_py()
        query = """
            SELECT DISTINCT subtype FROM wkls
            WHERE id = $row_id OR parent_id = $row_id
        """
        return Wkl(_df=_bootstrap.sedona.sql(query, params={"row_id": row_id}))

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
            TypeError: If ``query`` is not a string.
            ValueError: If ``query`` has no letters or digits, or the chain
                itself matches no rows (the message carries the same
                "did you mean" hint as the chain's repr).

        Examples:
            >>> import wkls
            >>> wkls.search("san francisco")              # full dataset
            >>> wkls.us.search("los angeles")             # scoped to US
            >>> wkls.us.ca.search("san")                  # scoped to CA
            >>> wkls.us.ca.search("san").search("fran")   # narrow within
        """
        if not isinstance(query, str):
            raise TypeError(f"search() takes a str query, got {type(query).__name__}.")
        depth = len(self.chain)
        result_mode = depth == 0 and self._df is not None

        # Normalize to the dot-access form so ``search("sanfrancisco")``
        # and ``search("San Francisco")`` both match "San Francisco".
        normalized_query = _normalize_name(query)
        if not normalized_query:
            raise ValueError(
                "search() needs a non-empty query with at least one letter or digit."
            )

        # A chain that matches nothing has nothing to search within. Raise
        # the hint its repr would show instead of returning a silent empty.
        if self.chain and self._resolve().count() == 0:
            raise ValueError(
                _build_error_hint(
                    self.chain, self._get_suggestions(self.chain[-1])
                ).strip()
            )

        # Depth > 2 or result-mode: resolve to a temp view and search
        # within it. For chain-mode at depth > 2 (e.g. wkls.us.pa.yorkcounty),
        # search the county's cities rather than the county row itself —
        # decided by the chain, not by whether ``_df`` happens to be cached.
        if depth > 2 or result_mode:
            view_name = "_wkls_search_within"
            if result_mode:
                # Result-mode: narrow within existing rows.
                resolved = self._df
            else:
                # Chain-mode depth > 2: search children (cities).
                resolved = self.cities()._df
            resolved.to_view(view_name, overwrite=True)
            sql = queries.SEARCH_WITHIN_VIEW.format(view_name=view_name)
            return Wkl(
                _df=_bootstrap.sedona.sql(sql, params={"query": normalized_query})
            )

        if depth == 0:
            return Wkl(
                _df=_bootstrap.sedona.sql(
                    queries.SEARCH_ROOT, params={"query": normalized_query}
                )
            )
        elif depth == 1 or not self._has_region:
            # Depth 1, or depth 2 on a country that doesn't use regions.
            return Wkl(
                _df=_bootstrap.sedona.sql(
                    queries.SEARCH_COUNTRY,
                    params={
                        "country": self._country_iso,
                        "query": normalized_query,
                    },
                )
            )
        else:  # depth == 2 with regions
            return Wkl(
                _df=_bootstrap.sedona.sql(
                    queries.SEARCH_REGION,
                    params={
                        "country": self._country_iso,
                        "region": self._region_iso,
                        "query": normalized_query,
                    },
                )
            )


# --- Surface 2 helpers (module scope so they reference the completed Wkl class)


def _wkl_from_arrow_slice(table: pa.Table) -> Wkl:
    """Wrap a pyarrow Table slice as a result-mode ``Wkl``.

    The slice is re-materialized as a sedona DataFrame via
    ``sedona.create_data_frame`` so the returned ``Wkl`` has a proper
    ``_df`` for downstream ``.wkt()`` / ``.path`` / ``.parent`` calls.
    """
    return Wkl(_df=_bootstrap.sedona.create_data_frame(table))
