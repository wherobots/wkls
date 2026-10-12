"""Tests for the import-time environment check in ``wkls._compat``."""

import warnings

import pyarrow.fs
import pyarrow.lib
import pytest

from wkls import _compat

_CLASH = (
    "Attempted to register factory for scheme 'file' but that scheme is "
    "already registered."
)


def test_silent_when_local_filesystem_works(monkeypatch):
    monkeypatch.setattr(pyarrow.fs, "LocalFileSystem", lambda: object())
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        _compat._warn_if_gdal_broke_pyarrow()


def test_warns_with_the_fix_when_file_scheme_is_double_registered(monkeypatch):
    def broken() -> None:
        raise pyarrow.lib.ArrowKeyError(_CLASH)

    monkeypatch.setattr(pyarrow.fs, "LocalFileSystem", broken)
    with pytest.warns(RuntimeWarning, match="pip install pyogrio"):
        _compat._warn_if_gdal_broke_pyarrow()


def test_unrelated_failures_stay_silent(monkeypatch):
    def broken() -> None:
        raise RuntimeError("some other LocalFileSystem failure, not the GDAL clash")

    monkeypatch.setattr(pyarrow.fs, "LocalFileSystem", broken)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        _compat._warn_if_gdal_broke_pyarrow()
