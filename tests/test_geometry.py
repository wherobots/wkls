"""Tests for geometry output formats and Arrow interop.

Covers ``.wkt()``, ``.wkb()``, ``.geojson()``, removal of
``.hexwkb()`` and ``.svg()``, Arrow C data protocol, and the
failure path when geometry methods are called on empty resolve results.
"""

from __future__ import annotations

import json

import pyarrow as pa
import pytest

import wkls
from wkls import _bootstrap, _geometry, core


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


# ---------- id drift between Overture releases ----------


class _FakeFrame:
    def __init__(self, table: pa.Table) -> None:
        self._table = table

    def to_arrow_table(self) -> pa.Table:
        return self._table


def _fake_overture(monkeypatch, rows_by_view: dict[str, list[str]]):
    """Fake the remote geometry views; the local metadata view stays real."""
    calls: list[str] = []

    def fake_sql(query, params=None):
        calls.append(query)
        for view, rows in rows_by_view.items():
            if f"FROM {view} WHERE" in query:
                return _FakeFrame(pa.table({"g": pa.array(rows, pa.string())}))
        return _FakeFrame(pa.table({"g": pa.array([], pa.string())}))

    monkeypatch.setattr(core, "_ensure_overture_loaded", lambda **kw: None)
    monkeypatch.setattr(_bootstrap, "_current_overture_version", "2026-10-21.0")
    monkeypatch.setattr(_geometry, "_bundled_overture_version", lambda: "2026-09-23.1")
    monkeypatch.setattr(
        _bootstrap, "_ensure_overture_view", lambda version: "overture_2026_09_23_1"
    )
    monkeypatch.setattr(_bootstrap.sedona, "sql", fake_sql)
    return calls


def test_geometry_retries_in_the_bundle_release_on_id_miss(monkeypatch):
    """An id the live release dropped is looked up in the bundle's release."""
    sf = wkls.us.ca.sanfrancisco
    sf._resolve()  # local metadata, before the remote views are faked
    calls = _fake_overture(monkeypatch, {"overture_2026_09_23_1": ["POINT (1 1)"]})

    assert sf.wkt() == "POINT (1 1)"
    assert "FROM overture WHERE" in calls[0] and "id = $id" in calls[0]
    assert "FROM overture_2026_09_23_1 WHERE" in calls[1] and "id = $id" in calls[1]
    assert len(calls) == 2


def test_geometry_miss_names_the_release_queried(monkeypatch):
    sf = wkls.us.ca.sanfrancisco
    sf._resolve()
    _fake_overture(monkeypatch, {})
    monkeypatch.setattr(_bootstrap, "_ensure_overture_view", lambda version: None)

    with pytest.raises(ValueError, match="in Overture 2026-10-21.0"):
        sf.wkt()


def test_bundle_view_registration_retries_after_a_transient_failure(monkeypatch):
    """A failed S3 read must not be cached as 'release unavailable'."""
    attempts: list[str] = []

    class _View:
        def to_view(self, name: str, overwrite: bool = False) -> None:
            return None

    def flaky_read_parquet(uri: str, options: dict | None = None) -> _View:
        attempts.append(uri)
        if len(attempts) == 1:
            raise RuntimeError("S3 unreachable")
        return _View()

    monkeypatch.setattr(_bootstrap, "_overture_views", {})
    monkeypatch.setattr(_bootstrap.sedona, "read_parquet", flaky_read_parquet)

    assert _bootstrap._ensure_overture_view("2026-09-23.1") is None
    assert _bootstrap._ensure_overture_view("2026-09-23.1") == "overture_2026_09_23_1"
    assert _bootstrap._ensure_overture_view("2026-09-23.1") == "overture_2026_09_23_1"
    assert len(attempts) == 2
