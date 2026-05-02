"""Tests for ``Wkl.to_arrow_table()`` — Surface 3 escape hatch.

Covers chain-mode (direct Overture query) and result-mode (IN-list
fallback) paths, schema validation, GeoArrow encoding, and edge cases.
"""

import pyarrow as pa
import pytest

import wkls


def test_chain_mode_returns_pyarrow_table():
    """wkls.us.ca.cities().to_arrow_table() returns pa.Table."""
    tbl = wkls.us.ca.cities().to_arrow_table()
    assert isinstance(tbl, pa.Table)
    assert len(tbl) > 0


def test_chain_mode_has_geometry_column():
    """Geometry column exists and is GeoArrow WKB extension type."""
    tbl = wkls.us.ca.cities().to_arrow_table()
    assert "geometry" in tbl.schema.names
    geom_field = tbl.schema.field("geometry")
    # Extension type name contains 'geoarrow.wkb'
    assert "geoarrow.wkb" in str(geom_field.type)


def test_chain_mode_has_metadata_columns():
    """All expected metadata columns present."""
    tbl = wkls.us.ca.cities().to_arrow_table()
    expected = {
        "id",
        "country",
        "region",
        "subtype",
        "name_primary",
        "name_en",
        "parent_id",
        "geometry",
    }
    assert expected <= set(tbl.schema.names)


def test_chain_mode_correct_crs():
    """GeoArrow column has OGC:CRS84 CRS metadata."""
    tbl = wkls.us.ca.cities().to_arrow_table()
    geom_field = tbl.schema.field("geometry")
    # CRS is encoded in the extension metadata
    assert (
        "CRS84" in str(geom_field.metadata)
        or "CRS84" in str(geom_field.type)
        or "CRS84" in repr(geom_field.type)
    )


def test_result_mode_from_cities():
    """Result-mode Wkl (from .cities()) produces valid table."""
    cities = wkls.us.ca.cities()
    _ = list(cities)  # force result-mode
    tbl = cities.to_arrow_table()
    assert isinstance(tbl, pa.Table)
    assert "geometry" in tbl.schema.names


def test_result_mode_from_search():
    """Result-mode from .search() works."""
    tbl = wkls.search("san francisco").to_arrow_table()
    assert isinstance(tbl, pa.Table)
    assert len(tbl) > 0


def test_empty_result_returns_empty_table_with_schema():
    """Chain that matches 0 rows → empty table, correct schema."""
    tbl = wkls.us.ca.search("xyznonexistent999").to_arrow_table()
    assert isinstance(tbl, pa.Table)
    assert len(tbl) == 0
    assert "geometry" in tbl.schema.names


def test_root_wkl_raises_valueerror():
    """to_arrow_table() on root Wkl raises ValueError."""
    with pytest.raises(ValueError):
        wkls.Wkl().to_arrow_table()


def test_roundtrip_geopandas():
    """GeoPandas round-trip produces valid GeoDataFrame."""
    gpd = pytest.importorskip("geopandas")

    tbl = wkls.us.ca.cities().to_arrow_table()
    gdf = gpd.GeoDataFrame.from_arrow(tbl)
    assert gdf.geometry.notna().any()
    assert len(gdf) == len(tbl)


def test_no_string_view_columns():
    """String columns are utf8, not utf8_view (pyarrow.compute compat)."""
    tbl = wkls.us.ca.counties().to_arrow_table()
    for field in tbl.schema:
        assert field.type != pa.string_view(), f"{field.name} is string_view"
