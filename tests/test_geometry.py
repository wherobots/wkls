"""Tests for geometry output formats and Arrow interop.

Covers ``.wkt()``, ``.wkb()``, ``.geojson()``, removal of
``.hexwkb()`` and ``.svg()``, Arrow C data protocol, and the
failure path when geometry methods are called on empty resolve results.
"""

from __future__ import annotations

import json

import pytest

import wkls


@pytest.fixture
def sf() -> wkls.Wkl:
    return wkls.us.ca.sanfrancisco


@pytest.fixture
def stoneyridge() -> wkls.Wkl:
    """A Falkland Islands locality — FK has no regions, so 2-level chain."""
    return wkls.fk.stoneyridge


def test_wkt(sf):
    geom = sf.wkt()
    assert isinstance(geom, str)
    assert geom.startswith("MULTIPOLYGON")


def test_wkb(sf):
    geom = sf.wkb()
    assert isinstance(geom, bytes)


def test_geojson(sf):
    geom = sf.geojson()
    parsed = json.loads(geom)
    assert isinstance(parsed, dict)
    assert parsed["type"] == "MultiPolygon"


def test_hexwkb_removed():
    """hexwkb() no longer exists on Wkl — removed in v1.2.0."""
    assert not hasattr(wkls.Wkl, "hexwkb")


def test_svg_removed():
    """svg() no longer exists on Wkl — removed in v1.2.0."""
    assert not hasattr(wkls.Wkl, "svg")


def test_arrow_interop(sf):
    pa = pytest.importorskip("pyarrow")
    assert hasattr(sf, "__arrow_c_array__")
    array = pa.array(sf)
    assert len(array) == 1
    assert array.type.extension_name == "geoarrow.wkb"
    assert array.storage[0].as_py() == sf.wkb()


def test_geometry_on_no_region_country(stoneyridge):
    """Countries without regions still return valid geometry via 2-level chain."""
    geom = stoneyridge.wkt()
    assert geom.startswith("POLYGON")


def test_geometry_methods_on_empty_results():
    """Geometry methods raise helpfully on empty resolve results."""
    empty = wkls.us.ca.nonexistentcity

    with pytest.raises(ValueError) as exc_info:
        empty.wkt()
    assert "No results found for: us.ca.nonexistentcity" in str(exc_info.value)
