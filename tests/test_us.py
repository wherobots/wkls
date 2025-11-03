import wkls


def test_access():
    assert wkls.us.wkt().startswith("MULTIPOLYGON (((-116.2887742 32.6039558")
    assert wkls.us.ca.wkt().startswith("MULTIPOLYGON (((-117.1258989 36.9409467")
    assert wkls.us.ny.newyork.wkt().startswith("MULTIPOLYGON (((-74.046135 40.691125")
    assert wkls.us.ca.sanfrancisco.wkt().startswith(
        "MULTIPOLYGON (((-122.5279985 37.8155806"
    )

    assert len(wkls.countries()) == 219
    assert len(wkls.us.regions()) == 51
    assert len(wkls["IN"]["MH"].counties()) == 36
    assert len(wkls["IN"]["MH"].cities()) == 327

    # Test San Francisco search returns DataFrame directly
    san_francisco_results = wkls["us"]["ca"]["%San Francisco%"]
    assert len(san_francisco_results) == 2, (
        "San Francisco search should return exactly two results"
    )
    assert "San Francisco" in san_francisco_results["name"].str.cat(sep=" "), (
        "Results should contain San Francisco"
    )

    # Test subtypes
    subtypes_df = wkls.subtypes()
    expected_subtypes = ["country", "region", "county", "locality", "localadmin"]
    for subtype in expected_subtypes:
        assert subtype in subtypes_df["subtype"].values, (
            f"Subtype '{subtype}' should exist"
        )


def test_overture_version():
    """Test that the Overture Maps dataset version is accessible."""
    # Should work at root level
    assert hasattr(wkls, "overture_version")
    version = wkls.overture_version()
    assert isinstance(version, str)
    assert "2025-09-24.0" in version  # Current version
    print(f"Using Overture Maps dataset version: {version}")

    # Should NOT work on chained objects - method should not exist
    assert not hasattr(wkls.us, "overture_version")
    print(
        "Correctly blocked chained access: wkls.us does not have overture_version method"
    )
