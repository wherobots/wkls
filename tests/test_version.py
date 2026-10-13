"""Tests for Overture release selection in ``wkls._version``."""

import io
from datetime import datetime, timedelta, timezone

import pytest

from wkls import _version

_NS = "http://s3.amazonaws.com/doc/2006-03-01/"


def _listing(stamps: list[str]) -> bytes:
    items = "".join(
        f"<Contents><Key>release/x/part-{i}.parquet</Key>"
        f"<LastModified>{stamp}</LastModified></Contents>"
        for i, stamp in enumerate(stamps)
    )
    return f'<ListBucketResult xmlns="{_NS}">{items}</ListBucketResult>'.encode()


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _serve(monkeypatch, payload: bytes) -> None:
    monkeypatch.setattr(
        _version.urllib.request, "urlopen", lambda url, timeout: _Response(payload)
    )


def test_release_settles_an_hour_after_its_last_object(monkeypatch):
    now = datetime(2026, 9, 23, 18, 30, tzinfo=timezone.utc)
    fresh = [_stamp(now - timedelta(minutes=50)), _stamp(now - timedelta(minutes=40))]
    old = [
        _stamp(now - timedelta(hours=2)),
        _stamp(now - timedelta(hours=1, minutes=1)),
    ]

    _serve(monkeypatch, _listing(fresh))
    assert _version._release_is_settled("2026-09-23.0", now=now) is False
    _serve(monkeypatch, _listing(old))
    assert _version._release_is_settled("2026-09-23.0", now=now) is True


def test_release_with_no_objects_is_not_settled(monkeypatch):
    _serve(monkeypatch, _listing([]))
    assert _version._release_is_settled("2026-09-23.0") is False


@pytest.fixture
def releases(monkeypatch):
    monkeypatch.delenv("WKLS_OVERTURE_VERSION", raising=False)
    monkeypatch.setattr(
        _version,
        "_list_s3_releases",
        lambda: ["2026-08-19.0", "2026-09-23.0", "2026-10-21.0"],
    )
    monkeypatch.setattr(_version, "_bundled_overture_version", lambda: "2026-09-23.0")


def test_newest_release_is_used_once_settled(releases, monkeypatch):
    monkeypatch.setattr(_version, "_release_is_settled", lambda version: True)
    assert _version._resolve_overture_version() == "2026-10-21.0"


def test_half_uploaded_release_is_skipped(releases, monkeypatch):
    monkeypatch.setattr(_version, "_release_is_settled", lambda version: False)
    assert _version._resolve_overture_version() == "2026-09-23.0"


def test_bundle_release_needs_no_settle_check(releases, monkeypatch):
    monkeypatch.setattr(
        _version, "_list_s3_releases", lambda: ["2026-08-19.0", "2026-09-23.0"]
    )

    def never(version):
        raise AssertionError("settle check must not run for the bundle's release")

    monkeypatch.setattr(_version, "_release_is_settled", never)
    assert _version._resolve_overture_version() == "2026-09-23.0"


def test_only_release_is_used_even_while_uploading(releases, monkeypatch):
    monkeypatch.setattr(_version, "_list_s3_releases", lambda: ["2026-10-21.0"])
    monkeypatch.setattr(_version, "_release_is_settled", lambda version: False)
    assert _version._resolve_overture_version() == "2026-10-21.0"


def test_bundled_version_comes_from_the_parquet_metadata():
    assert _version._bundled_overture_version().startswith("20")
