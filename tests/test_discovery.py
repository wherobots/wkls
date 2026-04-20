"""Tests for dataset discovery surface.

Covers how users find out what's available:

- ``dir(wkls)`` / ``dir(wkls.us)`` introspection.
- ``.search(query)`` at each chain level with subtree scope.
- Subtree-scoped listing methods (``.regions()``, ``.counties()``, ``.cities()``).
- Root-only methods: ``.countries()``, ``.dependencies()``, ``.subtypes()``,
  ``.overture_version()`` / ``.overture_releases()``.
"""

from __future__ import annotations

import os

import pytest

import wkls
from wkls import core

# ---------- dir() overrides ----------


def test_dir_root_contains_key_attrs():
    """dir(wkls) exposes the Wkl class, version, and the method surface."""
    entries = dir(wkls)
    assert "Wkl" in entries
    assert "__version__" in entries


def test_return_types_uniform():
    """Chain access, listing methods, and search all return the same type."""
    from wkls import Wkl

    assert isinstance(wkls.us, Wkl)
    assert isinstance(wkls.us.ca, Wkl)
    assert isinstance(wkls.us.ca.sanfrancisco, Wkl)
    assert isinstance(wkls.countries(), Wkl)
    assert isinstance(wkls.dependencies(), Wkl)
    assert isinstance(wkls.us.regions(), Wkl)
    assert isinstance(wkls.us.counties(), Wkl)
    assert isinstance(wkls.us.ca.cities(), Wkl)
    assert isinstance(wkls.subtypes(), Wkl)
    assert isinstance(wkls.search("united"), Wkl)
    assert isinstance(wkls.us.search("new"), Wkl)


def test_geometry_on_single_row_search_result():
    """.wkt() works on a search result that resolves to one row."""
    # "oakland" within US-CA matches exactly one locality — unambiguous,
    # so geometry resolves without raising AmbiguousLocationError.
    wkt = wkls.us.ca.search("oakland").wkt()
    assert isinstance(wkt, str)
    assert len(wkt) > 0


def test_dir_root_both_forms():
    """dir(wkls) exposes both ISO codes and names for countries."""
    entries = dir(wkls)
    assert "us" in entries
    assert "unitedstates" in entries
    assert "in" in entries
    assert "india" in entries


def test_dir_country_level_both_forms():
    """dir(wkls.us) exposes both region ISO suffixes and region names."""
    entries = dir(wkls.us)
    assert "ca" in entries
    assert "california" in entries
    assert "or" in entries
    assert "oregon" in entries
    assert "regions" in entries


def test_dir_region_level_methods_only():
    """dir(wkls.us.ca) returns methods only — no city identifiers."""
    entries = dir(wkls.us.ca)
    assert set(entries) == {"cities", "counties", "geojson", "search", "wkb", "wkt"}


def test_dir_city_level_geometry_methods_only():
    """dir on a resolved city returns geometry-output methods."""
    entries = dir(wkls.us.ca.sanfrancisco)
    assert set(entries) == {"geojson", "wkb", "wkt"}


def test_dir_cached_no_query_on_repeat(capsys):
    """Second dir() call at the same level fires zero SQL queries."""
    core._dir_cache.clear()
    os.environ["WKLS_DEBUG"] = "true"
    try:
        dir(wkls)
        first_out = capsys.readouterr().out
        dir(wkls)
        second_out = capsys.readouterr().out
    finally:
        del os.environ["WKLS_DEBUG"]

    assert "subtype IN ('country', 'dependency')" in first_out
    assert "SELECT DISTINCT" not in second_out


# ---------- .search() ----------


def test_search_root_returns_mixed_subtypes():
    """wkls.search() at root scans every subtype, not just countries."""
    df = wkls.search("san francisco").to_arrow_table()
    subtypes = {df.column("subtype")[i].as_py() for i in range(df.num_rows)}
    assert subtypes & {"locality", "localadmin", "county"}, (
        f"Expected city-like subtypes, got {subtypes}"
    )


def test_search_finds_city_from_root():
    """A root-level search for a city name returns the actual city."""
    df = wkls.search("san francisco").to_arrow_table()
    matches = [
        (df.column("country")[i].as_py(), df.column("name_primary")[i].as_py())
        for i in range(df.num_rows)
    ]
    assert ("US", "San Francisco") in matches


def test_search_country_scope_expands():
    """search() under a country finds cities, not just regions."""
    df = wkls.us.search("los angeles").to_arrow_table()
    subtypes = {df.column("subtype")[i].as_py() for i in range(df.num_rows)}
    assert subtypes & {"locality", "county"}


def test_search_country_finds_regions_too():
    """Country-level search still returns region matches alongside cities."""
    df = wkls.us.search("new").to_arrow_table()
    names_by_subtype = {
        (df.column("subtype")[i].as_py(), df.column("name_primary")[i].as_py())
        for i in range(df.num_rows)
    }
    region_names = {n for st, n in names_by_subtype if st == "region"}
    assert {"New Hampshire", "New Jersey", "New Mexico", "New York"}.issubset(
        region_names
    )


def test_search_region_returns_cities():
    """search() at region level returns matching cities/counties."""
    df = wkls.us.ca.search("san fran")
    assert df.count() >= 1
    table = df.to_arrow_table()
    names = [table.column("name_primary")[i].as_py() for i in range(table.num_rows)]
    assert any("San Francisco" in n for n in names)


def test_search_no_region_country():
    """search() on countries without regions scopes to that country."""
    df = wkls.fk.search("stanley")
    assert df is not None


def test_search_too_deep_raises():
    """search() past city level raises ValueError."""
    with pytest.raises(ValueError, match="past city level"):
        wkls.us.ca.sanfrancisco.search("foo")


def test_search_normalizes_query_to_dot_access_form():
    """search() matches the same form users type in dot-access.

    ``search("sanfrancisco")`` and ``search("san francisco")`` should
    return the same rows — both sides of the match are normalized
    (lowercased + non-alphanumerics stripped). Regression: prior to
    normalization, the spaceless form returned an empty DataFrame.
    """
    spaceless = wkls.us.ca.search("sanfrancisco").count()
    spaced = wkls.us.ca.search("san francisco").count()
    assert spaceless == spaced
    assert spaceless >= 1  # at least the SF locality


# ---------- Subtree-scoped list methods ----------


def test_regions_at_root_returns_all():
    """wkls.regions() at root returns every region worldwide."""
    assert wkls.regions().count() > 3000  # ~3,900 in practice


def test_regions_scoped_to_country():
    """wkls.us.regions() returns regions scoped to that country."""
    assert wkls.us.regions().count() == 51  # 50 states + DC


def test_india_regions_count():
    """Canonical dataset sanity check."""
    assert wkls.IN.regions().count() == 37


def test_counties_at_country_level():
    """wkls.us.counties() returns counties in the US."""
    assert wkls.us.counties().count() > 3000


def test_cities_at_country_level():
    """wkls.us.cities() returns cities in the US."""
    assert wkls.us.cities().count() > 10000


def test_counties_at_region_level():
    """wkls.us.ca.counties() returns CA counties."""
    assert wkls.us.ca.counties().count() == 58  # California has 58 counties


def test_counties_at_root_returns_all():
    """wkls.counties() at root lists every county worldwide."""
    assert wkls.counties().count() > 10000


def test_cities_at_root_returns_all():
    """wkls.cities() at root lists every city worldwide."""
    assert wkls.cities().count() > 100000


def test_no_region_country_counties_and_cities():
    """Depth-1 list methods on no-region countries (FK) still work."""
    assert wkls.fk.counties().count() >= 0
    assert wkls.fk.cities().count() >= 1


def test_fk_cities_count():
    """Canonical dataset sanity check."""
    assert wkls.fk.cities().count() == 25


# ---------- List-method depth guards ----------


def test_regions_at_region_level_returns_self():
    """regions() at region level returns the region itself — only region in scope."""
    df = wkls.us.ca.regions().to_arrow_table()
    assert df.num_rows == 1
    assert df.column("region")[0].as_py() == "US-CA"


def test_regions_past_region_level_returns_empty():
    """regions() past region level cascades via parent_id; a locality has no sub-regions."""
    assert wkls.us.ca.sanfrancisco.regions().count() == 0


def test_counties_past_region_level_returns_empty():
    """counties() past region level cascades via parent_id; a locality has no sub-counties."""
    assert wkls.us.ca.sanfrancisco.counties().count() == 0


def test_cities_at_county_level_returns_children():
    """cities() on a county returns its direct locality/localadmin children via parent_id."""
    cities = wkls.us.ca.sandiegocounty.cities()
    assert cities.count() >= 15  # San Diego County has ~19 localities
    table = cities.to_arrow_table()
    names = {table.column("name_primary")[i].as_py() for i in range(table.num_rows)}
    assert {"San Diego", "Chula Vista", "Oceanside"}.issubset(names)


def test_cities_past_region_level_on_ambiguous_raises():
    """cities() past region level requires a single-row chain scope."""
    with pytest.raises(ValueError, match="single row"):
        wkls.us.pa.franklin.cities()


# ---------- Root-only dataset methods ----------


def test_countries_at_root():
    """Canonical country count."""
    assert wkls.countries().count() == 219


def test_dependencies_at_root():
    """Canonical dependency count."""
    assert wkls.dependencies().count() == 53


def test_subtypes_at_root():
    """subtypes() at root returns the distinct set of subtypes in the dataset."""
    table = wkls.subtypes().to_arrow_table()
    subtype_values = {table.column("subtype")[i].as_py() for i in range(table.num_rows)}
    for expected in ("country", "region", "county", "locality", "localadmin"):
        assert expected in subtype_values, f"Missing subtype: {expected}"


def test_countries_hidden_on_chain():
    """countries() is root-only and hidden from chained Wkl instances."""
    assert not hasattr(wkls.us, "countries")
    with pytest.raises(AttributeError):
        wkls.us.countries()


def test_dependencies_hidden_on_chain():
    """dependencies() is root-only and hidden from chained Wkl instances."""
    assert not hasattr(wkls.us, "dependencies")
    with pytest.raises(AttributeError):
        wkls.us.dependencies()


def test_subtypes_hidden_on_chain():
    """subtypes() is root-only and hidden from chained Wkl instances."""
    assert not hasattr(wkls.us, "subtypes")
    with pytest.raises(AttributeError):
        wkls.us.subtypes()
