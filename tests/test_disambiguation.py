"""Tests for the hierarchy-aware conflict-resolution surface.

Covers:

- ``AmbiguousLocationError`` raised by geometry methods on multi-row ``Wkl``.
- ``Wkl.by_id(uuid)`` as the explicit escape hatch.
- ``Wkl.parent`` for walking up one level.
- ``Wkl.path`` — the canonical dot-access string that round-trips.
- Chain depth 4 (parent narrower): ``wkls.us.pa.adamscounty.franklin``.
- Dot access is admin-hierarchy only — no subtype filtering via attrs.
"""

from __future__ import annotations

import pytest

import wkls
from wkls import Wkl
from wkls.core import AmbiguousLocationError

# ---------- AmbiguousLocationError ----------


def test_ambiguity_raises_on_multi_row_geometry():
    """Geometry methods raise AmbiguousLocationError when >1 row matches."""
    # 18 Franklin townships in PA.
    with pytest.raises(AmbiguousLocationError):
        wkls.us.pa.franklin.wkt()


def test_ambiguity_error_lists_candidates_with_copy_pasteable_chains():
    """Error message echoes the query, lists each candidate's subtype + parent,
    and surfaces copy-pasteable narrower chains + literal by_id(...).wkt()."""
    try:
        wkls.us.pa.franklin.wkt()
    except AmbiguousLocationError as e:
        msg = str(e)
    else:
        pytest.fail("expected AmbiguousLocationError")
    # Query echoed with wkls. prefix so it's copy-pasteable.
    assert "18 matches for 'wkls.us.pa.franklin'" in msg
    # 18 Franklins share subtype + attr but differ by parent; the narrower
    # should be the 4-level parent-narrower form with at least one known
    # Pennsylvania county name in the normalized attribute form.
    assert "Narrow by parent (4-level chain)" in msg
    assert "wkls.us.pa.yorkcounty.franklin" in msg  # known candidate
    # Literal by_id calls with .wkt() for copy-paste.
    assert "wkls.by_id('" in msg
    assert ".wkt()" in msg
    # Subtype + parent name appear in the by_id comments.
    assert "(locality," in msg
    assert "County" in msg


def test_ambiguity_error_indistinguishable_candidates_uses_by_id():
    """When no dot-access narrower distinguishes candidates, the error
    explicitly says so and surfaces by_id calls for each candidate."""
    # Two Yorks in PA share subtype AND parent — only by_id resolves.
    try:
        wkls.us.pa.york.wkt()
    except AmbiguousLocationError as e:
        msg = str(e)
    else:
        pytest.fail("expected AmbiguousLocationError")
    assert "Narrow with one of" in msg
    assert "Pick by id" in msg
    # Two literal by_id calls, one per candidate.
    assert msg.count("wkls.by_id('") == 2


def test_ambiguity_is_valueerror_subclass():
    """Preserves compatibility with `except ValueError:` blocks."""
    with pytest.raises(ValueError):
        wkls.us.pa.franklin.wkt()


def test_search_wkt_raises_on_multi_row():
    """Geometry on a multi-row search result raises too."""
    with pytest.raises(AmbiguousLocationError):
        wkls.search("united").wkt()


def test_ambiguity_error_suggests_subtype_narrowing_when_subtypes_differ():
    """When candidates span subtypes (e.g. city + county), the error
    surfaces ``.cities()`` / ``.counties()`` as a narrower alongside
    ``by_id``. This is the path an agent hits after searching for a
    name that's both a locality and a county.
    """
    # San Diego in CA: the city (locality) + the county (county).
    try:
        wkls.us.ca.search("san diego").wkt()
    except AmbiguousLocationError as e:
        msg = str(e)
    else:
        pytest.fail("expected AmbiguousLocationError")
    assert "Filter by subtype on this result" in msg
    assert ".cities()" in msg
    assert ".counties()" in msg
    # by_id still advertised as the universal escape hatch.
    assert "wkls.by_id('" in msg


def test_ambiguity_error_skips_subtype_narrowing_when_all_same_subtype():
    """Subtype suggestion is suppressed when it wouldn't reduce the set.

    The 18 Franklins in PA are all localities — ``.cities()`` would
    return the same 18 rows, so the message shouldn't pretend it helps.
    """
    try:
        wkls.us.pa.franklin.wkt()
    except AmbiguousLocationError as e:
        msg = str(e)
    else:
        pytest.fail("expected AmbiguousLocationError")
    assert "filter by subtype on this result" not in msg


# ---------- Wkl.by_id ----------


def test_by_id_returns_single_row():
    """by_id resolves one specific row."""
    # Get any id via search.
    df = wkls.us.ca.search("oakland").to_arrow_table()
    uid = df.column("id")[0].as_py()
    result = wkls.by_id(uid)
    assert isinstance(result, Wkl)
    assert len(result) == 1


def test_by_id_geometry():
    """by_id supports geometry access."""
    df = wkls.us.ca.search("oakland").to_arrow_table()
    uid = df.column("id")[0].as_py()
    wkt = wkls.by_id(uid).wkt()
    assert wkt.startswith(("POLYGON", "MULTIPOLYGON"))


def test_by_id_unknown_raises():
    """Unknown UUID raises ValueError."""
    with pytest.raises(ValueError, match="No row found"):
        wkls.by_id("00000000-0000-0000-0000-000000000000")


# ---------- Wkl.parent ----------


def test_parent_walks_up_one_level():
    """.parent returns the row one level up the hierarchy."""
    sf = wkls.us.ca.sanfrancisco
    parent = sf.parent
    assert isinstance(parent, Wkl)
    table = parent._resolve().to_arrow_table()
    assert table.column("name_primary")[0].as_py() == "California"


def test_parent_parent_chain():
    """.parent.parent walks up two levels."""
    grandparent = wkls.us.ca.sanfrancisco.parent.parent
    table = grandparent._resolve().to_arrow_table()
    assert table.column("name_primary")[0].as_py() == "United States"


def test_parent_on_country_raises():
    """Countries are at the top; .parent raises."""
    with pytest.raises(ValueError, match="no parent"):
        wkls.us.parent  # noqa: B018


def test_parent_on_multi_row_raises():
    """.parent requires a single-row Wkl."""
    with pytest.raises(ValueError, match="single-row"):
        wkls.search("united").parent  # noqa: B018


# ---------- Wkl.path ----------


def test_path_chain_mode():
    """Chain-mode .path joins the chain under wkls."""
    assert wkls.us.ca.sanfrancisco.path == "wkls.us.ca.sanfrancisco"


def test_path_root():
    """Root Wkl.path is just 'wkls'."""
    from wkls import Wkl

    assert Wkl().path == "wkls"


def test_path_result_mode_round_trips():
    """A result-mode Wkl's path resolves back via eval."""
    df = wkls.us.ca.search("oakland").to_arrow_table()
    uid = df.column("id")[0].as_py()
    resolved = wkls.by_id(uid)
    path = resolved.path
    # Oakland's parent is Alameda County → 4-level chain
    assert path == "wkls.us.ca.alamedacounty.oakland"
    # Round-trip: eval the string and get equivalent geometry
    assert eval(path).wkt() == resolved.wkt()


def test_path_multi_row_raises():
    """No single path exists for a multi-row Wkl."""
    with pytest.raises(ValueError, match="single-row"):
        wkls.search("united").path  # noqa: B018


def test_path_on_single_row_search_result_walks_parent_id():
    """.path on a single-row search result walks parent_id correctly.

    Regression: SEARCH_* templates used to project an explicit column list
    that omitted parent_id, so the walk loop bailed after one iteration
    and the returned path contained only the leaf segment.
    """
    oakland = wkls.us.ca.search("oakland")
    assert len(oakland) == 1
    assert oakland.path == "wkls.us.ca.alamedacounty.oakland"


# ---------- Chain depth 4 (parent narrower) ----------


def test_chain_depth_4_resolves_ambiguity():
    """wkls.us.pa.franklin is ambiguous, but parent narrower resolves it."""
    # Adams County Franklin should be unique.
    result = wkls.us.pa.adamscounty.franklin
    assert result._resolve().count() == 1
    assert result.wkt().startswith(("POLYGON", "MULTIPOLYGON"))


def test_chain_depth_4_requires_single_parent():
    """4-level chain rejects ambiguous parent at depth 3."""
    # "Franklin" in PA is ambiguous — can't use it as a parent.
    with pytest.raises(ValueError, match="single row"):
        wkls.us.pa.franklin.anything  # noqa: B018


def test_chain_depth_5_raises():
    """Chain past depth 4 raises."""
    with pytest.raises(ValueError, match="Chain too deep"):
        wkls.us.pa.adamscounty.franklin.deeper  # noqa: B018


# ---------- Dot access is admin-hierarchy only ----------


def test_subtype_is_not_a_chain_filter():
    """Subtype names are not interpreted as in-place filters.

    Dots step through the admin hierarchy, nothing else. On a
    multi-row result-mode Wkl, attribute access either drills as a
    location name (and likely returns empty) or raises — it does NOT
    filter the current result by subtype.
    """
    multi = wkls.us.ca.search("san")
    assert len(multi) > 1
    # Accessing 'locality' on a result-mode Wkl should go through the
    # DataFrame passthrough (sedona DataFrames don't have a .locality
    # attribute) and raise AttributeError, NOT silently filter.
    with pytest.raises(AttributeError):
        multi.locality  # noqa: B018


def test_result_mode_dir_omits_subtype_names():
    """dir() on a multi-row result no longer surfaces subtype narrowers."""
    entries = set(dir(wkls.us.ca.search("san")))
    # Dot access is hierarchy only; subtype filters are gone.
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


def test_ambiguous_error_carries_candidates():
    """``err.candidates`` is the multi-row Wkl — no message parsing needed."""
    with pytest.raises(wkls.AmbiguousLocationError) as exc_info:
        wkls.us.pa.york.wkt()
    candidates = exc_info.value.candidates
    assert len(candidates) == 2
    assert all(
        "york" in (r["name_en"] or r["name_primary"]).lower()
        for r in candidates.to_dicts()
    )
    assert candidates.to_dicts()[0]["id"] in str(exc_info.value)


def test_exception_class_is_exported_at_package_level():
    """``except wkls.AmbiguousLocationError`` must catch, not drill a chain."""
    assert wkls.AmbiguousLocationError is wkls.core.AmbiguousLocationError
    assert issubclass(wkls.AmbiguousLocationError, ValueError)
