"""Configuration widget for the OneDrive sync.

The widget is intentionally thin: all validation and path logic lives in
:mod:`onedrive_sync.common.paths`, which is unit-testable without Qt. The
OneDrive folder is the *locally synced* folder maintained by the OneDrive
desktop client; the plugin never talks to the cloud API directly.
"""

from __future__ import annotations

from qt.core import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..common.config import Settings, load_settings, save_settings
from ..common.i18n import _
from ..common.paths import default_onedrive_folder, target_path, validate_settings


class ConfigWidget(QWidget):
    """Preferences widget: pick the library and the OneDrive destination."""

    def __init__(self, parent=None, *, default_library_path: str = "") -> None:
        super().__init__(parent)
        self._default_library_path = default_library_path
        self._build_ui()
        self.load()

    # -- UI construction ----------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(_("Calibre library folder")))
        library_row = QHBoxLayout()
        self.library_path = QLineEdit()
        self.library_path.setPlaceholderText(_("Path to your calibre library"))
        self.library_path.textChanged.connect(self._update_preview)
        self.library_browse = QPushButton(_("Browse…"))
        self.library_browse.clicked.connect(self._browse_library)
        library_row.addWidget(self.library_path, 1)
        library_row.addWidget(self.library_browse)
        layout.addLayout(library_row)

        layout.addWidget(QLabel(_("OneDrive folder")))
        onedrive_row = QHBoxLayout()
        self.onedrive_folder = QLineEdit()
        self.onedrive_folder.setPlaceholderText(_("Path to your local OneDrive folder"))
        self.onedrive_folder.textChanged.connect(self._update_preview)
        self.onedrive_browse = QPushButton(_("Browse…"))
        self.onedrive_browse.clicked.connect(self._browse_onedrive)
        onedrive_row.addWidget(self.onedrive_folder, 1)
        onedrive_row.addWidget(self.onedrive_browse)
        layout.addLayout(onedrive_row)

        layout.addWidget(QLabel(_("Folder inside OneDrive")))
        self.remote_subfolder = QLineEdit()
        self.remote_subfolder.setPlaceholderText(_("e.g. calibre-library"))
        self.remote_subfolder.textChanged.connect(self._update_preview)
        layout.addWidget(self.remote_subfolder)

        layout.addWidget(QLabel(_("Sync mode")))
        self.sync_mode = QComboBox()
        self.sync_mode.addItem(_("Backup — copy new and changed files"), "backup")
        self.sync_mode.addItem(_("Mirror — make OneDrive match the library"), "mirror")
        self.sync_mode.currentIndexChanged.connect(self._on_mode_changed)
        layout.addWidget(self.sync_mode)

        self.delete_removed = QCheckBox(
            _("Delete files in OneDrive that are gone from the library")
        )
        layout.addWidget(self.delete_removed)

        self.include_metadata_db = QCheckBox(_("Include calibre metadata.db"))
        layout.addWidget(self.include_metadata_db)

        self.debug = QCheckBox(_("Debug (verbose logging)"))
        layout.addWidget(self.debug)

        self.preview = QLabel("")
        self.preview.setWordWrap(True)
        layout.addWidget(self.preview)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    # -- public API ---------------------------------------------------------

    def load(self) -> None:
        """Populate the widget from persisted settings."""
        settings = load_settings()
        self.library_path.setText(settings.library_path or self._default_library_path)
        self.onedrive_folder.setText(settings.onedrive_folder or default_onedrive_folder())
        self.remote_subfolder.setText(settings.remote_subfolder)
        index = self.sync_mode.findData(settings.sync_mode)
        self.sync_mode.setCurrentIndex(index if index >= 0 else 0)
        self.delete_removed.setChecked(settings.delete_removed)
        self.include_metadata_db.setChecked(settings.include_metadata_db)
        self.debug.setChecked(settings.debug)
        self._on_mode_changed()
        self._update_preview()
        self._show_status("")

    def fields(self) -> dict:
        """Collect the current widget values into a settings dict."""
        mode = self.sync_mode.currentData() or "backup"
        return {
            "library_path": self.library_path.text().strip(),
            "onedrive_folder": self.onedrive_folder.text().strip(),
            "remote_subfolder": self.remote_subfolder.text().strip(),
            "sync_mode": mode,
            # Deletion only makes sense in mirror mode; never persist a stale
            # checkbox from a previous mirror selection.
            "delete_removed": mode == "mirror" and self.delete_removed.isChecked(),
            "include_metadata_db": self.include_metadata_db.isChecked(),
            "debug": self.debug.isChecked(),
        }

    def validate(self) -> bool:
        """Validate the current fields; show inline errors and return False on failure."""
        errors = validate_settings(Settings.from_dict(self.fields()))
        if errors:
            self.status.setText("\n".join(errors))
            self.status.setStyleSheet("color: #a00;")
            return False
        self._show_status(_("Configuration looks good."), ok=True)
        return True

    def apply(self) -> None:
        """Persist the current fields."""
        save_settings(Settings.from_dict(self.fields()))

    # -- signals / slots ----------------------------------------------------

    def _browse_library(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, _("Select calibre library folder"), self.library_path.text()
        )
        if path:
            self.library_path.setText(path)

    def _browse_onedrive(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, _("Select OneDrive folder"), self.onedrive_folder.text()
        )
        if path:
            self.onedrive_folder.setText(path)

    def _on_mode_changed(self, *_args) -> None:
        self.delete_removed.setEnabled(self.sync_mode.currentData() == "mirror")

    def _update_preview(self, *_args) -> None:
        target = target_path(Settings.from_dict(self.fields()))
        if target:
            self.preview.setText(_("OneDrive target: {path}").format(path=target))
        else:
            self.preview.setText("")

    def _show_status(self, message: str, *, ok: bool = False) -> None:
        self.status.setText(message)
        self.status.setStyleSheet("color: #080;" if ok else "")


def open_config(parent=None, *, default_library_path: str = "") -> bool:
    """Open the configuration dialog and apply on accept.

    Reused by the action menu (``gui.action``) and the *Customize plugin* hook.
    Returns True when the user accepted (and settings were saved).
    """
    dialog = QDialog(parent)
    dialog.setWindowTitle(_("OneDrive Sync – Configuration"))
    layout = QVBoxLayout(dialog)
    widget = ConfigWidget(dialog, default_library_path=default_library_path)
    layout.addWidget(widget)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
    )
    buttons.accepted.connect(lambda: widget.validate() and dialog.accept())
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)

    if dialog.exec() != QDialog.DialogCode.Accepted:
        return False
    widget.apply()
    return True
