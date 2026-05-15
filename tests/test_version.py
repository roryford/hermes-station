"""Verify __version__ is single-sourced from package metadata."""

from importlib.metadata import version as pkg_version

from hermes_station import __version__


def test_version_matches_package_metadata() -> None:
    assert __version__ == pkg_version("hermes-station")


def test_version_is_not_unknown() -> None:
    assert __version__ != "unknown"
