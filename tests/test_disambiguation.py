"""Tests for the hierarchy-aware conflict-resolution surface.

Covers:

- ``AmbiguousLocationError`` raised by geometry methods on multi-row ``Wkl``.
- ``Wkl.by_id(uuid)`` as the explicit escape hatch.
- ``Wkl.parent`` for walking up one level.
- ``Wkl.path`` — the canonical dot-access string that round-trips.
- Chain depth 4 (parent narrower): ``wkls.us.pa.adamscounty.franklin``.
- Subtype-as-modifier: ``wkls.search('united').country`` filters to subtype.
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


def test_ambiguity_error_lists_candidates_with_parent_and_subtype():
    """Error message shows candidate rows with subtype + parent name."""
    try:
        wkls.us.pa.franklin.wkt()
    except AmbiguousLocationError as e:
        msg = str(e)
    else:
        pytest.fail("expected AmbiguousLocationError")
    assert "18 matches" in msg
    assert "subtype=locality" in msg
    assert "parent=" in msg  # parent county name appears
    assert "County" in msg  # at least one parent is a *County


def test_ambiguity_is_valueerror_subclass():
    """Preserves compatibility with `except ValueError:` blocks."""
    with pytest.raises(ValueError):
        wkls.us.pa.franklin.wkt()


def test_search_wkt_raises_on_multi_row():
    """Geometry on a multi-row search result raises too."""
    with pytest.raises(AmbiguousLocationError):
        wkls.search("united").wkt()


# ---------- Wkl.by_id ----------


def test_by_id_returns_single_row():
    """by_id resolves one specific row."""
    # Get any id via search.
    df = wkls.us.ca.search("oakland").to_arrow_table()
    uid = df.column("id")[0].as_py()
    result = wkls.by_id(uid)
    assert isinstance(result, Wkl)
    assert result.count() == 1


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
    table = parent._df.to_arrow_table()
    assert table.column("name_primary")[0].as_py() == "California"


def test_parent_parent_chain():
    """.parent.parent walks up two levels."""
    grandparent = wkls.us.ca.sanfrancisco.parent.parent
    table = grandparent._df.to_arrow_table()
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


def test_path_after_subtype_modifier_on_search():
    """.path on a filtered search result walks parent_id correctly.

    Regression: SEARCH_* templates used to project an explicit column list
    that omitted parent_id, so the walk loop bailed after one iteration
    and the returned path contained only the leaf segment.
    """
    sd = wkls.us.ca.search("san d").county
    assert sd._df.count() == 1
    assert sd.path == "wkls.us.ca.sandiegocounty"


# ---------- Chain depth 4 (parent narrower) ----------


def test_chain_depth_4_resolves_ambiguity():
    """wkls.us.pa.franklin is ambiguous, but parent narrower resolves it."""
    # Adams County Franklin should be unique.
    result = wkls.us.pa.adamscounty.franklin
    assert result._df.count() == 1
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


# ---------- Subtype-as-modifier ----------


def test_subtype_modifier_filters_multi_row():
    """.<subtype> on a multi-row Wkl filters by subtype."""
    all_united = wkls.search("united")
    assert all_united.count() > 1  # countries + dependencies
    countries_only = all_united.country
    assert countries_only.count() >= 1
    # All rows in the filtered result have subtype='country'
    table = countries_only.to_arrow_table()
    for i in range(table.num_rows):
        assert table.column("subtype")[i].as_py() == "country"


def test_subtype_modifier_dependency():
    """The 'dependency' subtype filter works."""
    deps = wkls.search("united").dependency
    table = deps.to_arrow_table()
    for i in range(table.num_rows):
        assert table.column("subtype")[i].as_py() == "dependency"


def test_subtype_modifier_on_single_row_matching_returns_self():
    """Subtype modifier on a single-row Wkl whose subtype matches is a no-op."""
    sf = wkls.us.ca.sanfrancisco
    subtype = sf._df.to_arrow_table().column("subtype")[0].as_py()
    assert sf.__getattr__(subtype) is sf
