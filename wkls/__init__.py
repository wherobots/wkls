"""wkls — Well-Known Locations.

Provides chainable access to Overture Maps administrative boundary geometries
via Apache SedonaDB. Two usage patterns, both supported:

    >>> import wkls                          # module-level ergonomic access
    >>> wkls.us.ca.sanfrancisco.wkt()

    >>> from wkls import Wkl                 # explicit instantiation
    >>> wkl = Wkl()
    >>> wkl.us.ca.sanfrancisco.wkt()
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any

from .core import ChainableDataFrame, Wkl

__all__ = ["ChainableDataFrame", "Wkl"]

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


def __getattr__(name: str) -> Any:
    if name.startswith("_"):
        raise AttributeError(f"module 'wkls' has no attribute {name!r}")
    return getattr(_get_instance(), name)


def __dir__() -> list[str]:
    module_attrs = list(__all__) + ["__version__"]
    try:
        wkl_attrs = dir(_get_instance())
    except Exception:
        wkl_attrs = []
    return sorted(set(module_attrs + wkl_attrs))
