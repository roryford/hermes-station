"""Tests for _seed_himalaya_config and _himalaya_backend_config.

Covers: Gmail/iCloud/generic domain inference, no-op on missing creds,
atomic write with mode 0o600, and config.toml content shape.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from hermes_station.config import _himalaya_backend_config, _seed_himalaya_config


# ---------------------------------------------------------------------------
# _himalaya_backend_config: domain inference
# ---------------------------------------------------------------------------


def test_gmail_imap_smtp() -> None:
    cfg = _himalaya_backend_config("me@gmail.com", "secret")
    assert 'backend.host = "imap.gmail.com"' in cfg
    assert 'message.send.backend.host = "smtp.gmail.com"' in cfg


def test_gmail_folder_aliases() -> None:
    cfg = _himalaya_backend_config("me@gmail.com", "secret")
    assert 'folder.aliases.sent = "[Gmail]/Sent Mail"' in cfg
    assert 'folder.aliases.drafts = "[Gmail]/Drafts"' in cfg
    assert 'folder.aliases.trash = "[Gmail]/Trash"' in cfg


def test_googlemail_treated_as_gmail() -> None:
    cfg = _himalaya_backend_config("me@googlemail.com", "secret")
    assert 'backend.host = "imap.gmail.com"' in cfg


def test_icloud_imap_smtp() -> None:
    cfg = _himalaya_backend_config("me@icloud.com", "secret")
    assert 'backend.host = "imap.mail.me.com"' in cfg
    assert 'message.send.backend.host = "smtp.mail.me.com"' in cfg


def test_icloud_me_domain() -> None:
    cfg = _himalaya_backend_config("me@me.com", "secret")
    assert 'backend.host = "imap.mail.me.com"' in cfg


def test_generic_domain_infers_imap_smtp() -> None:
    cfg = _himalaya_backend_config("user@example.com", "secret")
    assert 'backend.host = "imap.example.com"' in cfg
    assert 'message.send.backend.host = "smtp.example.com"' in cfg


def test_generic_standard_folder_aliases() -> None:
    cfg = _himalaya_backend_config("user@example.com", "secret")
    assert 'folder.aliases.sent = "Sent"' in cfg
    assert 'folder.aliases.drafts = "Drafts"' in cfg
    assert 'folder.aliases.trash = "Trash"' in cfg


def test_password_embedded() -> None:
    cfg = _himalaya_backend_config("user@example.com", "my-app-pw")
    assert cfg.count('backend.auth.raw = "my-app-pw"') == 2  # imap + smtp


def test_email_embedded_and_default_flag() -> None:
    cfg = _himalaya_backend_config("user@example.com", "pw")
    assert 'email = "user@example.com"' in cfg
    assert "default = true" in cfg


def test_display_name_included_when_provided() -> None:
    cfg = _himalaya_backend_config("me@gmail.com", "pw", "Hermes Bot")
    assert 'display-name = "Hermes Bot"' in cfg


def test_display_name_omitted_when_empty() -> None:
    cfg = _himalaya_backend_config("me@gmail.com", "pw", "")
    assert "display-name" not in cfg


def test_display_name_omitted_when_not_passed() -> None:
    cfg = _himalaya_backend_config("me@gmail.com", "pw")
    assert "display-name" not in cfg


def test_password_with_double_quote_is_escaped() -> None:
    cfg = _himalaya_backend_config("user@example.com", 'p@ss"word')
    assert r'backend.auth.raw = "p@ss\"word"' in cfg


def test_password_with_backslash_is_escaped() -> None:
    cfg = _himalaya_backend_config("user@example.com", "p\\ass")
    assert r'backend.auth.raw = "p\\ass"' in cfg


def test_display_name_with_quote_is_escaped() -> None:
    cfg = _himalaya_backend_config("user@example.com", "pw", 'My "Bot"')
    assert r'display-name = "My \"Bot\""' in cfg


def test_plural_folder_aliases_not_singular() -> None:
    """Ensure we use the v1.2.0 plural `folder.aliases.X` syntax, not the
    pre-v1.2.0 `folder.alias` sub-section that himalaya silently ignores."""
    cfg = _himalaya_backend_config("user@example.com", "pw")
    assert "folder.aliases." in cfg
    assert "folder.alias." not in cfg


# ---------------------------------------------------------------------------
# _seed_himalaya_config: file-write behaviour
# ---------------------------------------------------------------------------


def test_display_name_written_from_env(tmp_path: Path) -> None:
    _seed_himalaya_config(
        {
            "EMAIL_ADDRESS": "me@gmail.com",
            "EMAIL_PASSWORD": "pw",
            "EMAIL_DISPLAY_NAME": "My Bot",
            "HOME": str(tmp_path),
        }
    )
    cfg = (tmp_path / ".config" / "himalaya" / "config.toml").read_text()
    assert 'display-name = "My Bot"' in cfg


def test_display_name_absent_when_var_unset(tmp_path: Path) -> None:
    _seed_himalaya_config({"EMAIL_ADDRESS": "me@gmail.com", "EMAIL_PASSWORD": "pw", "HOME": str(tmp_path)})
    cfg = (tmp_path / ".config" / "himalaya" / "config.toml").read_text()
    assert "display-name" not in cfg


def test_writes_config_toml(tmp_path: Path) -> None:
    _seed_himalaya_config({"EMAIL_ADDRESS": "me@gmail.com", "EMAIL_PASSWORD": "pw", "HOME": str(tmp_path)})
    cfg = tmp_path / ".config" / "himalaya" / "config.toml"
    assert cfg.exists()
    assert "imap.gmail.com" in cfg.read_text()


def test_mode_0600(tmp_path: Path) -> None:
    _seed_himalaya_config({"EMAIL_ADDRESS": "me@gmail.com", "EMAIL_PASSWORD": "pw", "HOME": str(tmp_path)})
    cfg = tmp_path / ".config" / "himalaya" / "config.toml"
    mode = stat.S_IMODE(cfg.stat().st_mode)
    assert mode == 0o600


def test_creates_parent_dirs(tmp_path: Path) -> None:
    _seed_himalaya_config({"EMAIL_ADDRESS": "me@gmail.com", "EMAIL_PASSWORD": "pw", "HOME": str(tmp_path)})
    assert (tmp_path / ".config" / "himalaya").is_dir()


def test_noop_when_email_missing(tmp_path: Path) -> None:
    _seed_himalaya_config({"EMAIL_PASSWORD": "pw", "HOME": str(tmp_path)})
    assert not (tmp_path / ".config" / "himalaya" / "config.toml").exists()


def test_noop_when_password_missing(tmp_path: Path) -> None:
    _seed_himalaya_config({"EMAIL_ADDRESS": "me@gmail.com", "HOME": str(tmp_path)})
    assert not (tmp_path / ".config" / "himalaya" / "config.toml").exists()


def test_noop_when_both_missing(tmp_path: Path) -> None:
    _seed_himalaya_config({"HOME": str(tmp_path)})
    assert not (tmp_path / ".config" / "himalaya" / "config.toml").exists()


def test_noop_when_email_empty(tmp_path: Path) -> None:
    _seed_himalaya_config({"EMAIL_ADDRESS": "  ", "EMAIL_PASSWORD": "pw", "HOME": str(tmp_path)})
    assert not (tmp_path / ".config" / "himalaya" / "config.toml").exists()


def test_overwrites_on_second_call(tmp_path: Path) -> None:
    _seed_himalaya_config({"EMAIL_ADDRESS": "a@gmail.com", "EMAIL_PASSWORD": "old", "HOME": str(tmp_path)})
    _seed_himalaya_config({"EMAIL_ADDRESS": "b@example.com", "EMAIL_PASSWORD": "new", "HOME": str(tmp_path)})
    cfg = (tmp_path / ".config" / "himalaya" / "config.toml").read_text()
    assert "b@example.com" in cfg
    assert "a@gmail.com" not in cfg


# ---------------------------------------------------------------------------
# Env-pop behaviour: after a successful write, EMAIL_PASSWORD must be removed
# from os.environ so the in-process agent cannot read it from env dumps.
# EMAIL_ADDRESS is not sensitive and must stay in place.
# ---------------------------------------------------------------------------


def test_pops_email_password_from_os_environ_after_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("EMAIL_ADDRESS", "me@gmail.com")
    monkeypatch.setenv("EMAIL_PASSWORD", "supersecret")
    _seed_himalaya_config(os.environ)
    assert "EMAIL_PASSWORD" not in os.environ
    # File still has the credential — himalaya needs it.
    cfg = (tmp_path / ".config" / "himalaya" / "config.toml").read_text()
    assert 'backend.auth.raw = "supersecret"' in cfg


def test_does_not_pop_email_address(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("EMAIL_ADDRESS", "me@gmail.com")
    monkeypatch.setenv("EMAIL_PASSWORD", "pw")
    _seed_himalaya_config(os.environ)
    assert os.environ.get("EMAIL_ADDRESS") == "me@gmail.com"
    assert "EMAIL_PASSWORD" not in os.environ


def test_does_not_pop_when_no_write_happened(monkeypatch: pytest.MonkeyPatch) -> None:
    # Password set but address missing → no file write, no pop.
    monkeypatch.delenv("EMAIL_ADDRESS", raising=False)
    monkeypatch.setenv("EMAIL_PASSWORD", "still-here")
    _seed_himalaya_config(os.environ)
    assert os.environ.get("EMAIL_PASSWORD") == "still-here"


def test_pop_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Second call with the password already gone must not raise."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("EMAIL_ADDRESS", "me@gmail.com")
    monkeypatch.setenv("EMAIL_PASSWORD", "pw")
    _seed_himalaya_config(os.environ)
    # Password is gone; second call sees no password → noop, no error.
    _seed_himalaya_config(os.environ)
    assert "EMAIL_PASSWORD" not in os.environ
