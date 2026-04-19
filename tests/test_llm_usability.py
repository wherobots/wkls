"""Golden tests for LLM/agent usability work.

Covers PEP 562 dual import, name-based country/region resolution,
the _country_info cache, docstring smoke coverage, __dir__ overrides,
.search(), and deprecation of bracket access.
"""

from __future__ import annotations

import os
import types
import warnings

import pytest

import wkls
from wkls import Wkl, core


@pytest.fixture
def cold_caches():
    """Wipe the country cache so queries fire fresh."""
    core._country_info.clear()
    yield
    core._country_info.clear()


# ---------- Stream 5: PEP 562 dual import ----------


def test_module_type():
    """`wkls` is a real ModuleType, not a Wkl instance."""
    assert isinstance(wkls, types.ModuleType)
    assert type(wkls) is types.ModuleType


def test_explicit_import():
    """`from wkls import Wkl` gives the class, instantiable directly."""
    wkl = Wkl()
    assert isinstance(wkl, Wkl)
    assert len(wkl.us.ca.sanfrancisco.wkt()) > 0


def test_singleton_identity():
    """The lazy module singleton is stable across accesses."""
    assert core is wkls.core
    first = wkls._get_instance()
    second = wkls._get_instance()
    assert first is second


def test_module_dir_contains_key_attrs():
    """dir(wkls) exposes classes, version, and Wkl attributes."""
    entries = dir(wkls)
    assert "Wkl" in entries
    assert "ChainableDataFrame" in entries
    assert "__version__" in entries


def test_module_private_attribute_raises():
    """Module-level __getattr__ rejects dunder/private names."""
    with pytest.raises(AttributeError):
        wkls.__nonexistent__  # noqa: B018


# ---------- Stream 1: name-based resolution ----------


def test_keyword_country_names():
    """Python-keyword ISO codes resolve by name (IN, IS, AS)."""
    assert len(wkls.india.wkt()) > 0
    assert len(wkls.iceland.wkt()) > 0
    assert len(wkls.americansamoa.wkt()) > 0


def test_keyword_region_names():
    """Python-keyword region suffixes resolve by name (OR, IN, AS)."""
    assert len(wkls.us.oregon.wkt()) > 0
    assert len(wkls.us.indiana.wkt()) > 0
    assert len(wkls.spain.asturias.wkt()) > 0


def test_numeric_region_names():
    """Regions with numeric ISO suffixes resolve by name.

    Japan uses "Hokkaido Prefecture" as name_en, so the user must type
    the full suffix — ILIKE is not a substring match.
    """
    df = wkls.austria.burgenland._df.to_arrow_table()
    assert df.num_rows >= 1
    assert df.column("region")[0].as_py() == "AT-1"

    df2 = wkls.japan.hokkaidoprefecture._df.to_arrow_table()
    assert df2.num_rows >= 1
    assert df2.column("region")[0].as_py() == "JP-01"


@pytest.mark.xfail(
    reason=(
        "Phase 1 does not accent-fold: Overture's name_en for Côte d'Ivoire "
        "contains diacritics and punctuation (ô, apostrophe) that cannot be "
        "typed as a Python identifier. Needs a name_normalized column or "
        "SQL-side accent stripping — tracked as a future enhancement."
    ),
    strict=True,
)
def test_diacritic_english_fallback():
    df = wkls.ivorycoast._df.to_arrow_table()
    assert df.num_rows >= 1
    assert df.column("country")[0].as_py() == "CI"


@pytest.mark.xfail(
    reason=(
        "Phase 1 does not accent-fold: 'São Paulo' ILIKE 'saopaulo' is "
        "FALSE because ILIKE is case-insensitive but not accent-insensitive. "
        "Same follow-up as the Côte d'Ivoire case."
    ),
    strict=True,
)
def test_diacritic_preserved_in_replace():
    df = wkls.brazil.saopaulo._df.to_arrow_table()
    assert df.num_rows >= 1
    assert df.column("region")[0].as_py() == "BR-SP"


def test_iso_code_still_works():
    """Existing ISO dot access continues to work unchanged."""
    assert len(wkls.us.ca.sanfrancisco.wkt()) > 0


def test_india_maharashtra_full_chain():
    """Full name chain at country + region level returns geometry."""
    wkt = wkls.india.maharashtra.wkt()
    assert len(wkt) > 0
    assert wkt.startswith("MULTIPOLYGON") or wkt.startswith("POLYGON")


# ---------- Cache correctness ----------


def test_name_iso_cache_agreement(cold_caches):
    """Name access and ISO access populate the same cache entry."""
    wkls.india.wkt()  # triggers cache population
    assert ("IN", True) == core._country_info["in"]
    assert ("IN", True) == core._country_info["india"]


def test_cache_shared_across_instantiations(cold_caches):
    """A Wkl built from a name sees the same canonical ISO as one from the ISO."""
    by_name = Wkl(["india"])
    by_iso = Wkl(["IN"])
    assert by_name._country_iso == by_iso._country_iso == "IN"
    assert by_name._has_region == by_iso._has_region is True


def test_cache_query_count(cold_caches, capsys):
    """Second access fires zero lookup queries."""
    os.environ["WKLS_DEBUG"] = "true"
    try:
        wkls.us.ca.sanfrancisco.wkt()
        first_out = capsys.readouterr().out

        wkls.us.ca.sanfrancisco.wkt()
        second_out = capsys.readouterr().out
    finally:
        del os.environ["WKLS_DEBUG"]

    assert "SELECT DISTINCT country AS iso" in first_out  # COUNTRY_LOOKUP
    assert "SELECT DISTINCT country AS iso" not in second_out
    # COUNTRY_HAS_REGIONS query string
    assert (
        "SELECT * FROM wkls\n    WHERE country = 'US'\n      AND subtype = 'region'"
        not in second_out
    )


# ---------- Stream 3: __dir__ overrides ----------


def test_dir_root_both_forms():
    """dir(wkls) exposes both ISO codes and names for countries."""
    entries = dir(wkls)
    assert "us" in entries
    assert "unitedstates" in entries
    assert "in" in entries
    assert "india" in entries
    assert "Wkl" in entries  # module attrs still present


def test_dir_country_level_both_forms():
    """dir(wkls.us) exposes both region ISO suffixes and region names."""
    entries = dir(wkls.us)
    assert "ca" in entries
    assert "california" in entries
    assert "or" in entries
    assert "oregon" in entries
    assert "regions" in entries  # method still present


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


# ---------- Stream 2: .search() ----------


def test_search_root_returns_mixed_subtypes():
    """wkls.search() at root scans every subtype, not just countries."""
    df = wkls.search("san francisco").to_arrow_table()
    subtypes = {df.column("subtype")[i].as_py() for i in range(df.num_rows)}
    # There's no country named San Francisco but many cities and counties;
    # the result should include city-like subtypes.
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
    """search() under a country now finds cities, not just regions."""
    df = wkls.us.search("los angeles").to_arrow_table()
    subtypes = {df.column("subtype")[i].as_py() for i in range(df.num_rows)}
    # Expect the locality + county + any region-level match.
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
    # FK has no regions; depth-1 search filters by country only.
    assert df is not None


def test_search_too_deep_raises():
    """search() past city level raises ValueError."""
    with pytest.raises(ValueError, match="past city level"):
        wkls.us.ca.sanfrancisco.search("foo")


# ---------- Region-name resolution regression ----------


def test_counties_via_name_chain():
    """wkls.india.maharashtra.counties() should return the same count as IN-MH."""
    assert wkls.india.maharashtra.counties().count() >= 1
    # Matches the ISO path — both identifiers resolve to IN-MH.
    from_name = wkls.india.maharashtra.counties().count()
    from_iso = wkls.IN.MH.counties().count()
    assert from_name == from_iso


def test_cities_via_name_chain():
    """Name-based chain supports cities()."""
    from_name = wkls.india.maharashtra.cities().count()
    from_iso = wkls.IN.MH.cities().count()
    assert from_name == from_iso >= 1


# ---------- __getitem__ deprecation ----------


def test_bracket_access_emits_deprecation():
    """Wkl()[...] still works but emits a DeprecationWarning."""
    with pytest.warns(DeprecationWarning, match="Bracket access is deprecated"):
        result = Wkl()["IN"]
    assert result._df.to_arrow_table().column("country")[0].as_py() == "IN"


def test_bracket_wildcard_emits_deprecation_pointing_to_search():
    """Wildcard bracket access warns and suggests .search()."""
    with pytest.warns(
        DeprecationWarning, match=r"wildcards is deprecated; use \.search\('fran'\)"
    ):
        result = wkls.us.ca["%fran%"]
    assert result.count() >= 1


def test_chainable_bracket_emits_deprecation():
    """ChainableDataFrame[...] also warns."""
    with pytest.warns(DeprecationWarning, match="Bracket access is deprecated"):
        wkls.us["CA"]


def test_chainable_list_index_does_not_warn():
    """DataFrame-style list indexing on ChainableDataFrame does NOT emit the warning."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        # A plain DataFrame-column list select should not trip the location-access warning.
        # If this raises, it's the underlying DataFrame behavior, not our deprecation.
        try:
            wkls.us.ca[["id", "country"]]
        except DeprecationWarning:
            pytest.fail("DataFrame-style indexing should not emit DeprecationWarning")
        except Exception:
            pass  # Any other exception is fine — we're only testing the warning.


# ---------- Error-hint migration ----------


def test_error_hint_uses_search_not_brackets():
    """Empty-result hint points at .search(), not bracket wildcards."""
    repr_str = repr(wkls.us.ca.nonexistentcity)
    assert "wkls.us.ca.search('nonexistentcity')" in repr_str
    assert "['%" not in repr_str


def test_error_hint_at_root_uses_search():
    """Root-level empty result hints at wkls.search()."""
    repr_str = repr(wkls.zz)
    assert "wkls.search('zz')" in repr_str
