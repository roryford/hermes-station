"""Verify the lite-tier toolbelt binaries are present and runnable.

Each binary is added in the Dockerfile's apt layer (or the yq side-layer).
They cost ~150-200MB of image growth and zero idle RAM, and unlock OCR,
voice (ffmpeg), JSON/YAML wrangling, fast file search, and MCP server
prereqs (npx) for agents running inside the container.

This file is hermetic: each test auto-skips if the binary isn't on PATH,
so plain `pytest` on a dev laptop stays green. CI runs it (a) inside the
built image via `container exec`, and (b) optionally with
`HERMES_STATION_REQUIRE_TOOLBELT=1` set to convert skips into failures.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

REQUIRE = os.environ.get("HERMES_STATION_REQUIRE_TOOLBELT") == "1"

# (binary, args-that-print-version-and-exit-0)
# Some tools (ripgrep, fd, tesseract, yq) accept --version; others need
# a different flag. ffmpeg writes version to stderr and exits 0 anyway.
TOOLBELT: list[tuple[str, list[str]]] = [
    ("node", ["--version"]),
    ("npm", ["--version"]),
    ("npx", ["--version"]),
    ("ffmpeg", ["-version"]),
    ("tesseract", ["--version"]),
    ("rg", ["--version"]),
    ("fd", ["--version"]),
    ("sqlite3", ["--version"]),
    ("pdftotext", ["-v"]),  # poppler-utils; writes to stderr, exits 0
    ("yq", ["--version"]),
]


@pytest.mark.parametrize(("binary", "args"), TOOLBELT, ids=[b for b, _ in TOOLBELT])
def test_toolbelt_binary_on_path_and_runnable(binary: str, args: list[str]) -> None:
    path = shutil.which(binary)
    if path is None:
        msg = f"{binary!r} not on PATH"
        if REQUIRE:
            pytest.fail(msg)
        pytest.skip(msg + " (set HERMES_STATION_REQUIRE_TOOLBELT=1 to fail instead)")

    proc = subprocess.run(  # noqa: S603 - controlled args, fixed binary list
        [path, *args],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0, (
        f"{binary} {' '.join(args)} exited {proc.returncode}: "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    # Some tools write version to stderr (ffmpeg, pdftotext); accept either.
    output = (proc.stdout + proc.stderr).strip()
    assert output, f"{binary} produced no version output"


def test_node_is_v20_lts() -> None:
    """Pin guard: NodeSource repo is `node_20.x`, so node --version must
    be v20.x. If this fails after a Dockerfile bump, that bump was unintended."""
    path = shutil.which("node")
    if path is None:
        if REQUIRE:
            pytest.fail("node not on PATH")
        pytest.skip("node not on PATH")
    proc = subprocess.run(  # noqa: S603
        [path, "--version"], capture_output=True, text=True, timeout=10
    )
    assert proc.returncode == 0
    version = proc.stdout.strip()
    if not version.startswith("v20."):
        msg = f"expected Node 20.x LTS, got {version!r}"
        # On a dev laptop, the host's nodejs version is irrelevant — only the
        # container build matters. Fail only when REQUIRE is set (CI / in-image).
        if REQUIRE:
            pytest.fail(msg)
        pytest.skip(msg + " (host node, not container; skipping)")


def test_fd_symlink_resolves_to_fdfind() -> None:
    """The Debian binary is `fdfind`; the Dockerfile symlinks it to `fd`
    so docs/scripts/agents that call `fd` directly work."""
    fd = shutil.which("fd")
    if fd is None:
        if REQUIRE:
            pytest.fail("fd not on PATH")
        pytest.skip("fd not on PATH")
    # Either the symlink target is fdfind, or fdfind is also on PATH.
    real = os.path.realpath(fd)
    fdfind = shutil.which("fdfind")
    assert real.endswith("fdfind") or fdfind is not None, (
        f"fd ({fd}) does not resolve to fdfind (realpath={real}, fdfind={fdfind})"
    )
