import wkls


def test_access():
    assert wkls.us.wkt().startswith("MULTIPOLYGON(((-68.383447 47.3194761")
    assert wkls.us.ca.wkt().startswith("MULTIPOLYGON(((-117.1258989 36.9409467")
    assert wkls.us.ny.newyork.wkt().startswith("MULTIPOLYGON(((-74.046135 40.691125")
    assert wkls.us.ny.cityofnewyork.wkt().startswith(
        "MULTIPOLYGON(((-74.046135 40.691125"
    )
    assert wkls.us.ca.sanfrancisco.wkt().startswith(
        "MULTIPOLYGON(((-122.5279985 37.8155806"
    )

    assert wkls.countries().count() == 219
    assert wkls.us.regions().count() == 51
    assert wkls.IN.regions().count() == 37
    assert wkls["IN"]["MH"].counties().count() == 36
    assert wkls["IN"]["MH"].cities().count() == 329

    # Test San Francisco search returns DataFrame directly
    san_francisco_results = wkls["us"]["ca"]["%San Francisco%"].to_arrow_table()
    assert san_francisco_results.num_rows == 2, (
        "San Francisco search should return exactly two results"
    )
    name_values = [
        san_francisco_results.column("name_primary")[i].as_py()
        for i in range(san_francisco_results.num_rows)
    ]
    assert any("San Francisco" in name for name in name_values), (
        "Results should contain San Francisco"
    )

    # Test subtypes
    subtypes_table = wkls.subtypes().to_arrow_table()
    subtype_values = [
        subtypes_table.column("subtype")[i].as_py()
        for i in range(subtypes_table.num_rows)
    ]
    expected_subtypes = ["country", "region", "county", "locality", "localadmin"]
    for subtype in expected_subtypes:
        assert subtype in subtype_values, f"Subtype '{subtype}' should exist"


def test_overture_version():
    """Test that the Overture Maps dataset version is accessible."""
    # Should work at root level
    assert hasattr(wkls, "overture_version")
    version = wkls.overture_version()
    assert isinstance(version, str)
    assert "2025-12-17.0" in version  # Current version
    print(f"Using Overture Maps dataset version: {version}")

    # Should NOT work on chained objects - method should not exist
    assert not hasattr(wkls.us, "overture_version")
    print(
        "Correctly blocked chained access: wkls.us does not have overture_version method"
    )
