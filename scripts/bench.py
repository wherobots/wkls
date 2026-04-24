#!/usr/bin/env python3
"""Benchmark wkls patterns that don't hit the network.

Cold = module-level caches cleared before the call. Warm = median of
``WARM_REPS`` repeats with caches populated. Run before and after a
change to confirm the direction of the impact; don't treat the numbers
as absolute (wall-clock, shared machines).

Skipped on purpose: ``.wkt()``, ``.wkb()``, ``.geojson()``, ``.hexwkb()``,
``.svg()`` — all trigger ``_ensure_overture_loaded`` and read S3.

Usage:
    uv run scripts/bench.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from statistics import median

import wkls
from wkls import core


WARM_REPS = 5


def _prefetch_sf_id() -> str:
    """Return a real row id so ``by_id`` has a valid target."""
    rows = wkls.us.ca.sanfrancisco.to_dicts()
    return rows[0]["id"]


def build_cases(sf_id: str) -> list[tuple[str, str]]:
    _ = sf_id  # referenced inside exec'd snippets via the namespace
    return [
        # Introspection
        ("dir(wkls)",                              "dir(wkls)"),
        ("dir(wkls.us)",                           "dir(wkls.us)"),
        ("dir(wkls.us.ca)",                        "dir(wkls.us.ca)"),
        ("pydoc.render_doc(wkls)  # help proxy",   "import pydoc; pydoc.render_doc(wkls)"),
        ("pydoc.render_doc(wkls.us)",              "import pydoc; pydoc.render_doc(wkls.us)"),
        # Chain drills
        ("wkls.us",                                "wkls.us"),
        ("wkls.us.ca",                             "wkls.us.ca"),
        ("wkls.us.ca.sanfrancisco",                "wkls.us.ca.sanfrancisco"),
        ("wkls.unitedstates  # name vs ISO",       "wkls.unitedstates"),
        ("wkls.us.pa.adamscounty.franklin  # 4-lvl", "wkls.us.pa.adamscounty.franklin"),
        # Lookup
        ("wkls.by_id(<sf id>)",                    "wkls.by_id(SF_ID)"),
        # Listings
        ("wkls.countries()",                       "wkls.countries()"),
        ("wkls.dependencies()",                    "wkls.dependencies()"),
        ("wkls.subtypes()",                        "wkls.subtypes()"),
        ("wkls.us.regions()",                      "wkls.us.regions()"),
        ("wkls.us.ca.counties()",                  "wkls.us.ca.counties()"),
        ("wkls.us.ca.sandiegocounty.cities()",     "wkls.us.ca.sandiegocounty.cities()"),
        # Search
        ("wkls.search('franklin')",                "wkls.search('franklin')"),
        ("wkls.us.tn.search('franklin')",          "wkls.us.tn.search('franklin')"),
        # Result-mode inspection
        ("wkls.countries().count()",               "wkls.countries().count()"),
        ("wkls.countries().head(5)",               "wkls.countries().head(5)"),
        ("wkls.countries().to_dicts()",            "wkls.countries().to_dicts()"),
        # Repr
        ("repr(wkls.us.ca)  # chain, single row",  "repr(wkls.us.ca)"),
        ("repr(wkls.us.ca.sanfrancisco)",          "repr(wkls.us.ca.sanfrancisco)"),
        ("repr(wkls.countries())  # result, 219r", "repr(wkls.countries())"),
        # Navigation
        ("wkls.us.ca.sanfrancisco.parent",         "wkls.us.ca.sanfrancisco.parent"),
        ("wkls.us.ca.sanfrancisco.path",           "wkls.us.ca.sanfrancisco.path"),
    ]


def reset_caches() -> None:
    core._dir_cache.clear()
    core._country_info.clear()
    core._region_info.clear()
    core._row_info.clear()


def time_once(code: str, sf_id: str) -> float:
    namespace: dict = {"wkls": wkls, "SF_ID": sf_id}
    t = time.perf_counter()
    exec(code, namespace)
    return time.perf_counter() - t


def bench(code: str, sf_id: str) -> tuple[float, float]:
    reset_caches()
    cold = time_once(code, sf_id)
    warm = [time_once(code, sf_id) for _ in range(WARM_REPS)]
    return cold, median(warm)


def measure_import() -> float:
    """Full cold import time in a fresh Python process."""
    script = (
        "import time\n"
        "t = time.perf_counter()\n"
        "import wkls\n"
        "print(time.perf_counter() - t)\n"
    )
    out = subprocess.check_output([sys.executable, "-c", script], text=True)
    return float(out.strip().splitlines()[-1])


def main() -> None:
    # Prime sedona so the first in-process measurement isn't skewed by
    # query-plan warmup from the very first SQL call.
    wkls.countries()
    sf_id = _prefetch_sf_id()

    print(f"wkls bench  —  cold = fresh caches,  warm = median of {WARM_REPS}")
    print("=" * 82)
    import_cold = measure_import()
    print(f"{'import wkls (fresh process)':<44}{import_cold * 1000:>14.1f} ms")
    print("=" * 82)
    print(f"{'pattern':<44}{'cold':>18}{'warm':>18}")
    print("-" * 82)
    for name, code in build_cases(sf_id):
        cold, warm = bench(code, sf_id)
        print(f"{name:<44}{cold * 1000:>14.1f} ms{warm * 1000:>14.1f} ms")


if __name__ == "__main__":
    main()
