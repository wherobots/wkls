"""Tests for the bracket-access deprecation shim.

``Wkl.__getitem__`` still works for backward compatibility but emits a
``DeprecationWarning`` pointing at the modern replacement (dot access
or ``.search()``). DataFrame-style indexing (list/slice keys) is
unaffected — it does not emit the warning.
"""

from __future__ import annotations

import warnings

import pytest

import wkls
from wkls import Wkl


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


def test_chained_bracket_emits_deprecation():
    """Bracket access on a chained Wkl also warns."""
    with pytest.warns(DeprecationWarning, match="Bracket access is deprecated"):
        wkls.us["CA"]


def test_list_style_index_does_not_warn():
    """DataFrame-style list indexing does NOT emit the deprecation warning."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        try:
            wkls.us.ca[["id", "country"]]
        except DeprecationWarning:
            pytest.fail("DataFrame-style indexing should not emit DeprecationWarning")
        except Exception:
            # Any other exception is fine — we're only testing the warning surface.
            pass
