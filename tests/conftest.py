"""Pytest fixtures shared across the test suite.

The pre-v1.2 ``ignore_bracket_deprecation`` fixture was removed when the
bracket-access shim was replaced with a ``TypeError`` in v1.2.0. No
module-level fixtures are currently shared; kept for future additions.
"""

from __future__ import annotations
