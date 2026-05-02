"""Tests for the v1.2.0 removal of the bracket-access deprecation shim.

Pre-v1.2, ``Wkl.__getitem__`` accepted string keys as a deprecated
alias for chain access / wildcard search and emitted a
``DeprecationWarning``. In v1.2.0 the shim was removed — string
subscripts now raise ``TypeError`` pointing at the modern replacement
(dot access, ``.search(...)``, ``.resolve()``). See
``docs/design/pythonic-wkl.md`` § Migration.

The Surface 2 protocol (int / slice subscript) is tested in
``tests/test_pythonic.py``.
"""

from __future__ import annotations

import pytest

import wkls
from wkls import Wkl


def test_string_bracket_access_raises():
    """``Wkl()["IN"]`` used to warn; v1.2 raises ``TypeError``."""
    with pytest.raises(TypeError, match="dot access"):
        Wkl()["IN"]


def test_string_bracket_access_message_points_at_search():
    """Error message lists ``.search()`` as one of the alternatives."""
    with pytest.raises(TypeError, match="\\.search"):
        wkls.us["OR"]


def test_wildcard_bracket_access_raises():
    """``wkls.us.ca["%fran%"]`` used to warn and resolve; v1.2 raises."""
    with pytest.raises(TypeError, match="dot access"):
        wkls.us.ca["%fran%"]


def test_chained_string_bracket_access_raises():
    """Bracket chain access at any depth raises ``TypeError``."""
    with pytest.raises(TypeError, match="dot access"):
        wkls.us["CA"]


def test_list_bracket_access_raises():
    """List keys (pre-v1.2: DataFrame column selection passthrough) now raise.

    Users wanting DataFrame-style column selection should call
    ``.resolve()`` and use the sedona DataFrame directly.
    """
    with pytest.raises(TypeError, match="int or slice"):
        wkls.us.ca[["id", "country"]]
