"""Tests for the configuration widget."""

from __future__ import annotations

from qt.core import QDialog

from onedrive_sync.common.config import (
    MAX_UPLOAD_WORKERS,
    MIN_UPLOAD_WORKERS,
    Settings,
)
from onedrive_sync.common.models import FolderRef
from onedrive_sync.gui import config_widget
from onedrive_sync.gui.config_widget import ConfigWidget, open_config

FOLDER = FolderRef("drive", "folder", "Books", "/Books")


def base_settings(**overrides):
    values = {
        "client_id": "client",
        "account_name": "Alice",
        "drive_id": "drive",
        "folder_id": "folder",
        "folder_path": "/Books",
        "folder_name": "Books",
        "export_subfolder": "Export",
        "export_directly": False,
        "export_format": "pdf",
        "import_enabled": True,
        "export_enabled": True,
        "auto_sync": True,
        "sync_interval_minutes": 30,
        "debug": True,
    }
    values.update(overrides)
    return Settings(**values)


def widget_with(monkeypatch, settings=None, token="refresh"):
    monkeypatch.setattr(config_widget, "load_settings", lambda: settings or base_settings())
    monkeypatch.setattr(config_widget, "get_refresh_token", lambda: token)
    return ConfigWidget()


def test_load_and_fields(monkeypatch):
    widget = widget_with(monkeypatch)
    assert widget.client_id.text() == "client"
    assert widget.folder_label.text() == "OneDrive folder: /Books"
    assert widget.export_format.currentData() == "pdf"
    assert widget.debug.isChecked()
    assert widget.preview.text() == "Books are uploaded directly to: /Books"
    fields = widget.fields()
    assert fields["drive_id"] == "drive"
    assert fields["folder_id"] == "folder"
    assert fields["export_format"] == "pdf"
    assert "export_subfolder" not in fields


def test_the_upload_workers_box_round_trips_the_setting(monkeypatch):
    widget = widget_with(monkeypatch, settings=base_settings(upload_workers=6))
    assert widget.upload_workers.value() == 6
    assert widget.fields()["upload_workers"] == 6

    widget.upload_workers.setValue(99)
    assert widget.upload_workers.value() == MAX_UPLOAD_WORKERS
    widget.upload_workers.setValue(-3)
    assert widget.upload_workers.value() == MIN_UPLOAD_WORKERS


def test_load_without_folder_or_token(monkeypatch):
    widget = widget_with(
        monkeypatch,
        settings=base_settings(drive_id="", folder_id="", folder_path="", folder_name=""),
        token="",
    )
    assert widget.folder_label.text() == "No OneDrive folder selected"
    assert widget.auth_status.text() == "Not signed in"
    assert not widget.sign_out_button.isEnabled()
    assert widget.preview.text() == ""


def test_validate_success(monkeypatch):
    widget = widget_with(monkeypatch)
    assert widget.validate()
    assert widget.status.text() == "Configuration looks good."


def test_validate_requires_sign_in_after_client_id_change(monkeypatch):
    widget = widget_with(monkeypatch)
    widget.client_id.setText("different-client")
    assert not widget.validate()
    assert "Sign in again" in widget.status.text()


def test_validate_allows_first_sign_in_before_client_id_is_saved(monkeypatch):
    widget = widget_with(monkeypatch, settings=base_settings(client_id=""))
    widget.client_id.setText("client")
    assert widget.validate()


def test_validate_compares_client_id_case_insensitively(monkeypatch):
    widget = widget_with(monkeypatch, settings=base_settings(client_id="CLIENT"))
    widget.client_id.setText("client")
    assert widget.validate()


def test_validate_detects_client_id_change_after_sign_in(monkeypatch):
    widget = widget_with(monkeypatch)
    monkeypatch.setattr(
        config_widget,
        "sign_in",
        lambda _parent, _auth, profile_loader=None: (True, {"displayName": "Alice"}),
    )
    widget._on_sign_in()
    widget.client_id.setText("different-client")
    assert not widget.validate()
    assert "Sign in again" in widget.status.text()


def test_validate_reports_all_errors(monkeypatch):
    widget = widget_with(
        monkeypatch,
        settings=base_settings(drive_id="", folder_id="", folder_path="", folder_name=""),
        token="",
    )
    widget.client_id.setText("")
    assert not widget.validate()
    text = widget.status.text()
    assert "client" in text
    assert "Sign in" in text
    assert "folder" in text


def test_apply_saves_fields(monkeypatch):
    saved = []
    widget = widget_with(monkeypatch)
    monkeypatch.setattr(config_widget, "save_settings", lambda settings: saved.append(settings))
    widget.apply()
    assert saved[0].folder_id == "folder"


def test_sign_in_requires_client_id(monkeypatch):
    widget = widget_with(monkeypatch)
    widget.client_id.setText("")
    widget._on_sign_in()
    assert "client" in widget.status.text()


def test_sign_in_success(monkeypatch):
    widget = widget_with(monkeypatch)
    monkeypatch.setattr(
        config_widget,
        "sign_in",
        lambda _parent, _auth, profile_loader=None: (True, {"displayName": "Alice"}),
    )
    widget._on_sign_in()
    assert "Alice" in widget.auth_status.text()
    assert "Signed in" in widget.status.text()


def test_sign_in_cancelled(monkeypatch):
    widget = widget_with(monkeypatch)
    monkeypatch.setattr(
        config_widget,
        "sign_in",
        lambda _parent, _auth, profile_loader=None: (False, {}),
    )
    widget._on_sign_in()
    assert "cancelled" in widget.status.text()


def test_sign_in_shows_the_reported_failure(monkeypatch):
    widget = widget_with(monkeypatch)
    monkeypatch.setattr(
        config_widget,
        "sign_in",
        lambda _parent, _auth, profile_loader=None: (
            False,
            {"error": "connection reset by peer"},
        ),
    )
    widget._on_sign_in()
    assert "connection reset by peer" in widget.status.text()
    assert "cancelled" not in widget.status.text().lower()


def test_sign_in_exception(monkeypatch):
    widget = widget_with(monkeypatch)

    def fail(*_args, **_kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(config_widget, "sign_in", fail)
    widget._on_sign_in()
    assert "network down" in widget.status.text()


def test_sign_out_clears_session(monkeypatch):
    widget = widget_with(monkeypatch)
    cleared = []
    monkeypatch.setattr(config_widget, "clear_refresh_token", lambda: cleared.append(1))
    widget._on_sign_out()
    assert cleared
    assert widget._folder is None
    assert widget.folder_label.text() == "No OneDrive folder selected"
    assert "Signed out" in widget.status.text()


def test_choose_folder_requires_client_and_token(monkeypatch):
    widget = widget_with(monkeypatch)
    widget.client_id.setText("")
    widget._on_choose_folder()
    assert "client" in widget.status.text()

    widget = widget_with(monkeypatch, token="")
    widget._on_choose_folder()
    assert "Sign in" in widget.status.text()


def test_choose_folder_success(monkeypatch):
    widget = widget_with(monkeypatch)
    monkeypatch.setattr(
        config_widget, "open_folder_picker", lambda _parent, _graph, initial=None: FOLDER
    )
    widget._on_choose_folder()
    assert widget._folder == FOLDER
    assert "Folder selected" in widget.status.text()


def test_choose_folder_cancelled_and_exception(monkeypatch):
    widget = widget_with(monkeypatch)
    monkeypatch.setattr(
        config_widget, "open_folder_picker", lambda _parent, _graph, initial=None: None
    )
    widget._on_choose_folder()
    assert widget._folder is not None

    def fail(*_args, **_kwargs):
        raise RuntimeError("graph down")

    monkeypatch.setattr(config_widget, "open_folder_picker", fail)
    widget._on_choose_folder()
    assert "graph down" in widget.status.text()


def test_open_config_applies(monkeypatch):
    saved = []

    class AcceptedDialog(QDialog):
        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(config_widget, "QDialog", AcceptedDialog)
    monkeypatch.setattr(config_widget, "load_settings", lambda: base_settings())
    monkeypatch.setattr(config_widget, "get_refresh_token", lambda: "refresh")
    monkeypatch.setattr(config_widget, "save_settings", lambda settings: saved.append(settings))

    assert open_config()
    assert saved and saved[0].folder_id == "folder"


def test_open_config_cancelled(monkeypatch):
    class RejectedDialog(QDialog):
        def exec(self):
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(config_widget, "QDialog", RejectedDialog)
    monkeypatch.setattr(config_widget, "load_settings", lambda: base_settings())
    monkeypatch.setattr(config_widget, "get_refresh_token", lambda: "refresh")
    assert not open_config()
