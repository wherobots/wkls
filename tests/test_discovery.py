"""Tests for dataset discovery surface.

Covers how users find out what's available:

- ``dir(wkls)`` / ``dir(wkls.us)`` introspection.
- ``.search(query)`` at each chain level with subtree scope.
- Scoped listing methods — ``.countries()``, ``.dependencies()``,
  ``.regions()``, ``.counties()``, ``.cities()``, ``.subtypes()`` —
  all narrow with chain depth, inclusive of self where subtype matches.
- Root-only config methods: ``.overture_version()`` / ``.overture_releases()``.
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
    # Pager-free guide entry point — must be discoverable via dir() so
    # agents find it without reading the module source.
    assert "help" in entries


def test_wkls_help_prints_guide_without_pager(capsys):
    """wkls.help() prints the module docstring to stdout.

    Agents running `python -c "import wkls; help(wkls)"` get stuck on
    the terminal pager. wkls.help() is the non-interactive equivalent
    and should exercise a plain ``print(__doc__)`` path.
    """
    wkls.help()
    out = capsys.readouterr().out
    assert "Quickstart:" in out
    assert "wkls.us.ca.sanfrancisco.wkt()" in out
    # A clue that users can find the pager-free path.
    assert "wkls.help()" in out or "print(wkls.__doc__)" in out


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
    assert set(entries) == {
        "cities",
        "counties",
        "geojson",
        "parent",
        "path",
        "search",
        "wkb",
        "wkt",
    }


def test_dir_city_level_exposes_geometry_and_navigation():
    """dir on a resolved city returns geometry methods + hierarchy navigation."""
    entries = dir(wkls.us.ca.sanfrancisco)
    assert set(entries) == {"geojson", "parent", "path", "wkb", "wkt"}


def test_dir_country_level_has_path_but_not_parent():
    """Countries are at the top of the hierarchy — .parent raises, .path doesn't."""
    entries = set(dir(wkls.us))
    assert "path" in entries
    assert "parent" not in entries


def test_dir_result_mode_multi_row_exposes_inspection_and_subtype_narrowers():
    """dir() on an ambiguous result shows DataFrame inspection verbs plus
    the listing methods that narrow by subtype.

    Geometry methods stay omitted because they'd raise on multi-row. Raw
    subtype names (``country``, ``locality``, …) are *not* advertised —
    the narrower is the method (``cities``, ``counties``, …), not the
    subtype attribute.
    """
    entries = set(dir(wkls.us.ca.search("san francisco")))
    assert {"count", "head", "to_arrow_table", "to_dicts"}.issubset(entries)
    assert entries.isdisjoint({"wkt", "wkb", "geojson"})  # geometry would raise
    # Listing narrowers ARE advertised — they're the discovery path for
    # agents hitting AmbiguousLocationError in result-mode.
    assert {
        "cities",
        "counties",
        "countries",
        "dependencies",
        "regions",
        "subtypes",
    }.issubset(entries)
    # Raw subtype names are not — dot-access does not accept them as filters.
    for subtype in (
        "country",
        "dependency",
        "region",
        "county",
        "locality",
        "localadmin",
    ):
        assert subtype not in entries, (
            f"dir() should not advertise subtype '{subtype}' as a narrower"
        )


def test_dir_result_mode_single_row_exposes_geometry_and_nav():
    """dir() on a single-row result shows geometry, navigation, and df verbs."""
    entries = set(dir(wkls.us.ca.search("oakland")))
    assert {"wkt", "wkb", "geojson"}.issubset(entries)
    assert {"path", "parent"}.issubset(entries)
    assert {"count", "head", "to_arrow_table"}.issubset(entries)


def test_dir_result_mode_empty_result():
    """dir() on an empty result exposes only DataFrame inspection verbs."""
    entries = set(dir(wkls.us.ca.search("zzzzznope")))
    assert entries == {
        "count",
        "head",
        "limit",
        "show",
        "to_arrow_table",
        "to_dicts",
    }


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


def test_search_chains_narrow_within_prior_result():
    """A second ``.search()`` on a result-mode Wkl narrows within the
    prior rows rather than firing a fresh global scan.

    Regression: ``wkls.us.ca.search('san').search('san francisco')``
    used to bail out of the chain and run SEARCH_ROOT, returning
    global matches from every country. After the fix it stays within
    the US/CA scope.
    """
    narrowed = wkls.us.ca.search("san").search("san francisco")
    tbl = narrowed.to_arrow_table()
    countries = {tbl.column("country")[i].as_py() for i in range(tbl.num_rows)}
    regions = {tbl.column("region")[i].as_py() for i in range(tbl.num_rows)}
    # Must be a subset of the prior scope (US/CA).
    assert countries == {"US"}
    assert regions == {"US-CA"}
    # Must match the single-call equivalent (same final scope, same query).
    single = wkls.us.ca.search("san francisco").count()
    assert narrowed.count() == single


def test_search_chains_on_root_result_still_narrows():
    """A chained search on a root-level result narrows within it too."""
    first = wkls.search("francisco")  # root-level, many countries
    second = first.search("san")
    # Every row in `second` must also be in `first` — narrowing, not
    # re-scanning.
    first_ids = {
        first.to_arrow_table().column("id")[i].as_py() for i in range(first.count())
    }
    second_ids = {
        second.to_arrow_table().column("id")[i].as_py() for i in range(second.count())
    }
    assert second_ids.issubset(first_ids)
    assert second.count() < first.count()


# ---------- Listing methods narrow on result-mode ----------


def test_counties_narrows_on_result_mode():
    """counties() on a result-mode Wkl filters the prior rows to counties.

    Regression: previously fired a fresh global scan, returning tens
    of thousands of counties unrelated to the prior search.
    """
    prior = wkls.us.search("san")
    assert prior.count() > 1
    narrowed = prior.counties()
    prior_ids = {
        prior.to_arrow_table().column("id")[i].as_py() for i in range(prior.count())
    }
    tbl = narrowed.to_arrow_table()
    for i in range(tbl.num_rows):
        assert tbl.column("subtype")[i].as_py() == "county"
        assert tbl.column("id")[i].as_py() in prior_ids


def test_cities_narrows_on_result_mode():
    """cities() on a result-mode Wkl filters to locality/localadmin rows."""
    prior = wkls.us.search("san")
    narrowed = prior.cities()
    subtypes = {
        narrowed.to_arrow_table().column("subtype")[i].as_py()
        for i in range(narrowed.count())
    }
    assert subtypes.issubset({"locality", "localadmin"})


def test_countries_narrows_on_result_mode():
    """countries() on a result-mode Wkl filters to country rows in the prior result."""
    # A name that doesn't match any country — should narrow to empty.
    assert wkls.search("franklin").countries().count() == 0
    # A name that does — should include at least that country.
    united = wkls.search("united").countries()
    tbl = united.to_arrow_table()
    subtypes = {tbl.column("subtype")[i].as_py() for i in range(tbl.num_rows)}
    assert subtypes == {"country"}
    assert united.count() >= 1


def test_dependencies_narrows_on_result_mode():
    """dependencies() on a result-mode Wkl filters to dependency rows."""
    result = wkls.search("united").dependencies()
    tbl = result.to_arrow_table()
    for i in range(tbl.num_rows):
        assert tbl.column("subtype")[i].as_py() == "dependency"


def test_subtypes_narrows_on_result_mode():
    """subtypes() on a result-mode Wkl lists distinct subtypes in the prior rows."""
    result = wkls.us.search("san").subtypes()
    vals = {
        result.to_arrow_table().column("subtype")[i].as_py()
        for i in range(result.count())
    }
    # Must be a subset of real subtypes (no spurious global ones).
    assert vals <= {
        "country",
        "dependency",
        "region",
        "county",
        "locality",
        "localadmin",
    }
    assert len(vals) >= 1


def test_config_methods_still_hidden_on_result_mode():
    """configure / overture_* are genuinely global — still hidden on result-mode."""
    r = wkls.search("san")
    for name in ("configure", "overture_version", "overture_releases"):
        assert not hasattr(r, name), f"{name} should be hidden on result-mode"


# ---------- to_dicts() ----------


def test_to_dicts_returns_list_of_dicts():
    """to_dicts() returns plain dict rows ready for Python iteration."""
    rows = wkls.us.ca.counties().to_dicts()
    assert isinstance(rows, list)
    assert rows and isinstance(rows[0], dict)
    # Schema we document — these keys exist on every row.
    for key in ("id", "country", "region", "subtype", "name_primary"):
        assert key in rows[0], f"missing key {key!r}"


def test_to_dicts_enables_simple_filtering():
    """The use case: filter a multi-row search result in pure Python."""
    non_us = [r for r in wkls.search("franklin").to_dicts() if r["country"] != "US"]
    # At time of writing there are Franklins in AU/CA/NZ.
    assert len(non_us) >= 1
    assert all(r["country"] != "US" for r in non_us)


def test_to_dicts_in_result_mode_dir():
    """dir() on a multi-row result surfaces to_dicts alongside other df verbs."""
    entries = set(dir(wkls.us.ca.search("san")))
    assert "to_dicts" in entries


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


def test_counties_past_region_level_self_inclusive():
    """counties() past region level includes self if self is a county.

    Overture models San Francisco as a county (consolidated
    city-county), so .counties() on it returns [SF].
    """
    result = wkls.us.ca.sanfrancisco.counties()
    assert result.count() == 1
    assert result.to_arrow_table().column("name_primary")[0].as_py() == "San Francisco"


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


def test_countries_scope_narrows_to_self_on_country_chain():
    """wkls.us.countries() returns [US] — the country that contains the chain."""
    result = wkls.us.countries()
    assert result.count() == 1
    tbl = result.to_arrow_table()
    assert tbl.column("country")[0].as_py() == "US"
    assert tbl.column("subtype")[0].as_py() == "country"


def test_countries_scope_narrows_to_self_past_country_chain():
    """countries() at region or city depth still returns the containing country."""
    assert wkls.us.ca.countries().count() == 1
    assert wkls.us.ca.sanfrancisco.countries().count() == 1


def test_dependencies_scope_empty_on_country_chain():
    """wkls.us.dependencies() returns [] — US is a country, no dependencies in scope."""
    assert wkls.us.dependencies().count() == 0


def test_dependencies_scope_narrows_to_self_on_dependency_chain():
    """wkls.pr.dependencies() returns [PR] — PR's subtype is 'dependency'."""
    result = wkls.pr.dependencies()
    assert result.count() == 1
    tbl = result.to_arrow_table()
    assert tbl.column("country")[0].as_py() == "PR"
    assert tbl.column("subtype")[0].as_py() == "dependency"


def test_subtypes_scope_narrows_to_country():
    """wkls.us.subtypes() lists distinct subtypes present in the US."""
    tbl = wkls.us.subtypes().to_arrow_table()
    values = {tbl.column("subtype")[i].as_py() for i in range(tbl.num_rows)}
    # US has country + region + county + locality at minimum.
    assert {"country", "region", "county", "locality"}.issubset(values)


def test_subtypes_scope_narrows_to_region():
    """wkls.us.ca.subtypes() lists distinct subtypes present in CA."""
    tbl = wkls.us.ca.subtypes().to_arrow_table()
    values = {tbl.column("subtype")[i].as_py() for i in range(tbl.num_rows)}
    # CA is itself a region; its subtree adds county + locality + localadmin.
    assert "region" in values
    assert values & {"county", "locality", "localadmin"}


def test_subtypes_no_region_scope():
    """Entities without regions in their subtree (e.g. Falklands) don't list 'region'.

    FK is itself a dependency, so its subtype scope is {dependency, locality}.
    """
    tbl = wkls.fk.subtypes().to_arrow_table()
    values = {tbl.column("subtype")[i].as_py() for i in range(tbl.num_rows)}
    assert "region" not in values
    assert "dependency" in values  # FK is a dependency
    assert "locality" in values  # FK has localities


def test_subtypes_past_region_level_requires_single_row():
    """subtypes() past region level matches the other list-method single-row rule."""
    with pytest.raises(ValueError, match="single row"):
        wkls.us.pa.franklin.subtypes()


def test_cities_at_locality_level_includes_self():
    """cities() on a single-row locality chain includes self.

    Consistency: ``regions()`` at region level returns the region itself;
    ``cities()`` at locality level should return the locality itself.
    Oakland is a locality (San Francisco is modelled as a county in
    Overture, so it's tested via counties() instead).
    """
    oakland = wkls.us.ca.alamedacounty.oakland
    cities = oakland.cities()
    assert cities.count() >= 1
    names = {
        cities.to_arrow_table().column("name_primary")[i].as_py()
        for i in range(cities.count())
    }
    assert "Oakland" in names


def test_counties_at_county_level_includes_self():
    """counties() on a single-row county chain returns the county itself."""
    sf = wkls.us.ca.sanfrancisco
    # Overture models SF as a county (consolidated city-county).
    counties = sf.counties()
    assert counties.count() == 1
    tbl = counties.to_arrow_table()
    assert tbl.column("name_primary")[0].as_py() == "San Francisco"
    assert tbl.column("subtype")[0].as_py() == "county"
