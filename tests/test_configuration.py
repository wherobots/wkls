import pytest

import wkls


def test_overture_releases():
    """Test that overture_releases() returns available S3 versions."""
    releases = wkls.overture_releases()
    assert isinstance(releases, list)
    assert len(releases) >= 1
    # Each release should follow YYYY-MM-DD.N format
    for release in releases:
        parts = release.split("-")
        assert len(parts) == 3, f"Unexpected release format: {release}"
        assert "." in parts[-1], f"Unexpected release format: {release}"
    # Should be sorted
    assert releases == sorted(releases)
    print(f"Available releases: {releases}")


def test_overture_releases_blocked_on_chained():
    """Test that overture_releases() is blocked on chained objects."""
    assert not hasattr(wkls.us, "overture_releases")


def test_overture_version_blocked_on_chained():
    """Test that overture_version() is blocked on chained objects."""
    assert not hasattr(wkls.us, "overture_version")


def test_configure_valid_version():
    """Test that configure() works with a valid version."""
    releases = wkls.overture_releases()

    # Configure to the first available release
    target_version = releases[0]
    wkls.configure(overture_version=target_version)
    assert wkls.overture_version() == target_version

    # Restore to the latest version
    wkls.configure(overture_version=releases[-1])
    assert wkls.overture_version() == releases[-1]


def test_configure_invalid_version():
    """Test that configure() raises ValueError for invalid versions."""
    with pytest.raises(ValueError) as exc_info:
        wkls.configure(overture_version="2020-01-01.0")

    error_msg = str(exc_info.value)
    assert "2020-01-01.0" in error_msg
    assert "not available" in error_msg
    # Should list available versions in the error
    assert "Available versions:" in error_msg


def test_configure_blocked_on_chained():
    """Test that configure() is blocked on chained objects."""
    assert not hasattr(wkls.us, "configure")


def test_version_matches_latest_release():
    """Test that the auto-detected version is the latest available release."""
    releases = wkls.overture_releases()
    version = wkls.overture_version()
    assert version == releases[-1], (
        f"Auto-detected version '{version}' should be the latest release '{releases[-1]}'"
    )
