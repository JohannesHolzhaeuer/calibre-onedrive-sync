"""Tests for the Qt configuration widget."""

from __future__ import annotations

import qt.core as qtcore

from onedrive_sync.common import config
from onedrive_sync.gui import config_widget


def _widget(monkeypatch, prefs_backend, tmp_path, **settings):
    monkeypatch.setattr(config_widget, "default_onedrive_folder", lambda: str(tmp_path / "od"))
    for key, value in settings.items():
        prefs_backend.set(key, value)
    return config_widget.ConfigWidget(None, default_library_path=str(tmp_path / "default-lib"))


def test_load_populates_existing_settings(monkeypatch, prefs_backend, tmp_path):
    library = tmp_path / "library"
    onedrive = tmp_path / "onedrive"
    library.mkdir()
    onedrive.mkdir()

    widget = _widget(
        monkeypatch,
        prefs_backend,
        tmp_path,
        library_path=str(library),
        onedrive_folder=str(onedrive),
        remote_subfolder="backup",
        sync_mode="mirror",
        delete_removed=True,
        include_metadata_db=False,
        debug=True,
    )

    assert widget.library_path.text() == str(library)
    assert widget.onedrive_folder.text() == str(onedrive)
    assert widget.remote_subfolder.text() == "backup"
    assert widget.sync_mode.currentData() == "mirror"
    assert widget.delete_removed.isChecked() is True
    assert widget.delete_removed.isEnabled() is True
    assert widget.include_metadata_db.isChecked() is False
    assert widget.debug.isChecked() is True
    assert str(onedrive / "backup") in widget.preview.text()


def test_load_uses_defaults(monkeypatch, prefs_backend, tmp_path):
    widget = _widget(monkeypatch, prefs_backend, tmp_path)

    assert widget.library_path.text() == str(tmp_path / "default-lib")
    assert widget.onedrive_folder.text() == str(tmp_path / "od")
    assert widget.remote_subfolder.text() == "calibre-library"
    assert widget.sync_mode.currentData() == "backup"
    assert widget.delete_removed.isEnabled() is False
    assert str(tmp_path / "od" / "calibre-library") in widget.preview.text()


def test_fields_and_apply(monkeypatch, prefs_backend, tmp_path):
    widget = _widget(monkeypatch, prefs_backend, tmp_path)
    widget.library_path.setText("C:/Books")
    widget.onedrive_folder.setText("C:/OneDrive")
    widget.remote_subfolder.setText("library")
    widget.sync_mode.setCurrentIndex(widget.sync_mode.findData("mirror"))
    widget.delete_removed.setChecked(True)
    widget.include_metadata_db.setChecked(False)
    widget.debug.setChecked(True)

    fields = widget.fields()
    assert fields["sync_mode"] == "mirror"
    assert fields["delete_removed"] is True

    widget.apply()

    saved = config.load_settings()
    assert saved.library_path == "C:/Books"
    assert saved.sync_mode == "mirror"
    assert saved.delete_removed is True
    assert saved.include_metadata_db is False
    assert saved.debug is True


def test_delete_removed_only_persists_in_mirror_mode(monkeypatch, prefs_backend, tmp_path):
    widget = _widget(monkeypatch, prefs_backend, tmp_path)
    widget.delete_removed.setChecked(True)

    widget.sync_mode.setCurrentIndex(widget.sync_mode.findData("backup"))
    assert widget.fields()["delete_removed"] is False

    widget.sync_mode.setCurrentIndex(widget.sync_mode.findData("mirror"))
    assert widget.fields()["delete_removed"] is True


def test_validate_reports_errors_and_success(monkeypatch, prefs_backend, tmp_path):
    widget = _widget(monkeypatch, prefs_backend, tmp_path)

    assert widget.validate() is False
    assert widget.status.text()
    assert widget.status.styleSheet() == "color: #a00;"

    library = tmp_path / "library"
    onedrive = tmp_path / "onedrive"
    library.mkdir()
    onedrive.mkdir()
    widget.library_path.setText(str(library))
    widget.onedrive_folder.setText(str(onedrive))

    assert widget.validate() is True
    assert widget.status.styleSheet() == "color: #080;"


def test_browse_buttons(monkeypatch, prefs_backend, tmp_path):
    widget = _widget(monkeypatch, prefs_backend, tmp_path)
    answers = [str(tmp_path / "picked-lib"), ""]
    monkeypatch.setattr(
        config_widget.QFileDialog,
        "getExistingDirectory",
        staticmethod(lambda *args, **kwargs: answers.pop(0)),
    )

    widget._browse_library()
    assert widget.library_path.text() == str(tmp_path / "picked-lib")

    widget._browse_onedrive()
    assert widget.onedrive_folder.text() == str(tmp_path / "od")


def test_mode_toggle_and_preview_clearing(monkeypatch, prefs_backend, tmp_path):
    widget = _widget(monkeypatch, prefs_backend, tmp_path)

    widget.sync_mode.setCurrentIndex(widget.sync_mode.findData("mirror"))
    widget._on_mode_changed()
    assert widget.delete_removed.isEnabled() is True

    widget.sync_mode.setCurrentIndex(widget.sync_mode.findData("backup"))
    widget._on_mode_changed()
    assert widget.delete_removed.isEnabled() is False

    widget.library_path.setText("")
    widget.onedrive_folder.setText("")
    widget._update_preview()
    assert widget.preview.text() == ""


def test_open_config_accepts_and_applies(monkeypatch):
    events = []

    class FakeDialog:
        class DialogCode:
            Accepted = 1
            Rejected = 0

        def __init__(self, parent=None):
            self.parent = parent
            self.result = self.DialogCode.Accepted

        def setWindowTitle(self, title):
            self.title = title

        def exec(self):
            return self.result

        def accept(self):
            events.append("accept")

        def reject(self):
            events.append("reject")

    class FakeWidget:
        def __init__(self, parent=None, *, default_library_path=""):
            self.default_library_path = default_library_path
            events.append(("widget", default_library_path))

        def validate(self):
            events.append("validate")
            return True

        def apply(self):
            events.append("apply")

    monkeypatch.setattr(config_widget, "QDialog", FakeDialog)
    monkeypatch.setattr(config_widget, "ConfigWidget", FakeWidget)

    assert config_widget.open_config(default_library_path="C:/Books") is True
    assert ("widget", "C:/Books") in events
    assert events[-1] == "apply"


def test_open_config_rejects(monkeypatch):
    class FakeDialog:
        class DialogCode:
            Accepted = 1
            Rejected = 0

        def __init__(self, parent=None):
            pass

        def setWindowTitle(self, title):
            pass

        def exec(self):
            return self.DialogCode.Rejected

        def accept(self):
            raise AssertionError("must not accept")

        def reject(self):
            pass

    class FakeWidget:
        def __init__(self, *args, **kwargs):
            pass

        def validate(self):
            return True

        def apply(self):
            raise AssertionError("must not apply")

    monkeypatch.setattr(config_widget, "QDialog", FakeDialog)
    monkeypatch.setattr(config_widget, "ConfigWidget", FakeWidget)

    assert config_widget.open_config() is False


def test_dialog_button_signal_wiring(monkeypatch):
    """Accepting the button box validates before accepting the dialog."""
    captured = {}

    class FakeSignal:
        def __init__(self):
            self.slot = None

        def connect(self, slot):
            self.slot = slot

    class FakeButtonBox:
        class StandardButton:
            Ok = 1
            Cancel = 2

        def __init__(self, buttons):
            self.buttons = buttons
            self.accepted = FakeSignal()
            self.rejected = FakeSignal()
            captured["box"] = self

    class FakeDialog:
        class DialogCode:
            Accepted = 1
            Rejected = 0

        def __init__(self, parent=None):
            self.accepted = False

        def setWindowTitle(self, title):
            pass

        def exec(self):
            captured["box"].accepted.slot()
            return self.DialogCode.Accepted if self.accepted else self.DialogCode.Rejected

        def accept(self):
            self.accepted = True

        def reject(self):
            pass

    class FakeWidget:
        def __init__(self, *args, **kwargs):
            pass

        def validate(self):
            captured["validated"] = True
            return True

        def apply(self):
            captured["applied"] = True

    monkeypatch.setattr(config_widget, "QDialog", FakeDialog)
    monkeypatch.setattr(config_widget, "ConfigWidget", FakeWidget)
    monkeypatch.setattr(config_widget, "QDialogButtonBox", FakeButtonBox)

    assert config_widget.open_config() is True
    assert captured["validated"] is True
    assert captured["applied"] is True


def test_qdialog_alias_is_available():
    assert qtcore.QDialog is config_widget.QDialog
