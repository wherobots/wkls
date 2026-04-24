"""wkls — Well-Known Locations. Administrative boundaries via dot access.

Quickstart:

    >>> import wkls
    >>> wkls.us.ca.sanfrancisco.wkt()      # geometry as WKT string
    >>> wkls.india.maharashtra.geojson()   # or GeoJSON, or wkb()

In scripts and agent shells, call ``wkls.help()`` (or
``print(wkls.__doc__)``) to read this guide. The builtin ``help(wkls)``
opens a terminal pager that can't be driven non-interactively.

Chain depth maps to the admin hierarchy (max 3 for unambiguous cases):

    wkls.<country>                         # country / dependency
    wkls.<country>.<region>                # state / province
    wkls.<country>.<region>.<city>         # county / locality / localadmin

Names are lowercased with non-alphanumerics stripped; ISO codes work
too: wkls.us, wkls.unitedstates, wkls.us.ca, wkls.us.california.

Resolving ambiguity — when a chain resolves to >1 row, geometry
methods raise AmbiguousLocationError (a ValueError subclass) with a
list of candidates and copy-paste-ready code. Three ways to narrow:

    wkls.us.ca.search('san diego').cities()  # (a) filter by subtype on a result
    wkls.us.pa.adamscounty.franklin          # (b) 4-level parent narrower
    wkls.by_id('273bc9a0-...')               # (c) exact pick (UUID from the error)

When an *intermediate* chain step is ambiguous (e.g. 'york' in PA is
both a locality and a county), pick the unambiguous full normalized
name of the intermediate row:

    wkls.us.pa.york.wkt()                  # raises — 'york' is ambiguous
    wkls.us.pa.yorkcounty.franklin.wkt()   # ✓ pick the County unambiguously,
                                           #   then drill for the child

Search — .search(q) at any chain depth. Lead with a scoped search to
filter by country/region; fall back to global only when scope isn't
known. The query is normalized (lowercase, non-alphanumerics stripped),
so 'san francisco' / 'San Francisco' / 'sanfrancisco' all match.

    wkls.au.search('franklin')             # Franklin(s) in Australia
    wkls.us.tn.search('franklin')          # Franklins in Tennessee only
    wkls.search('franklin')                # global — 125+ rows, use sparingly

Every call returns a Wkl — one unified type. Inspect like any Python
sequence:

    len(wkl)                               # row count
    for row in wkl: row.wkt()              # iterate; each row is a Wkl
    wkl[0], wkl[-1]                        # positional index
    wkl[:5]                                # slice; returns a multi-row Wkl
    '<uuid>' in wkl                        # id-column membership check
    list(wkl), tuple(wkl)                  # standard collection conversions

Also available: .count(), .head(n), .limit(n), .to_dicts(),
.to_arrow_table(), .show(). For DataFrame ops beyond admin-boundary
lookup (.filter, .join, .group_by, …), call .resolve() to drop to the
underlying SedonaDB DataFrame. Extract geometry with .wkt() / .wkb() /
.geojson() when the Wkl holds exactly one row.

Navigation — .parent walks up one level; .path returns the canonical
dot-chain string that round-trips via eval:

    wkls.us.ca.sanfrancisco.parent         # → California
    wkls.us.ca.sanfrancisco.path           # 'wkls.us.ca.sanfrancisco'

Listing — scopes narrow with chain depth:

    wkls.countries()                       # all 219 countries
    wkls.us.regions()                      # 51 US regions
    wkls.us.ca.counties()                  # 58 CA counties
    wkls.us.ca.sandiegocounty.cities()     # 19 localities in SD County

Two ways to use the library (equivalent):

    >>> import wkls                        # module-level ergonomics
    >>> wkls.us.ca.sanfrancisco.wkt()

    >>> from wkls import Wkl               # explicit instantiation
    >>> wkl = Wkl()
    >>> wkl.us.ca.sanfrancisco.wkt()
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any

from .core import Wkl

__all__ = ["Wkl"]


# Intentionally shadows the builtin at module level so ``wkls.help()``
# just works; deliberately omitted from ``__all__`` so ``from wkls import *``
# does not pollute the caller's namespace with an alternate ``help``.
def help() -> None:  # noqa: A001
    """Print the wkls guide to stdout without opening the terminal pager.

    Equivalent to ``print(wkls.__doc__)``. Use this from scripts and
    agent shells — the builtin ``help(wkls)`` opens a pager that hangs
    non-interactive drivers.
    """
    print(__doc__)


try:
    __version__ = version("wkls")
except PackageNotFoundError:
    __version__ = "0.0.0.dev"


_instance: Wkl | None = None


def _get_instance() -> Wkl:
    global _instance
    if _instance is None:
        _instance = Wkl()
    return _instance


# Names removed in 1.2.0 — surface explicit migration messages instead of
# silently delegating to chain-drill and producing empty Wkl results.
_REMOVED: dict[str, str] = {
    "ChainableDataFrame": (
        "ChainableDataFrame was removed in wkls 1.2.0; Wkl is now the sole "
        "public type. Replace any `ChainableDataFrame` references with `Wkl`."
    ),
}


def __getattr__(name: str) -> Any:
    if name.startswith("_"):
        raise AttributeError(f"module 'wkls' has no attribute {name!r}")
    if name in _REMOVED:
        raise AttributeError(_REMOVED[name])
    return getattr(_get_instance(), name)


def __dir__() -> list[str]:
    module_attrs = list(__all__) + ["__version__", "help"]
    try:
        wkl_attrs = dir(_get_instance())
    except Exception:
        wkl_attrs = []
    return sorted(set(module_attrs + wkl_attrs))
