"""Golden tests for Phase 1 LLM/agent usability work.

Covers PEP 562 dual import, name-based country/region resolution and
the _country_info cache, and docstring smoke coverage.
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
