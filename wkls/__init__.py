"""wkls — Well-Known Locations. Administrative boundaries via dot access.

Quickstart:

    >>> import wkls
    >>> wkls.us.ca.sanfrancisco.wkt()      # geometry as WKT string
    >>> wkls.india.maharashtra.geojson()   # or GeoJSON, or wkb()

Chain depth maps to the admin hierarchy (max 3 for unambiguous cases):

    wkls.<country>                         # country / dependency
    wkls.<country>.<region>                # state / province
    wkls.<country>.<region>.<city>         # county / locality / localadmin

Names are lowercased with non-alphanumerics stripped; ISO codes work
too: wkls.us, wkls.unitedstates, wkls.us.ca, wkls.us.california.

When a chain resolves to >1 row, geometry methods raise
AmbiguousLocationError (a ValueError subclass). Three dot-faithful
ways to narrow, plus an escape hatch:

    wkls.us.ca.mission.locality            # subtype modifier
    wkls.us.pa.adamscounty.franklin        # 4-level parent narrower
    wkls.by_id('273bc9a0-...')             # exact pick (UUID from
                                           #   the error message)

Navigation and introspection:

    wkls.us.ca.sanfrancisco.parent         # → California
    wkls.us.ca.sanfrancisco.path           # 'wkls.us.ca.sanfrancisco'
    wkls.us.ca.search('mission')           # substring search, any depth

Listing scopes narrow with chain depth:

    wkls.countries()                       # all 219 countries
    wkls.us.regions()                      # 51 US regions
    wkls.us.ca.counties()                  # 58 CA counties
    wkls.us.ca.sandiegocounty.cities()     # 19 localities in SD County

Every call returns a Wkl — one unified type. Inspect with .count(),
.head(), .to_arrow_table(); extract geometry with .wkt() / .wkb() /
.geojson() when the Wkl holds exactly one row.

For the full agent reference including error handling patterns:
    >>> print(wkls.__llm_guide__)

Two ways to use the library:

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
    module_attrs = list(__all__) + ["__version__"]
    try:
        wkl_attrs = dir(_get_instance())
    except Exception:
        wkl_attrs = []
    return sorted(set(module_attrs + wkl_attrs))
