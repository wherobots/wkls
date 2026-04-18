"""Pytest fixtures shared across the test suite."""

from __future__ import annotations

import warnings

import pytest


@pytest.fixture
def ignore_bracket_deprecation():
    """Silence DeprecationWarning emitted by legacy __getitem__ calls.

    Used by tests that exercise bracket access on purpose — for example,
    regression tests asserting the shim still works, or older tests that
    predate the `.search()` / name-access migration.
    """
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"Bracket access.*deprecated",
            category=DeprecationWarning,
        )
        yield
