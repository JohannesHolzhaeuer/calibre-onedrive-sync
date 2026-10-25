"""Tests for the top-level plugin class wiring."""

from __future__ import annotations

from onedrive_sync import OneDriveSyncAction
from onedrive_sync.common import i18n


def test_initialize_activates_translations(monkeypatch):
    called = []
    monkeypatch.setattr(i18n, "activate", lambda namespace: called.append(namespace))
    OneDriveSyncAction().initialize()
    assert len(called) == 1


def test_do_user_config_opens_dialog(monkeypatch):
    from onedrive_sync.gui import config_widget

    parents = []
    monkeypatch.setattr(config_widget, "open_config", lambda parent=None: parents.append(parent))
    gui = object()
    OneDriveSyncAction().do_user_config(gui)
    assert parents == [gui]


def test_customization_help_is_translated_text():
    assert "OneDrive" in OneDriveSyncAction().customization_help()
