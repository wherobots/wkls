"""Tests for the Surface 2 Python collection protocol on ``Wkl``.

Covers the v1.3.0 changes:

- Collection dunders (``__len__``, ``__bool__``, ``__iter__``,
  ``__getitem__``, ``__contains__``).
- Iteration yields single-row ``Wkl`` instances that support ``.wkt()``,
  ``.path``, ``.parent``, etc.
- ``.head(n)`` / ``.limit(n)`` now return ``Wkl`` (was: sedona DataFrame).
- ``__getattr__`` passthrough narrowed to the allowlist in
  ``_DIR_DATAFRAME_METHODS``. ``.filter`` / ``.select`` / etc. raise
  ``AttributeError`` pointing at ``.resolve()``.
- Bracket string subscript raises ``TypeError`` (was: ``DeprecationWarning``
  shim for chain access / wildcard).

See the v1.3.0 release notes for the full contract this test suite
validates.
"""

from __future__ import annotations

import pyarrow as pa
import pytest
import sedonadb

import wkls
from wkls import Wkl

# ---------- __len__ / __bool__ ----------


def test_len_on_chain_mode():
    """len() on a chain-mode listing returns the expected count."""
    assert len(wkls.us.ca.counties()) >= 1


def test_len_matches_count():
    """len(wkl) and wkl.count() must agree."""
    counties = wkls.us.ca.counties()
    assert len(counties) == counties.count()


def test_len_on_empty_result():
    """len() on a zero-row search result is 0."""
    assert len(wkls.us.ca.search("zzznope-no-such-place")) == 0


def test_len_on_root_raises():
    """len(Wkl()) raises TypeError pointing at dot access."""
    with pytest.raises(TypeError, match="Root Wkl has no rows"):
        len(Wkl())


def test_bool_nonempty():
    assert bool(wkls.us.ca.counties()) is True


def test_bool_empty():
    assert bool(wkls.us.ca.search("zzznope-no-such-place")) is False


def test_bool_on_root_raises():
    with pytest.raises(TypeError, match="Root Wkl has no rows"):
        bool(Wkl())


# ---------- __iter__ ----------


def test_iter_yields_wkls():
    """Every item yielded by iteration is a ``Wkl`` instance."""
    assert all(isinstance(x, Wkl) for x in wkls.us.ca.counties())


def test_iter_count_matches_len():
    """Iterator exhausts the full set of rows."""
    counties = wkls.us.ca.counties()
    assert len(list(counties)) == len(counties)


def test_iter_rows_are_single_row():
    """Each yielded row is a single-row Wkl usable for geometry."""
    counties = wkls.us.ca.counties()
    first = next(iter(counties))
    assert len(first) == 1
    wkt = first.wkt()
    assert isinstance(wkt, str)
    assert len(wkt) > 0


def test_iter_on_root_raises():
    with pytest.raises(TypeError, match="Root Wkl has no rows"):
        iter(Wkl())


def test_iter_on_empty_result_yields_nothing():
    assert list(wkls.us.ca.search("zzznope-no-such-place")) == []


# ---------- __getitem__(int) ----------


def test_getitem_int_returns_single_row_wkl():
    row = wkls.us.ca.counties()[0]
    assert isinstance(row, Wkl)
    assert len(row) == 1


def test_getitem_negative_index():
    counties = wkls.us.ca.counties()
    assert (
        wkls.us.ca.counties()[-1].to_dicts() == counties[len(counties) - 1].to_dicts()
    )


def test_getitem_int_out_of_range():
    with pytest.raises(IndexError, match="out of range"):
        wkls.us.ca.counties()[999]


def test_getitem_int_negative_out_of_range():
    with pytest.raises(IndexError, match="out of range"):
        wkls.us.ca.counties()[-999]


def test_getitem_on_root_raises():
    with pytest.raises(TypeError, match="Root Wkl has no rows"):
        Wkl()[0]


# ---------- __getitem__(slice) ----------


def test_getitem_slice_returns_wkl():
    result = wkls.us.ca.counties()[:5]
    assert isinstance(result, Wkl)
    assert len(result) == 5


def test_getitem_empty_slice():
    assert len(wkls.us.ca.counties()[1000:2000]) == 0


def test_getitem_negative_slice():
    counties = wkls.us.ca.counties()
    assert len(counties[-3:]) == 3


def test_getitem_stepped_slice_raises():
    with pytest.raises(TypeError, match="step"):
        wkls.us.ca.counties()[::2]


# ---------- __getitem__(str / other) ----------


def test_getitem_str_raises():
    """String subscripts raise TypeError with Surface-1 guidance.

    Breaking change from v1.2: the old DeprecationWarning shim for
    ``wkls.us["OR"]`` / ``wkls.us.ca["%San%"]`` is removed. Dot access
    and ``.search(...)`` are the documented paths.
    """
    with pytest.raises(TypeError, match="dot access"):
        wkls.us["OR"]


def test_getitem_wildcard_str_raises():
    with pytest.raises(TypeError, match="search"):
        wkls.us.ca["%San%"]


def test_getitem_list_raises():
    with pytest.raises(TypeError, match="int or slice"):
        wkls.us.ca.counties()[[1, 2]]


def test_getitem_bool_raises():
    """bool is a subclass of int in Python; explicitly reject to avoid surprises."""
    with pytest.raises(TypeError, match="int or slice"):
        wkls.us.ca.counties()[True]


# ---------- __contains__ ----------


def test_contains_uuid_hit():
    counties = wkls.us.ca.counties()
    uid = counties.to_dicts()[0]["id"]
    assert uid in counties


def test_contains_uuid_miss():
    assert "00000000-0000-0000-0000-000000000000" not in wkls.us.ca.counties()


def test_contains_non_string_is_false():
    """Non-string items return False, not TypeError (soft-check idiom)."""
    assert (123 in wkls.us.ca.counties()) is False


def test_contains_on_root_is_false():
    """Soft-check: no rows to inspect → False, not TypeError."""
    assert ("anything" in Wkl()) is False


# ---------- list / tuple conversions ----------


def test_list_conversion():
    counties = wkls.us.ca.counties()
    lst = list(counties)
    assert len(lst) == len(counties)
    assert all(isinstance(x, Wkl) for x in lst)


def test_tuple_conversion():
    counties = wkls.us.ca.counties()
    tup = tuple(counties)
    assert len(tup) == len(counties)


def test_slice_then_list():
    lst = list(wkls.us.ca.counties()[:3])
    assert len(lst) == 3
    assert all(isinstance(x, Wkl) for x in lst)


# ---------- head / limit return Wkl (was: sedona DataFrame) ----------


def test_head_returns_wkl():
    """Behavior change from v1.2: .head(n) wraps its return in a Wkl."""
    result = wkls.us.ca.counties().head(3)
    assert isinstance(result, Wkl)
    assert len(result) == 3


def test_limit_returns_wkl():
    result = wkls.us.ca.counties().limit(3)
    assert isinstance(result, Wkl)
    assert len(result) == 3


def test_head_to_dicts_chain():
    """Copilot-review bug: .head(3).to_dicts() end-to-end.

    Before v1.3 this failed because .head returned a sedona DataFrame
    which has no .to_dicts method.
    """
    rows = wkls.us.ca.counties().head(3).to_dicts()
    assert isinstance(rows, list)
    assert len(rows) == 3
    assert all(isinstance(r, dict) for r in rows)


# ---------- narrowed __getattr__ passthrough ----------


def test_filter_raises_attribute_error():
    """`.filter()` used to silently pass through to sedona; now it raises."""
    with pytest.raises(AttributeError, match=r"\.resolve\(\)"):
        wkls.us.ca.counties().filter(None)


def test_select_raises_attribute_error():
    with pytest.raises(AttributeError, match=r"\.resolve\(\)"):
        wkls.us.ca.counties().select("id")


def test_group_by_raises_attribute_error():
    with pytest.raises(AttributeError, match=r"\.resolve\(\)"):
        wkls.us.ca.counties().group_by("subtype")


def test_arbitrary_sedona_method_raises():
    with pytest.raises(AttributeError, match=r"\.resolve\(\)"):
        wkls.us.ca.counties().nonexistent_method()


# ---------- allowlisted passthroughs unchanged ----------


def test_count_unchanged():
    assert isinstance(wkls.us.ca.counties().count(), int)


def test_show_unchanged(capsys):
    result = wkls.us.ca.counties().head(1).show()
    # show() returns None and prints to stdout. We don't assert on the
    # exact output to avoid coupling to sedona's formatting.
    assert result is None


def test_to_arrow_table_unchanged():
    tbl = wkls.us.ca.counties().to_arrow_table()
    assert isinstance(tbl, pa.Table)


def test_to_dicts_unchanged():
    rows = wkls.us.ca.counties().to_dicts()
    assert isinstance(rows, list)
    assert all(isinstance(r, dict) for r in rows)


# ---------- Surface 3: .resolve() escape ----------


def test_resolve_returns_sedona_dataframe():
    df = wkls.us.ca.counties().resolve()
    assert isinstance(df, sedonadb.dataframe.DataFrame)


def test_resolve_supports_sedona_api():
    """The object returned by .resolve() is a real sedona DataFrame.

    Surface 3's contract: .resolve() hands you sedona's API. We don't
    re-test sedona's behavior here; we just confirm the handoff works
    and users can call sedona-native methods on the result.
    """
    df = wkls.us.ca.counties().resolve()
    assert isinstance(df, sedonadb.dataframe.DataFrame)
    # Call something sedona-native (not surfaced on Wkl directly).
    assert df.count() >= 1
    df.show()  # sedona's show, not wkl's — should just print and return None


# ---------- chain-mode iteration (not-yet-resolved Wkl) ----------


def test_chain_mode_iter_resolves_transparently():
    """Iterating a chain-mode Wkl that hasn't been resolved yet works."""
    rows = list(wkls.us.ca.search("san"))
    assert len(rows) >= 2
    assert all(isinstance(r, Wkl) for r in rows)


# ---------- row-Wkl integration ----------


def test_row_wkt_end_to_end():
    row = wkls.us.ca.counties()[0]
    wkt = row.wkt()
    assert wkt.startswith(("POLYGON", "MULTIPOLYGON"))


def test_row_path_end_to_end():
    row = wkls.us.ca.counties()[0]
    assert row.path.startswith("wkls.us.ca.")


def test_row_to_dicts_single_element():
    row = wkls.us.ca.counties()[0]
    rows = row.to_dicts()
    assert len(rows) == 1
    assert isinstance(rows[0], dict)


def test_row_parent_walks_up():
    row = wkls.us.ca.counties()[0]
    parent = row.parent
    # Parent of a California county is California.
    parent_rows = parent.to_dicts()
    assert len(parent_rows) == 1
    assert parent_rows[0]["country"] == "US"
    assert parent_rows[0]["region"] == "US-CA"


# ── svg / hexwkb removal (v1.2.0) ──────────────────────────────────


def test_svg_removed():
    """svg() no longer exists on Wkl."""
    assert not hasattr(wkls.Wkl, "svg")


def test_hexwkb_removed():
    """hexwkb() no longer exists on Wkl."""
    assert not hasattr(wkls.Wkl, "hexwkb")
