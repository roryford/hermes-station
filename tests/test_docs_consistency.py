"""Docs consistency checks — cheap, catches path/config drift before it reaches users."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent
DOCS_DIR = REPO_ROOT / "docs"

# All text files in docs/ and the top-level README that we check for stale paths.
_DOC_FILES = [
    *DOCS_DIR.glob("*.md"),
    *DOCS_DIR.glob("*.yaml"),
    REPO_ROOT / "README.md",
]


def test_no_unqualified_data_hermes_path() -> None:
    """Canonical path is /data/.hermes (dot-prefixed). Catch the typo /data/hermes."""
    bad = "/data/hermes"
    good = "/data/.hermes"
    offenders: list[str] = []
    for path in _DOC_FILES:
        if not path.exists():
            continue
        text = path.read_text()
        for lineno, line in enumerate(text.splitlines(), 1):
            # Allow lines that mention /data/.hermes — only flag bare /data/hermes
            # that isn't immediately followed by a dot (i.e. /data/hermes/).
            idx = 0
            while True:
                pos = line.find(bad, idx)
                if pos == -1:
                    break
                # Check the character right after the match isn't a dot
                after = line[pos + len(bad) : pos + len(bad) + 1]
                if after != ".":
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()!r}")
                idx = pos + 1
    assert not offenders, (
        f"Found {len(offenders)} occurrence(s) of bare '{bad}' (should be '{good}'):\n"
        + "\n".join(offenders)
    )


def test_config_example_yaml_is_valid() -> None:
    """docs/config.example.yaml must parse without error."""
    example = DOCS_DIR / "config.example.yaml"
    assert example.exists(), "docs/config.example.yaml not found"
    parsed = yaml.safe_load(example.read_text())
    # A valid minimal config is either None (all-commented) or a dict.
    assert parsed is None or isinstance(parsed, dict), (
        f"Expected dict or None, got {type(parsed)}"
    )


def test_config_example_yaml_keys_are_known() -> None:
    """Top-level keys in docs/config.example.yaml must be ones the app recognises."""
    known_top_level = {
        "model",
        "delegation",
        "web",
        "memory",
        "mcp_servers",
        "terminal",
        "display",
        "toolsets",
        "messaging",
        "channels",
        "admin",
        "integrations",
        "github",
        "fal",
        "browser",
    }
    example = DOCS_DIR / "config.example.yaml"
    parsed = yaml.safe_load(example.read_text())
    if not parsed:
        return
    unknown = set(parsed.keys()) - known_top_level
    assert not unknown, (
        f"docs/config.example.yaml has unrecognised top-level keys: {unknown}. "
        "Either add them to known_top_level in this test or remove them from the example."
    )


def test_readme_references_admin_password() -> None:
    """README must mention both password env vars so newcomers know to set them."""
    readme = (REPO_ROOT / "README.md").read_text()
    assert "HERMES_ADMIN_PASSWORD" in readme
    assert "HERMES_WEBUI_PASSWORD" in readme


def test_configuration_md_exists_and_mentions_hermes_home() -> None:
    """docs/configuration.md must exist and document HERMES_HOME."""
    config_doc = DOCS_DIR / "configuration.md"
    assert config_doc.exists(), "docs/configuration.md not found"
    assert "HERMES_HOME" in config_doc.read_text()
