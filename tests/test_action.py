"""Tests for the toolbar action entry point and plugin class."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from onedrive_sync.common.config import Settings
from onedrive_sync.gui import action as action_mod
from onedrive_sync.gui.action import OneDriveSyncInterfaceAction


@pytest.fixture()
def action():
    return OneDriveSyncInterfaceAction(gui=SimpleNamespace())


def test_genesis_sets_icon_and_menu(monkeypatch, action):
    monkeypatch.setattr(action_mod, "get_icons", lambda path, name: f"{name}:{path}", raising=False)

    action.genesis()

    assert action.qaction.icon == "onedrive_sync:resources/images/onedrive.png"
    menu = action.qaction.menu()
    assert [text for text, _slot in menu.actions()] == ["Sync now", "Open configuration…"]


def test_library_path_reads_current_db(action):
    action.gui.current_db = SimpleNamespace(library_path="C:/Books")
    assert action._library_path() == "C:/Books"

    action.gui = SimpleNamespace()
    assert action._library_path() == ""


def test_open_config_delegates(monkeypatch, action):
    calls = []
    action.gui.current_db = SimpleNamespace(library_path="C:/Books")
    monkeypatch.setattr(
        action_mod,
        "open_config",
        lambda gui, *, default_library_path="": calls.append((gui, default_library_path)) or True,
    )

    assert action.open_config() is True
    assert calls == [(action.gui, "C:/Books")]


def test_sync_now_aborts_when_config_is_cancelled(monkeypatch, action):
    monkeypatch.setattr(action_mod, "load_settings", lambda: Settings())
    monkeypatch.setattr(action_mod, "is_configured", lambda settings: False)
    monkeypatch.setattr(action, "open_config", lambda: False)
    called = []
    monkeypatch.setattr(action_mod.QMessageBox, "information", lambda *a, **k: called.append(a))

    action.sync_now()

    assert called == []


def test_sync_now_aborts_when_config_stays_invalid(monkeypatch, action):
    monkeypatch.setattr(action_mod, "load_settings", lambda: Settings())
    monkeypatch.setattr(action_mod, "is_configured", lambda settings: False)
    monkeypatch.setattr(action, "open_config", lambda: True)
    called = []
    monkeypatch.setattr(action_mod.QMessageBox, "information", lambda *a, **k: called.append(a))

    action.sync_now()

    assert called == []


def test_sync_now_reports_configured_settings(monkeypatch, action):
    settings = Settings(library_path="C:/Books", onedrive_folder="C:/OneDrive")
    monkeypatch.setattr(action_mod, "load_settings", lambda: settings)
    monkeypatch.setattr(action_mod, "is_configured", lambda value: True)
    monkeypatch.setattr(action_mod, "validate_settings", lambda value: [])
    called = []
    monkeypatch.setattr(action_mod.QMessageBox, "information", lambda *a, **k: called.append(a))

    action.sync_now()

    assert len(called) == 1


def test_sync_now_reopens_config_when_validation_fails(monkeypatch, action):
    settings = Settings(library_path="C:/Books", onedrive_folder="C:/OneDrive")
    config_calls = []
    monkeypatch.setattr(action_mod, "load_settings", lambda: settings)
    monkeypatch.setattr(action_mod, "is_configured", lambda value: True)
    monkeypatch.setattr(action_mod, "validate_settings", lambda value: ["broken"])
    monkeypatch.setattr(
        action,
        "open_config",
        lambda: config_calls.append(True) or True,
    )
    called = []
    monkeypatch.setattr(action_mod.QMessageBox, "information", lambda *a, **k: called.append(a))

    action.sync_now()

    assert config_calls == [True]
    assert called == []


def test_plugin_initialize_and_do_user_config(monkeypatch):
    from onedrive_sync import OneDriveSyncAction

    plugin = OneDriveSyncAction()
    activated = []
    monkeypatch.setattr("onedrive_sync.common.i18n.activate", activated.append)
    plugin.initialize()
    assert activated == [vars(__import__("onedrive_sync"))]

    opened = []
    monkeypatch.setattr(
        "onedrive_sync.gui.config_widget.open_config",
        lambda parent=None, *, default_library_path="": opened.append(
            (parent, default_library_path)
        ),
    )
    gui = SimpleNamespace(current_db=SimpleNamespace(library_path="C:/Books"))
    plugin.do_user_config(gui)
    assert opened == [(gui, "C:/Books")]
    assert "OneDrive" in plugin.customization_help()

    plugin.do_user_config(None)
    assert opened[-1] == (None, "")
