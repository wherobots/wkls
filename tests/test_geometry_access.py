import json

import pytest

import wkls


@pytest.fixture
def stoneyridge() -> wkls.core.ChainableDataFrame:
    return wkls.fk.stoneyridge


@pytest.fixture
def sf() -> wkls.core.ChainableDataFrame:
    # noinspection PyUnresolvedReferences
    return wkls.us.ca.sanfrancisco


def test_wkt(sf):
    geom = sf.wkt()
    assert isinstance(geom, str)
    assert geom.startswith("MULTIPOLYGON")


def test_wkb(sf):
    geom = sf.wkb()
    assert isinstance(geom, bytes)


def test_hexwkb(sf):
    with pytest.raises(NotImplementedError):
        geom = sf.hexwkb()
        assert isinstance(geom, str)


def test_geojson(sf):
    with pytest.raises(NotImplementedError):
        geom = sf.geojson()
        geom = json.loads(geom)
        assert isinstance(geom, dict)


def test_svg(sf):
    with pytest.raises(NotImplementedError):
        geom = sf.svg()
        assert isinstance(geom, str)


def test_arrow(sf):
    pa = pytest.importorskip("pyarrow")
    assert hasattr(sf, "__arrow_c_array__")
    array = pa.array(sf)
    assert len(array) == 1
    assert array.type.extension_name == "geoarrow.wkb"
    assert array.storage[0].as_py() == sf.wkb()


def test_countries_without_region(stoneyridge):
    geom = stoneyridge.wkt()
    assert geom.startswith("POLYGON")
