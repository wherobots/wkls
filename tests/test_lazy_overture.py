"""Tests for lazy-loading the remote Overture GeoParquet view.

``import wkls`` must succeed without network access. All chain
resolution, search, listing, and introspection methods run against
the bundled local metadata. Only geometry methods (``.wkt()``,
``.wkb()``, ``.geojson()``) reach S3, and they do so lazily on first
call.
"""

from __future__ import annotations

import subprocess
import sys
import urllib.error
import urllib.request

import pytest

import wkls
from wkls import core


def test_import_without_network_subprocess():
    """A fresh import in a subprocess with S3 blocked still succeeds.

    Runs in a subprocess with a module-level monkeypatch that replaces
    ``urllib.request.urlopen`` before ``import wkls`` fires, then
    exercises the offline-safe surfaces. This is the strongest form
    of the guarantee — no shared state with the parent process.
    """
    script = r"""
import urllib.request, urllib.error
urllib.request.urlopen = lambda *a, **kw: (_ for _ in ()).throw(
    urllib.error.URLError("blocked for offline-import test")
)

import wkls  # must not raise

assert wkls.countries().count() > 0, "countries() should work offline"
assert wkls.us.regions().count() > 0, "regions() should work offline"
assert wkls.us.ca.sanfrancisco.resolve().count() == 1, "chain resolution should work offline"
assert wkls.us.ca.search('oakland').count() >= 1, "search should work offline"
print("OK")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"subprocess failed:\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    assert result.stdout.strip().endswith("OK")


def test_ensure_overture_loaded_is_idempotent(monkeypatch):
    """Calling ``_ensure_overture_loaded`` twice only registers once."""
    # Reset the module flag so we can observe the first load.
    monkeypatch.setattr(core, "_overture_view_loaded", False)

    calls = {"read_parquet": 0}
    original_read_parquet = core.sedona.read_parquet

    def counting_read_parquet(*args, **kwargs):
        calls["read_parquet"] += 1
        return original_read_parquet(*args, **kwargs)

    monkeypatch.setattr(core.sedona, "read_parquet", counting_read_parquet)

    core._ensure_overture_loaded()
    first = calls["read_parquet"]
    core._ensure_overture_loaded()
    second = calls["read_parquet"]

    assert first == 1, "first call should register the view"
    assert second == first, "second call should short-circuit"
    assert core._overture_view_loaded is True


def test_geometry_triggers_overture_load(monkeypatch):
    """A geometry call triggers lazy load when the view isn't registered."""
    # Force the lazy path: pretend nothing is loaded.
    monkeypatch.setattr(core, "_overture_view_loaded", False)

    called = {"count": 0}
    real_ensure = core._ensure_overture_loaded

    def spy_ensure():
        called["count"] += 1
        real_ensure()

    monkeypatch.setattr(core, "_ensure_overture_loaded", spy_ensure)

    wkls.us.ca.sanfrancisco.wkt()
    assert called["count"] == 1, "geometry call must trigger exactly one lazy load"


def test_offline_geometry_raises_connection_error(monkeypatch):
    """When S3 is unreachable, the first geometry call surfaces a clear error."""
    # Force a cold state and unresolved version so the lazy path must
    # go through _list_s3_releases — which we break.
    monkeypatch.setattr(core, "_overture_view_loaded", False)
    monkeypatch.setattr(core, "_current_overture_version", None)

    def blocked(*args, **kwargs):
        raise urllib.error.URLError("simulated offline")

    monkeypatch.setattr(urllib.request, "urlopen", blocked)

    with pytest.raises(ConnectionError, match="Overture Maps releases"):
        wkls.us.ca.sanfrancisco.wkt()
