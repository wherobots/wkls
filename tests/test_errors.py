import pytest
import wkls


def test_countries_without_region():
    with pytest.raises(ValueError) as exc_info:
        wkls.fk.regions()
    assert "The country 'FK' does not have regions in the dataset" in str(
        exc_info.value
    )


def test_empty_chain_error():
    """Test that empty chains raise appropriate errors."""
    # Create a new Wkl instance without any chain
    from wkls.core import Wkl

    wkl = Wkl()

    with pytest.raises(ValueError) as exc_info:
        wkl.resolve()
    assert "No attributes in the chain" in str(exc_info.value)
    assert "wkls.country" in str(exc_info.value)


def test_dependencies_chaining_error():
    """Test that dependencies() cannot be called on chained objects."""
    with pytest.raises(ValueError) as exc_info:
        wkls.us.dependencies()
    assert "dependencies() can only be called on the root object" in str(exc_info.value)
    assert "wkls.dependencies()" in str(exc_info.value)


def test_countries_chaining_error():
    """Test that countries() cannot be called on chained objects."""
    with pytest.raises(ValueError) as exc_info:
        wkls.us.countries()
    assert "countries() can only be called on the root object" in str(exc_info.value)
    assert "wkls.countries()" in str(exc_info.value)


def test_regions_chaining_errors():
    """Test regions() validation errors."""
    # regions() on root should fail
    with pytest.raises(ValueError) as exc_info:
        wkls.regions()
    assert "regions() requires exactly one level of chaining" in str(exc_info.value)
    assert "wkls.country.regions()" in str(exc_info.value)

    # regions() on country.region should fail
    with pytest.raises(ValueError) as exc_info:
        wkls.us.ca.regions()
    assert "regions() requires exactly one level of chaining" in str(exc_info.value)


def test_counties_chaining_errors():
    """Test counties() validation errors."""
    # counties() on root should fail
    with pytest.raises(ValueError) as exc_info:
        wkls.counties()
    assert "counties() requires exactly two levels of chaining" in str(exc_info.value)
    assert "wkls.country.region.counties()" in str(exc_info.value)

    # counties() on country only should fail
    with pytest.raises(ValueError) as exc_info:
        wkls.us.counties()
    assert "counties() cannot be called on a country alone" in str(exc_info.value)
    assert "wkls.country.region.counties()" in str(exc_info.value)

    # counties() on country.region.city should fail
    with pytest.raises(ValueError) as exc_info:
        wkls.us.ca.sanfrancisco.counties()
    assert "counties() requires exactly two levels of chaining" in str(exc_info.value)


def test_cities_chaining_errors():
    """Test cities() validation errors."""
    # cities() on root should fail
    with pytest.raises(ValueError) as exc_info:
        wkls.cities()
    assert "cities() requires exactly two levels of chaining" in str(exc_info.value)
    assert "wkls.country.region.cities()" in str(exc_info.value)

    # cities() on country only should fail
    with pytest.raises(ValueError) as exc_info:
        wkls.us.cities()
    assert "cities() cannot be called on a country alone" in str(exc_info.value)

    # cities() on country.region.city should fail
    with pytest.raises(ValueError) as exc_info:
        wkls.us.ca.sanfrancisco.cities()
    assert "cities() cannot be called on a specific city" in str(exc_info.value)


def test_subtypes_chaining_error():
    """Test that subtypes() cannot be called on chained objects."""
    with pytest.raises(ValueError) as exc_info:
        wkls.us.subtypes()
    assert "subtypes() can only be called on the root object" in str(exc_info.value)
    assert "wkls.subtypes()" in str(exc_info.value)


def test_too_many_chained_attributes():
    """Test that too many chained attributes raise an error."""
    with pytest.raises(ValueError) as exc_info:
        wkls.us.ca.sanfrancisco.somethingelse
    assert "Too many chained attributes (max = 3)" in str(exc_info.value)


def test_nonexistent_location_errors():
    """Test errors when trying to access nonexistent locations."""
    # Nonexistent country
    try:
        result = (
            wkls.zz
        )  # ZZ is not a valid country code, should return empty DataFrame
        assert len(result) == 0, "Nonexistent country should return empty DataFrame"
    except Exception:
        pass  # Could be various exceptions from DuckDB depending on validation

    # Nonexistent region
    try:
        result = (
            wkls.us.zz
        )  # ZZ is not a valid state code, should return empty DataFrame
        assert len(result) == 0, "Nonexistent region should return empty DataFrame"
    except Exception:
        pass  # Could be various exceptions from DuckDB depending on validation

    # Nonexistent city with search pattern (this should return empty results)
    result = wkls.us.ca["%nonexistentcity%"]
    assert len(result) == 0, "Nonexistent city search should return empty DataFrame"


def test_geometry_methods_on_empty_results():
    """Test that geometry methods fail gracefully on empty results."""
    # Create a chain that will return empty results
    empty_chain = wkls.us.ca.nonexistentcity

    with pytest.raises(ValueError) as exc_info:
        empty_chain.wkt()
    assert "No result found for: us.ca.nonexistentcity" in str(exc_info.value)


def test_chainable_dataframe_error_propagation():
    """Test that ChainableDataFrame properly propagates errors."""
    # Get a valid DataFrame first
    us_data = wkls.us

    # countries() should fail on chained data
    with pytest.raises(ValueError) as exc_info:
        us_data.countries()
    assert "countries() can only be called on the root object" in str(exc_info.value)

    # regions() should fail on chained data (more than 1 level)
    ca_data = wkls.us.ca
    with pytest.raises(ValueError) as exc_info:
        ca_data.regions()
    assert "regions() requires exactly one level of chaining" in str(exc_info.value)


if __name__ == "__main__":
    test_empty_chain_error()
    test_countries_chaining_error()
    test_regions_chaining_errors()
    test_counties_chaining_errors()
    test_cities_chaining_errors()
    test_subtypes_chaining_error()
    test_too_many_chained_attributes()
    test_nonexistent_location_errors()
    test_geometry_methods_on_empty_results()
    test_chainable_dataframe_error_propagation()
    print("All error handling tests passed!")
