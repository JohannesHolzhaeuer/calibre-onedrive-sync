"""Tests for persisted settings and shared helpers."""

from __future__ import annotations

import logging

from onedrive_sync.common import config, i18n, log


def test_defaults_and_roundtrip():
    settings = config.Settings()
    assert settings.library_path == ""
    assert settings.remote_subfolder == "calibre-library"
    assert settings.sync_mode == "backup"
    assert settings.as_dict()["include_metadata_db"] is True

    restored = config.Settings.from_dict(settings.as_dict())
    assert restored == settings


def test_from_dict_sanitizes_unknown_mode_and_missing_values():
    settings = config.Settings.from_dict({"sync_mode": "sideways", "library_path": None})
    assert settings.sync_mode == "backup"
    assert settings.library_path == ""


def test_load_and_save_roundtrip(prefs_backend):
    settings = config.Settings(
        library_path="C:/Books",
        onedrive_folder="C:/OneDrive",
        remote_subfolder="library",
        sync_mode="mirror",
        delete_removed=True,
        debug=True,
    )

    config.save_settings(settings)

    assert config.load_settings() == settings
    assert prefs_backend.get("sync_mode") == "mirror"


def test_load_settings_outside_calibre(monkeypatch):
    monkeypatch.setattr(config, "prefs", None)
    assert config.load_settings() == config.Settings()
    config.save_settings(config.Settings(library_path="ignored"))


def test_is_configured(prefs_backend):
    assert config.is_configured() is False

    config.save_settings(config.Settings(library_path="C:/Books", onedrive_folder="C:/OneDrive"))
    assert config.is_configured() is True

    settings = config.Settings(library_path="C:/Books", onedrive_folder=" ", remote_subfolder="x")
    assert config.is_configured(settings) is False


def test_logger_levels():
    assert log.get_logger("onedrive_sync.test").level == logging.WARNING
    assert log.get_logger("onedrive_sync.test", debug=True).level == logging.DEBUG


def test_i18n_identity_and_bind():
    assert i18n._("Hello") == "Hello"
    i18n._.bind(lambda text: f"de:{text}")
    assert i18n._("Hello") == "de:Hello"
    i18n._.bind(None)  # non-callable falls back to identity
    assert i18n._("Hello") == "Hello"


def test_i18n_broken_translator_falls_back():
    def boom(_text):
        raise RuntimeError("broken")

    i18n._.bind(boom)
    assert i18n._("Hello") == "Hello"
    i18n._.bind(lambda text: text)


def test_i18n_activate_without_injection_is_noop():
    module = {}
    i18n.activate(module)
    assert i18n._("Hello") == "Hello"


def test_i18n_activate_uses_injected_translator():
    module = {}

    def fake_load_translations():
        module["_"] = lambda text: f"xx:{text}"

    module["load_translations"] = fake_load_translations
    i18n.activate(module)
    assert i18n._("Hello") == "xx:Hello"
    i18n._.bind(lambda text: text)


def test_i18n_activate_swallows_broken_injection():
    module = {}

    def broken():
        raise RuntimeError("no locale")

    module["load_translations"] = broken
    i18n.activate(module)
    assert i18n._("Hello") == "Hello"
