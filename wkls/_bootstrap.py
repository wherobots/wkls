"""SedonaDB initialization, caching, and Overture view management."""

from __future__ import annotations

import importlib.resources
import os
from typing import Any, Callable

import sedonadb

from . import data, queries
from ._version import _overture_uri, _resolve_overture_version


def _log_and_query(
    exec_fn: Callable[..., sedonadb.dataframe.DataFrame],
    query: str,
    **kwargs: Any,
) -> sedonadb.dataframe.DataFrame:
    """Execute a SQL query with optional debug logging.

    Args:
        exec_fn: Function to execute the SQL query.
        query: SQL query string to execute.
        **kwargs: Additional keyword arguments passed to exec_fn (e.g. params).

    Returns:
        DataFrame containing the query results.
    """
    if os.environ.get("WKLS_DEBUG", "false").lower() in ["true", "yes", "1"]:
        print(query)
        if kwargs:
            print(f"  params={kwargs}")
    return exec_fn(query, **kwargs)


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

    # Enable interactive mode for auto-display. Widen the repr beyond the
    # 100-char/terminal default so UUID columns aren't truncated.
    sedona.options.interactive = True
    sedona.options.width = 200

    # Monkey-patch `.sql()` for debug mode.
    sedona_sql = sedona.sql
    sedona.sql = lambda q, **kw: _log_and_query(sedona_sql, q, **kw)

    sedona.sql(queries.INITIALIZATION)
    sedona.read_parquet(
        f"{importlib.resources.files(data)}/overture.zstd18.parquet"
    ).to_view("wkls")

    return sedona


def _seed_country_info(sedona: sedonadb.SedonaContext) -> None:
    """Populate ``_country_info`` for every country identifier in one pass.

    Without this, each unique country access (``wkls.us``, ``wkls.unitedstates``,
    etc.) fires two lookup queries the first time — and ``help(wkls)`` /
    ``dir()`` introspection paths fan this out across ~438 identifiers.
    Two small scans up front replace hundreds of queries on the cold path.
    """
    regions_tbl = sedona.sql(queries.COUNTRY_INFO_SEED_WITH_REGIONS).to_arrow_table()
    has_region_isos: set[str] = {
        regions_tbl.column("iso")[i].as_py() for i in range(regions_tbl.num_rows)
    }
    tbl = sedona.sql(queries.COUNTRY_INFO_SEED).to_arrow_table()
    for i in range(tbl.num_rows):
        iso = tbl.column("iso")[i].as_py()
        name = tbl.column("name")[i].as_py()
        value = (iso, iso in has_region_isos)
        _country_info[iso.lower()] = value
        if name:
            _country_info[name] = value


def _ensure_overture_loaded(*, force: bool = False) -> None:
    """Register the remote Overture GeoParquet view on first geometry access.

    Resolves the active Overture version (via ``WKLS_OVERTURE_VERSION``,
    module-level cache, or an S3 listing), then registers the remote
    GeoParquet as the ``overture`` SedonaDB view. Idempotent — later
    calls short-circuit on ``_overture_view_loaded`` unless *force* is
    set (used by ``configure()`` to reload after a version change).

    Args:
        force: If True, reload the view even if already loaded.

    Raises:
        ConnectionError: If the S3 listing or parquet read fails. The
            message points at the network requirement.
    """
    global _current_overture_version, _overture_view_loaded
    if _overture_view_loaded and not force:
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


# Module-level state for the active Overture version
_current_overture_version: str | None = None

# True once the remote GeoParquet has been registered as the
# ``overture`` SedonaDB view.
_overture_view_loaded: bool = False

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

# Initialize the table when the module is imported
sedona = _initialize_table()

_seed_country_info(sedona)
