"""Configuration dialog for the OneDrive sync.

The widget is mostly glue: OAuth/Graph work lives in ``common`` and the folder
browser in ``gui.folder_picker``. All network calls made from here run in
worker threads (sign-in dialog / folder picker).
"""

from __future__ import annotations

from qt.core import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..common.auth import AuthManager
from ..common.config import (
    DEFAULT_EXPORT_FORMAT,
    EXPORT_FORMATS,
    MAX_SYNC_INTERVAL,
    MIN_SYNC_INTERVAL,
    ConfigTokenStore,
    Settings,
    clear_refresh_token,
    get_refresh_token,
    load_settings,
    save_settings,
)
from ..common.graph import GraphClient
from ..common.i18n import _
from ..common.models import FolderRef
from ..common.sync import normalize_export_subfolder
from .auth_dialog import sign_in
from .folder_picker import open_folder_picker


class ConfigWidget(QWidget):
    """Preferences widget: app ID, sign-in, target folder and sync options."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._folder: FolderRef | None = None
        self._account_name = ""
        self._signed_in_client_id = ""
        self._build_ui()
        self.load()

    # -- UI construction ----------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(_("Microsoft app (client) ID")))
        self.client_id = QLineEdit()
        self.client_id.setPlaceholderText(_("Application (client) ID from Entra ID"))
        layout.addWidget(self.client_id)
        help_label = QLabel(
            _(
                "A shared public Entra app is preconfigured. Replace this only if you "
                "use your own app registration with delegated Files.ReadWrite.All, "
                "User.Read and offline_access permissions."
            )
        )
        help_label.setWordWrap(True)
        layout.addWidget(help_label)

        auth_row = QHBoxLayout()
        self.auth_status = QLabel("")
        self.sign_in_button = QPushButton(_("Sign in…"))
        self.sign_in_button.clicked.connect(self._on_sign_in)
        self.sign_out_button = QPushButton(_("Sign out"))
        self.sign_out_button.clicked.connect(self._on_sign_out)
        auth_row.addWidget(self.auth_status, 1)
        auth_row.addWidget(self.sign_in_button)
        auth_row.addWidget(self.sign_out_button)
        layout.addLayout(auth_row)

        folder_row = QHBoxLayout()
        self.folder_label = QLabel("")
        self.folder_label.setWordWrap(True)
        self.choose_folder_button = QPushButton(_("Choose folder…"))
        self.choose_folder_button.clicked.connect(self._on_choose_folder)
        folder_row.addWidget(self.folder_label, 1)
        folder_row.addWidget(self.choose_folder_button)
        layout.addLayout(folder_row)

        layout.addWidget(QLabel(_("Export subfolder")))
        self.export_subfolder = QLineEdit()
        self.export_subfolder.setPlaceholderText(_("Calibre Export"))
        self.export_subfolder.textChanged.connect(self._update_preview)
        layout.addWidget(self.export_subfolder)

        self.export_directly = QCheckBox(
            _("Export directly into the selected folder (no subfolder)")
        )
        self.export_directly.toggled.connect(self._on_export_directly_toggled)
        layout.addWidget(self.export_directly)

        layout.addWidget(QLabel(_("Export format")))
        self.export_format = QComboBox()
        labels = {
            "epub_or_pdf": _("EPUB, falling back to PDF"),
            "epub": _("EPUB only"),
            "pdf": _("PDF only"),
        }
        for value in EXPORT_FORMATS:
            self.export_format.addItem(labels.get(value, value), value)
        layout.addWidget(self.export_format)

        self.import_enabled = QCheckBox(_("Import new books from OneDrive"))
        layout.addWidget(self.import_enabled)
        self.export_enabled = QCheckBox(_("Export books that are not in OneDrive yet"))
        layout.addWidget(self.export_enabled)

        self.auto_sync = QCheckBox(_("Sync automatically when the library or OneDrive changes"))
        layout.addWidget(self.auto_sync)

        interval_row = QHBoxLayout()
        interval_row.addWidget(QLabel(_("Check for changes every")))
        self.interval = QSpinBox()
        self.interval.setRange(MIN_SYNC_INTERVAL, MAX_SYNC_INTERVAL)
        self.interval.setSuffix(_(" min"))
        interval_row.addWidget(self.interval)
        interval_row.addStretch(1)
        layout.addLayout(interval_row)

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
        """Populate the widget from persisted settings and the token store."""
        settings = load_settings()
        self.client_id.setText(settings.client_id)
        self._account_name = settings.account_name
        if settings.folder_id:
            self._folder = FolderRef(
                drive_id=settings.drive_id,
                item_id=settings.folder_id,
                name=settings.folder_name,
                path=settings.folder_path,
            )
        else:
            self._folder = None
        index = self.export_format.findData(settings.export_format or DEFAULT_EXPORT_FORMAT)
        self.export_format.setCurrentIndex(index if index >= 0 else 0)
        self.export_subfolder.setText(settings.export_subfolder)
        self.export_directly.setChecked(settings.export_directly)
        self._on_export_directly_toggled(settings.export_directly)
        self.import_enabled.setChecked(settings.import_enabled)
        self.export_enabled.setChecked(settings.export_enabled)
        self.auto_sync.setChecked(settings.auto_sync)
        self.interval.setValue(settings.sync_interval_minutes)
        self.debug.setChecked(settings.debug)
        self._refresh_auth_status()
        self._refresh_folder_label()
        self._update_preview()
        self._show_status("")

    def fields(self) -> dict:
        """Collect the current widget values into a settings dict."""
        folder = self._folder
        return {
            "client_id": self.client_id.text().strip(),
            "account_name": self._account_name,
            "drive_id": folder.drive_id if folder else "",
            "folder_id": folder.item_id if folder else "",
            "folder_path": folder.path if folder else "",
            "folder_name": folder.name if folder else "",
            "export_subfolder": normalize_export_subfolder(self.export_subfolder.text()),
            "export_directly": self.export_directly.isChecked(),
            "export_format": self.export_format.currentData() or DEFAULT_EXPORT_FORMAT,
            "import_enabled": self.import_enabled.isChecked(),
            "export_enabled": self.export_enabled.isChecked(),
            "auto_sync": self.auto_sync.isChecked(),
            "sync_interval_minutes": self.interval.value(),
            "debug": self.debug.isChecked(),
        }

    def validate(self) -> bool:
        """Validate the current fields; show inline errors and return False on failure."""
        errors: list[str] = []
        saved = load_settings()
        client_id = self.client_id.text().strip()
        signed_client_id = self._signed_in_client_id.strip() or saved.client_id.strip()
        if not client_id:
            errors.append(_("Enter the Microsoft app (client) ID."))
        if not get_refresh_token():
            errors.append(_("Sign in to OneDrive."))
        elif signed_client_id and client_id.casefold() != signed_client_id.casefold():
            errors.append(_("Sign in again after changing the Microsoft app (client) ID."))
        if self._folder is None or not self._folder.item_id:
            errors.append(_("Choose the OneDrive folder to sync with."))
        if not self.import_enabled.isChecked() and not self.export_enabled.isChecked():
            errors.append(_("Enable importing, exporting, or both."))
        if not normalize_export_subfolder(self.export_subfolder.text()):
            errors.append(_("Enter a valid export subfolder."))
        if errors:
            self._show_status("\n".join(errors))
            return False
        self._show_status(_("Configuration looks good."), ok=True)
        return True

    def apply(self) -> None:
        """Persist the current fields."""
        save_settings(Settings.from_dict(self.fields()))

    # -- signals / slots ----------------------------------------------------

    def _on_sign_in(self) -> None:
        client_id = self.client_id.text().strip()
        if not client_id:
            self._show_status(_("Enter the Microsoft app (client) ID first."))
            return
        auth = AuthManager(client_id, ConfigTokenStore())
        try:
            ok, profile = sign_in(
                self,
                auth,
                profile_loader=lambda: GraphClient(auth).get_me(),
            )
        except Exception as exc:  # noqa: BLE001 - auth/network failure
            self._show_status(_("Sign-in failed: {message}").format(message=exc))
            return
        if not ok:
            self._show_status(_("Sign-in cancelled."))
            return
        self._signed_in_client_id = client_id
        self._account_name = str(
            profile.get("displayName") or profile.get("userPrincipalName") or ""
        )
        self._refresh_auth_status()
        self._show_status(_("Signed in to OneDrive."), ok=True)

    def _on_sign_out(self) -> None:
        clear_refresh_token()
        self._signed_in_client_id = ""
        self._account_name = ""
        self._folder = None
        self._refresh_auth_status()
        self._refresh_folder_label()
        self._show_status(_("Signed out."))

    def _on_choose_folder(self) -> None:
        client_id = self.client_id.text().strip()
        if not client_id:
            self._show_status(_("Enter the Microsoft app (client) ID first."))
            return
        if not get_refresh_token():
            self._show_status(_("Sign in to OneDrive first."))
            return
        auth = AuthManager(client_id, ConfigTokenStore())
        try:
            folder = open_folder_picker(self, GraphClient(auth), initial=self._folder)
        except Exception as exc:  # noqa: BLE001 - network/Graph failure
            self._show_status(_("Could not open the folder picker: {message}").format(message=exc))
            return
        if folder is None or not folder.item_id:
            return
        self._folder = folder
        self._refresh_folder_label()
        self._update_preview()
        self._show_status(_("Folder selected: {path}").format(path=folder.display()), ok=True)

    def _refresh_auth_status(self) -> None:
        if get_refresh_token():
            name = self._account_name or _("OneDrive account")
            self.auth_status.setText(_("Signed in as {name}").format(name=name))
            self.sign_out_button.setEnabled(True)
        else:
            self.auth_status.setText(_("Not signed in"))
            self.sign_out_button.setEnabled(False)

    def _refresh_folder_label(self) -> None:
        if self._folder is None:
            self.folder_label.setText(_("No OneDrive folder selected"))
        else:
            self.folder_label.setText(
                _("OneDrive folder: {path}").format(path=self._folder.display())
            )

    def _on_export_directly_toggled(self, checked: bool) -> None:
        self.export_subfolder.setEnabled(not checked)
        self._update_preview()

    def _update_preview(self, *_args) -> None:
        if self._folder is None:
            self.preview.setText("")
            return
        path = self._folder.display().rstrip("/")
        if not self.export_directly.isChecked():
            path = f"{path}/{normalize_export_subfolder(self.export_subfolder.text())}"
        self.preview.setText(_("New books are uploaded to: {path}").format(path=path))

    def _show_status(self, message: str, *, ok: bool = False) -> None:
        self.status.setText(message)
        self.status.setStyleSheet("color: #080;" if ok else "color: #a00;")


def open_config(parent=None) -> bool:
    """Open the configuration dialog and apply on accept."""
    dialog = QDialog(parent)
    dialog.setWindowTitle(_("OneDrive Sync – Configuration"))
    layout = QVBoxLayout(dialog)
    widget = ConfigWidget(dialog)
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
