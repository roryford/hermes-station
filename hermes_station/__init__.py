"""hermes-station: single-container deployment scaffolding for Hermes Agent + WebUI."""

from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("hermes-station")
except PackageNotFoundError:
    __version__ = "unknown"
