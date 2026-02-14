import wkls


def test_falkland_island():
    # Geometry coordinates may shift between Overture releases, so just
    # verify that a non-empty WKT string is returned for each location.
    assert len(wkls.fk.wkt()) > 0
    assert wkls.fk.cities().count() == 25
    assert len(wkls.fk.stoneyridge.wkt()) > 0


def test_dependencies_function():
    assert wkls.dependencies().count() == 53
