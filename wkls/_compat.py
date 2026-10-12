"""Import-time checks for known interactions between wkls's dependencies."""

from __future__ import annotations

import warnings

import pyarrow.fs


def _warn_if_gdal_broke_pyarrow() -> None:
    """Warn once if sedonadb's GDAL autoload broke pyarrow's local filesystem.

    sedonadb 0.4+ loads a GDAL shared library when it is imported. With no
    rasterio or pyogrio installed it falls back to a Homebrew or conda GDAL,
    and those builds link their own Arrow C++, which registers a second
    ``file`` filesystem factory. Every later ``pyarrow.fs.LocalFileSystem()``
    in the process then raises ArrowKeyError (apache/arrow#44696), so
    ``pq.read_table("x.parquet")`` and ``geopandas.read_parquet`` fail with
    no mention of GDAL or sedonadb. wkls never takes that path itself, but
    the code around it does, so detect it here and name the fix.
    """
    try:
        pyarrow.fs.LocalFileSystem()
    except Exception as exc:  # ArrowKeyError; the check itself must not raise
        if "already registered" not in str(exc):
            return
        warnings.warn(
            "sedonadb loaded a GDAL build that links its own Arrow, so pyarrow "
            "local file access (pyarrow.parquet.read_table('x.parquet'), "
            "geopandas.read_parquet, ...) will fail in this process with: "
            f"{exc}. Fix: pip install pyogrio (or rasterio) so sedonadb uses "
            "the GDAL bundled in that wheel instead of the system one.",
            RuntimeWarning,
            stacklevel=2,
        )
