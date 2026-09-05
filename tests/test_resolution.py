"""Tests for chain → row resolution.

Covers how ``wkls.us.ca.sanfrancisco``-style chain access turns into a
DataFrame row, including:

- PEP 562 module import semantics (module vs. class, singleton identity).
- Name-based resolution at country and region levels.
- Country-info and region-info caches.
- Chain-depth limits and error handling for bad inputs.
"""

from __future__ import annotations

import os
import types

import pytest

import wkls
from wkls import Wkl, core


@pytest.fixture
def cold_caches():
    """Wipe the country cache so queries fire fresh."""
    core._country_info.clear()
    yield
    core._country_info.clear()


# ---------- PEP 562 module import ----------


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
    assert wkls._get_instance() is wkls._get_instance()


def test_module_private_attribute_raises():
    """Module-level __getattr__ rejects dunder/private names."""
    with pytest.raises(AttributeError):
        wkls.__nonexistent__  # noqa: B018


def test_dunder_attributes_raise_attribute_error():
    """Unset dunder attributes raise AttributeError, not empty DataFrames."""
    with pytest.raises(AttributeError):
        wkls.__nonexistent__  # noqa: B018
    with pytest.raises(AttributeError):
        wkls._private_attr  # noqa: B018


def test_version_attribute():
    """__version__ is accessible on the module."""
    assert hasattr(wkls, "__version__")
    assert isinstance(wkls.__version__, str)
    assert len(wkls.__version__) > 0


def test_repr_header_root():
    """Root Wkl() repr is a terse label."""
    assert repr(Wkl()) == "Wkl(root)"


def test_repr_header_single_row_chain():
    """A resolved single-row chain advertises path, rows, and subtype."""
    header = repr(wkls.us.ca.sanfrancisco).split("\n")[0]
    assert header.startswith("Wkl(")
    assert "path='wkls.us.ca.sanfrancisco'" in header
    assert "rows=1" in header
    assert "subtype='county'" in header


def test_repr_header_multi_row_chain():
    """An ambiguous chain uses chain= rather than path= and shows row count."""
    header = repr(wkls.us.pa.franklin).split("\n")[0]
    assert "chain='wkls.us.pa.franklin'" in header
    assert "rows=18" in header


def test_repr_header_result_mode_multi_row():
    """Multi-row results show a subtype breakdown dict."""
    header = repr(wkls.us.ca.search("san")).split("\n")[0]
    assert header.startswith("Wkl(rows=")
    assert "subtypes=" in header


def test_repr_header_empty_result():
    """Empty result-mode Wkl shows rows=0 with no subtype info."""
    header = repr(wkls.us.ca.search("zzznope")).split("\n")[0]
    assert header == "Wkl(rows=0)"


def test_module_docstring_leads_with_quickstart():
    """help(wkls) should show agents the key patterns within the first screen."""
    doc = wkls.__doc__ or ""
    assert "Quickstart:" in doc
    # Disambiguation must be advertised up front, not buried.
    assert "AmbiguousLocationError" in doc
    assert "parent narrower" in doc
    assert "by_id(" in doc
    # Intermediate ambiguity — the York-in-PA case from the agent test.
    assert "yorkcounty" in doc
    # Scoped search must be the lead example (before global .search()).
    assert "wkls.au.search" in doc or "wkls.us.tn.search" in doc
    # to_dicts mentioned as the iteration helper.
    assert "to_dicts()" in doc


# ---------- Basic chain access smoke ----------


def test_access_smoke():
    """Canonical dot-access chains return non-empty geometry."""
    assert len(wkls.us.wkt()) > 0
    assert len(wkls.us.ca.wkt()) > 0
    assert len(wkls.us.ny.newyork.wkt()) > 0
    assert len(wkls.us.ca.sanfrancisco.wkt()) > 0


def test_iso_code_still_works():
    """Existing ISO dot access continues to work unchanged."""
    assert len(wkls.us.ca.sanfrancisco.wkt()) > 0


# ---------- Name-based resolution (country + region) ----------


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
    df = wkls.austria.burgenland._resolve().to_arrow_table()
    assert df.num_rows >= 1
    assert df.column("region")[0].as_py() == "AT-1"

    df2 = wkls.japan.hokkaidoprefecture._resolve().to_arrow_table()
    assert df2.num_rows >= 1
    assert df2.column("region")[0].as_py() == "JP-01"


def test_india_maharashtra_full_chain():
    """Full name chain at country + region level returns geometry."""
    wkt = wkls.india.maharashtra.wkt()
    assert len(wkt) > 0
    assert wkt.startswith("MULTIPOLYGON") or wkt.startswith("POLYGON")


def test_diacritic_english_fallback():
    """English name resolves when name_primary carries diacritics.

    Overture 2026-08-19.0 lists name_en "Ivory Coast" for CI (earlier
    releases only had "Côte d'Ivoire", which no identifier can spell).
    Name resolution still does not accent-fold; see the São Paulo xfail.
    """
    df = wkls.ivorycoast._resolve().to_arrow_table()
    assert df.num_rows >= 1
    assert df.column("country")[0].as_py() == "CI"


@pytest.mark.xfail(
    reason=(
        "'São Paulo' ILIKE 'saopaulo' is FALSE because ILIKE is "
        "case-insensitive but not accent-insensitive."
    ),
    strict=True,
)
def test_diacritic_preserved_in_replace():
    df = wkls.brazil.saopaulo._resolve().to_arrow_table()
    assert df.num_rows >= 1
    assert df.column("region")[0].as_py() == "BR-SP"


# ---------- Region-name regression (regressed Phase 1 bug) ----------


def test_counties_via_name_chain():
    """wkls.india.maharashtra.counties() should match the ISO-chain result.

    Regression: pre-fix, name-based chains built region filters as
    'IN-MAHARASHTRA' (concatenating country_iso + chain[1].upper()),
    which matched zero rows. Must equal the ISO-chain path.
    """
    from_name = len(wkls.india.maharashtra.counties())
    from_iso = len(wkls.IN.MH.counties())
    assert from_name == from_iso
    assert from_name >= 1


def test_cities_via_name_chain():
    """Name-based chain also supports cities() symmetrically with ISO."""
    from_name = len(wkls.india.maharashtra.cities())
    from_iso = len(wkls.IN.MH.cities())
    assert from_name == from_iso
    assert from_name >= 1


# ---------- Country-info cache ----------


def test_name_iso_cache_agreement(cold_caches):
    """Name access and ISO access populate the same cache entry."""
    wkls.india.wkt()
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


# ---------- No-region countries ----------


def test_countries_without_region_returns_empty():
    """Countries with no regions return an empty DataFrame, not an error."""
    # Regions scope to a subtree; FK has no region rows, so this is zero
    # — not a special error case.
    assert len(wkls.fk.regions()) == 0


# ---------- Chain validation & error handling ----------


def test_empty_chain_error():
    """Empty chain raises on ._resolve()."""
    wkl = Wkl()
    with pytest.raises(ValueError) as exc_info:
        wkl._resolve()
    assert "No attributes in the chain" in str(exc_info.value)
    assert "wkls.<country>" in str(exc_info.value)


def test_chain_depth_4_is_parent_narrower():
    """Chain depth 4 narrows by parent_id."""
    # Adams County in PA has a Franklin township — single match.
    assert wkls.us.pa.adamscounty.franklin._resolve().count() == 1


def test_too_many_chained_attributes():
    """Chain past level 4 raises."""
    with pytest.raises(ValueError, match="Chain too deep"):
        wkls.us.ca.sanfrancisco.mission.somethingelse  # noqa: B018


def test_nonexistent_location_returns_empty():
    """Unknown identifiers return empty DataFrames, not exceptions."""
    # ZZ is not a valid country code
    assert wkls.zz._resolve().count() == 0

    # ZZ is not a valid US state code
    assert wkls.us.zz._resolve().count() == 0


# ---------- Attribute spellings agents actually use ----------


def test_underscored_place_name_resolves():
    """``san_francisco`` is the natural spelling; separators are stripped."""
    assert wkls.us.ca.san_francisco.to_dicts()[0]["name_en"] == "San Francisco"
    assert wkls.us.ca.san_francisco.path == wkls.us.ca.sanfrancisco.path


def test_root_country_aliases():
    """Colloquial codes that are not ISO alpha-2 resolve at the root."""
    assert wkls.uk.to_dicts()[0]["country"] == "GB"
    assert wkls.usa.to_dicts()[0]["country"] == "US"
    assert wkls.uae.to_dicts()[0]["country"] == "AE"


def test_depth3_search_scope_is_children_even_after_repr():
    """Regression: a cached ``_df`` made search narrow the county row itself."""
    fresh = len(wkls.us.pa.yorkcounty.search("york"))
    w = wkls.us.pa.yorkcounty
    repr(w)
    assert len(w.search("york")) == fresh > 1
