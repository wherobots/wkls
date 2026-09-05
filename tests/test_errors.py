"""Tests for error messages, "Did you mean?" suggestions, and empty-result hints.

When a chain doesn't resolve or a typo leads to a near-miss, `wkls`
produces suggestions (Levenshtein-based for city names, prefix-based
for ISO codes) and a tip pointing at ``.search()``. This file covers
that surface.
"""

from __future__ import annotations

import pytest

import wkls

# ---------- "Did you mean?" suggestions ----------


def test_did_you_mean_city_typo():
    """Typo in a city name surfaces suggestions via Levenshtein."""
    with pytest.raises(ValueError) as exc_info:
        wkls.us.ca.sanfran.wkt()
    msg = str(exc_info.value)
    assert "No results found for: us.ca.sanfran" in msg
    assert "Did you mean:" in msg
    assert "sanfrancisco" in msg


def test_did_you_mean_city_partial_match():
    """Partial name matches suggest the full name."""
    with pytest.raises(ValueError) as exc_info:
        wkls.us.ca.losangel.wkt()
    msg = str(exc_info.value)
    assert "Did you mean:" in msg
    assert "losangeles" in msg


def test_did_you_mean_new_york_typo():
    """Missing-letter typo suggests the correct spelling."""
    with pytest.raises(ValueError) as exc_info:
        wkls.us.ny.newyok.wkt()
    msg = str(exc_info.value)
    assert "No results found for: us.ny.newyok" in msg
    assert "Did you mean:" in msg
    assert "newyork" in msg.lower()


def test_did_you_mean_country_level():
    """Partial country prefixes suggest matching ISO codes."""
    result = wkls.u
    repr_str = repr(result)
    assert "No results found for: u" in repr_str
    assert "Did you mean:" in repr_str
    assert "us" in repr_str


def test_did_you_mean_region_level():
    """Partial region prefixes suggest matching region ISO suffixes."""
    result = wkls.us.c
    repr_str = repr(result)
    assert "No results found for: us.c" in repr_str
    assert "Did you mean:" in repr_str
    assert "ca" in repr_str


def test_did_you_mean_extended_code_suggests_prefix():
    """Extending a valid prefix (e.g., 'cali') suggests the prefix itself."""
    result = wkls.us.cali
    repr_str = repr(result)
    assert "No results found for: us.cali" in repr_str
    assert "Did you mean:" in repr_str
    assert "ca" in repr_str


def test_did_you_mean_no_region_country():
    """Suggestions work for 2-level chains on no-region countries like FK."""
    with pytest.raises(ValueError) as exc_info:
        wkls.fk.stoney.wkt()
    msg = str(exc_info.value)
    assert "No results found for: fk.stoney" in msg
    assert "Did you mean:" in msg
    assert "stoneyridge" in msg


# ---------- No-suggestions paths ----------


def test_no_suggestions_for_unrelated_region_code():
    """xyz doesn't prefix-match any US region, so no 'Did you mean?'."""
    result = wkls.us.xyz
    repr_str = repr(result)
    assert "No results found for: us.xyz" in repr_str
    assert "Did you mean:" not in repr_str
    # But the .search() tip is still shown
    assert "wkls.us.search('xyz')" in repr_str


def test_no_suggestions_for_unrelated_city():
    """Completely-wrong city names don't surface irrelevant suggestions."""
    with pytest.raises(ValueError) as exc_info:
        wkls.us.ca.xyzabc123.wkt()
    msg = str(exc_info.value)
    assert "No results found for: us.ca.xyzabc123" in msg


def test_region_codes_do_not_get_fuzzy_suggestions():
    """Region codes (wkls.us.xyz) use prefix match only, not fuzzy."""
    with pytest.raises(ValueError) as exc_info:
        wkls.us.xyz.wkt()
    msg = str(exc_info.value)
    assert "No results found for: us.xyz" in msg


# ---------- Empty-result hint shows .search() ----------


def test_error_hint_uses_search_not_brackets():
    """Empty-result hint points at .search(), not bracket wildcards."""
    repr_str = repr(wkls.us.ca.nonexistentcity)
    assert "wkls.us.ca.search('nonexistentcity')" in repr_str
    assert "['%" not in repr_str


def test_error_hint_at_root_uses_search():
    """Root-level empty result hints at wkls.search()."""
    repr_str = repr(wkls.zz)
    assert "wkls.search('zz')" in repr_str


def test_error_hint_at_country_level_uses_search():
    """Country-level empty result hints at wkls.<country>.search()."""
    repr_str = repr(wkls.us.xyz)
    assert "wkls.us.search('xyz')" in repr_str


def test_suggestions_show_in_empty_repr():
    """Non-raising empty chains include suggestions + search tip in repr."""
    result = wkls.us.ca.sanfran
    repr_str = repr(result)
    assert "No results found for: us.ca.sanfran" in repr_str
    assert "Did you mean:" in repr_str
    assert "sanfrancisco" in repr_str
    assert "wkls.us.ca.search('sanfran')" in repr_str


# NOTE: The pre-v1.2 companion test for bracket access on a Wkl instance
# (`Wkl()["uss"]` returning a Wkl with a "Did you mean:" hint in repr) was
# removed when the bracket-access shim was replaced with a TypeError in
# v1.2.0. TypeError coverage lives in tests/test_deprecation.py.


# ---------- Error propagation on chained Wkl ----------


def test_chainable_df_hides_root_only_config_methods():
    """Config methods (configure, overture_*) are hidden on chained Wkls."""
    us = wkls.us
    for name in ("configure", "overture_version", "overture_releases"):
        assert not hasattr(us, name), f"{name} should be hidden on chained Wkl"


# ---------- Wrong-guess paths ----------


def test_call_on_misspelled_method_names_the_closest_method():
    """``wkls.us.wktt()`` names ``.wkt()`` instead of a bare 'not callable'."""
    with pytest.raises(TypeError, match=r"Did you mean \.wkt\(\)"):
        wkls.us.wktt()
    with pytest.raises(TypeError, match=r"Did you mean \.countries\(\)"):
        wkls.countrie()


def test_call_on_a_real_place_says_drop_the_parentheses():
    with pytest.raises(TypeError, match=r"drop the parentheses \(wkls\.us\)"):
        wkls.us()


def test_field_attribute_redirects_to_to_dicts():
    """Row fields are read via to_dicts(), and the error says so."""
    row = wkls.us.tn.search("franklin")[0]
    with pytest.raises(AttributeError, match=r"to_dicts\(\)\[0\]\['name_primary'\]"):
        _ = row.name_primary
    with pytest.raises(AttributeError, match=r"to_dicts\(\)\[0\]\['name_en'\]"):
        _ = row.name
    with pytest.raises(AttributeError, match=r"\.wkt\(\)"):
        _ = row.geometry


def test_search_rejects_non_string_and_empty_queries():
    with pytest.raises(TypeError, match="str query"):
        wkls.search(123)
    with pytest.raises(ValueError, match="non-empty"):
        wkls.search("")
    with pytest.raises(ValueError, match="non-empty"):
        wkls.us.search("  - ")


def test_search_on_an_empty_scope_raises_the_chain_hint():
    """``wkls.zz.search('x')`` used to return a silent ``Wkl(rows=0)``."""
    with pytest.raises(ValueError, match="No results found for: zz"):
        wkls.zz.search("london")
    with pytest.raises(ValueError, match="No results found for: us.ca.sanfransisco"):
        wkls.us.ca.sanfransisco.search("x")


def test_far_suggestions_are_dropped():
    """Only near misses are suggested; 'sananselmo' is five edits away."""
    r = repr(wkls.us.ca.sanfransisco)
    assert "sanfrancisco" in r
    assert "sananselmo" not in r
