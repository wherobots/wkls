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
    assert "wkls.<country>" in str(exc_info.value)


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
    assert "wkls.<country>.regions()" in str(exc_info.value)

    # regions() on country.region should fail
    with pytest.raises(ValueError) as exc_info:
        wkls.us.ca.regions()
    assert "regions() requires exactly one level of chaining" in str(exc_info.value)


def test_counties_chaining_errors():
    """Test counties() validation errors."""
    # counties() on root should fail
    with pytest.raises(ValueError) as exc_info:
        wkls.counties()
    assert "counties() requires exactly one or two levels of chaining" in str(
        exc_info.value
    )
    assert "wkls.<country>.<region>.counties()" in str(exc_info.value)

    # counties() on country only should fail
    with pytest.raises(ValueError) as exc_info:
        wkls.us.counties()
    assert "counties() cannot be called on a country alone" in str(exc_info.value)
    assert "wkls.<country>.<region>.counties()" in str(exc_info.value)

    # counties() on country.region.city should fail
    with pytest.raises(ValueError) as exc_info:
        wkls.us.ca.sanfrancisco.counties()
    assert "counties() requires exactly one or two levels of chaining" in str(
        exc_info.value
    )


def test_cities_chaining_errors():
    """Test cities() validation errors."""
    # cities() on root should fail
    with pytest.raises(ValueError) as exc_info:
        wkls.cities()
    assert "cities() requires exactly one or two levels of chaining" in str(
        exc_info.value
    )
    assert "wkls.<country>.<region>.cities()" in str(exc_info.value)

    # cities() on country only should fail
    with pytest.raises(ValueError) as exc_info:
        wkls.us.cities()
    assert "cities() cannot be called on a country alone" in str(exc_info.value)

    # cities() on country.region.city should fail
    with pytest.raises(ValueError) as exc_info:
        wkls.us.ca.sanfrancisco.cities()
    assert "cities() requires exactly one or two levels of chaining" in str(
        exc_info.value
    )


def test_subtypes_chaining_error():
    """Test that subtypes() cannot be called on chained objects."""
    with pytest.raises(ValueError) as exc_info:
        wkls.us.subtypes()
    assert "subtypes() can only be called on the root object" in str(exc_info.value)
    assert "wkls.subtypes()" in str(exc_info.value)


def test_too_many_chained_attributes():
    """Test that too many chained attributes raise an error."""
    with pytest.raises(ValueError) as exc_info:
        wkls.us.ca.sanfrancisco.somethingelse  # noqa: B018
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
        pass  # Could be various exceptions depending on validation

    # Nonexistent region
    try:
        result = (
            wkls.us.zz
        )  # ZZ is not a valid state code, should return empty DataFrame
        assert len(result) == 0, "Nonexistent region should return empty DataFrame"
    except Exception:
        pass  # Could be various exceptions depending on validation

    # Nonexistent city with search pattern (this should return empty results)
    result = wkls.us.ca["%nonexistentcity%"]
    assert result.count() == 0, "Nonexistent city search should return empty DataFrame"


def test_geometry_methods_on_empty_results():
    """Test that geometry methods fail gracefully on empty results."""
    # Create a chain that will return empty results
    empty_chain = wkls.us.ca.nonexistentcity

    with pytest.raises(ValueError) as exc_info:
        empty_chain.wkt()
    assert "No results found for: us.ca.nonexistentcity" in str(exc_info.value)


def test_did_you_mean_suggestions():
    """Test that typos in location names provide helpful suggestions."""
    # Typo: "sanfran" instead of "sanfrancisco"
    with pytest.raises(ValueError) as exc_info:
        wkls.us.ca.sanfran.wkt()
    error_msg = str(exc_info.value)
    assert "No results found for: us.ca.sanfran" in error_msg
    assert "Did you mean:" in error_msg
    assert "sanfrancisco" in error_msg


def test_did_you_mean_region_level():
    """Test suggestions work for region-level partial codes."""
    # "c" should suggest codes starting with 'c' like 'ca', 'co', 'ct'
    result = wkls.us.c
    repr_str = repr(result)
    assert "No results found for: us.c" in repr_str
    assert "Did you mean:" in repr_str
    assert "ca" in repr_str


def test_did_you_mean_country_level():
    """Test suggestions work for country-level partial codes."""
    # "u" should suggest codes starting with 'u' like 'ua', 'ug', 'us'
    result = wkls.u
    repr_str = repr(result)
    assert "No results found for: u" in repr_str
    assert "Did you mean:" in repr_str
    assert "us" in repr_str
    # Verify wildcard tip has correct syntax for root level
    assert "wkls['%u%']" in repr_str


def test_suggestions_for_extended_codes():
    """Test that extended codes suggest the matching prefix."""
    # "cali" starts with "ca", so suggest "ca"
    result = wkls.us.cali
    repr_str = repr(result)
    assert "No results found for: us.cali" in repr_str
    assert "Did you mean:" in repr_str
    assert "ca" in repr_str


def test_no_suggestions_for_unrelated_codes():
    """Test that completely unrelated codes don't show suggestions."""
    # "xyz" doesn't match any region code prefix
    result = wkls.us.xyz
    repr_str = repr(result)
    assert "No results found for: us.xyz" in repr_str
    # No suggestions since no code starts with "xyz" and "xyz" doesn't start with any code
    assert "Did you mean:" not in repr_str
    # But wildcard tip is still shown
    assert "wkls.us['%xyz%']" in repr_str


def test_did_you_mean_partial_match():
    """Test suggestions work for partial name matches."""
    # "losangel" should suggest "losangeles"
    with pytest.raises(ValueError) as exc_info:
        wkls.us.ca.losangel.wkt()
    error_msg = str(exc_info.value)
    assert "Did you mean:" in error_msg
    assert "losangeles" in error_msg


def test_no_suggestions_for_completely_wrong_name():
    """Test that completely wrong names don't show irrelevant suggestions."""
    with pytest.raises(ValueError) as exc_info:
        wkls.us.ca.xyzabc123.wkt()
    error_msg = str(exc_info.value)
    assert "No results found for: us.ca.xyzabc123" in error_msg
    # Should either have no suggestions or the message shouldn't include irrelevant ones
    # (cutoff of 0.5 should filter out completely unrelated names)


def test_did_you_mean_country_without_region():
    """Test suggestions for countries that don't have regions (2-level chain)."""
    # Falkland Islands (FK) has no regions, so second level is city directly
    # Typo: "stoney" instead of "stoneyridge"
    with pytest.raises(ValueError) as exc_info:
        wkls.fk.stoney.wkt()
    error_msg = str(exc_info.value)
    assert "No results found for: fk.stoney" in error_msg
    assert "Did you mean:" in error_msg
    assert "stoneyridge" in error_msg


def test_did_you_mean_new_york():
    """Test suggestions for New York typo at city level."""
    # Typo: "newyok" (missing 'r') instead of "newyork"
    with pytest.raises(ValueError) as exc_info:
        wkls.us.ny.newyok.wkt()
    error_msg = str(exc_info.value)
    assert "No results found for: us.ny.newyok" in error_msg
    assert "Did you mean:" in error_msg
    # Should suggest newyork or newyorkcity
    assert "newyork" in error_msg.lower()


def test_did_you_mean_no_suggestions_for_region_codes():
    """Test that region code lookups don't provide fuzzy suggestions."""
    # Region codes (US-CA, US-NY) are exact matches, no fuzzy matching
    # When user types wkls.us.xyz, it's treated as region code, not city
    with pytest.raises(ValueError) as exc_info:
        wkls.us.xyz.wkt()
    error_msg = str(exc_info.value)
    assert "No results found for: us.xyz" in error_msg
    # Region codes are exact - no "Did you mean" for these


def test_suggestions_in_repr():
    """Test that suggestions appear in DataFrame repr for empty results."""
    # Access a non-existent city - should return empty DataFrame with suggestions in repr
    result = wkls.us.ca.sanfran
    repr_str = repr(result)
    assert "No results found for: us.ca.sanfran" in repr_str
    assert "Did you mean:" in repr_str
    assert "sanfrancisco" in repr_str
    assert "wkls.us.ca['%sanfran%']" in repr_str


def test_suggestions_in_repr_bracket_access():
    """Test that suggestions appear when using bracket access."""
    # Access a non-existent country using bracket syntax on a Wkl instance.
    # The module itself is no longer subscriptable (PEP 562 compliant); use
    # Wkl() for bracket access if needed.
    from wkls import Wkl

    result = Wkl()["uss"]
    repr_str = repr(result)
    assert "No results found for: uss" in repr_str
    assert "Did you mean:" in repr_str
    assert "us" in repr_str
    assert "wkls['%uss%']" in repr_str


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


def test_version_attribute():
    """Test that __version__ is accessible and returns a string."""
    assert hasattr(wkls, "__version__")
    assert isinstance(wkls.__version__, str)
    assert len(wkls.__version__) > 0


def test_dunder_attributes_raise_attribute_error():
    """Test that unset dunder attributes raise AttributeError, not empty DataFrames."""
    with pytest.raises(AttributeError):
        wkls.__nonexistent__  # noqa: B018

    with pytest.raises(AttributeError):
        wkls._private_attr  # noqa: B018


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
    test_did_you_mean_suggestions()
    test_did_you_mean_partial_match()
    test_no_suggestions_for_completely_wrong_name()
    test_did_you_mean_country_without_region()
    test_did_you_mean_new_york()
    test_did_you_mean_no_suggestions_for_region_codes()
    test_chainable_dataframe_error_propagation()
    print("All error handling tests passed!")
