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
    MAX_UPLOAD_WORKERS,
    MIN_UPLOAD_WORKERS,
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

        footer = QLabel(
            _(
                "Everything is manual: the toolbar button lets you pick a folder, "
                "checks for a calibre index and then opens the review window. "
                "Books are uploaded directly into the selected folder; subfolders "
                "are never read. The one exception is the upload of books calibre "
                "has just added, which can be switched off below."
            )
        )
        footer.setWordWrap(True)
        layout.addWidget(footer)

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

        layout.addWidget(QLabel(_("Parallel uploads")))
        self.upload_workers = QSpinBox()
        self.upload_workers.setRange(MIN_UPLOAD_WORKERS, MAX_UPLOAD_WORKERS)
        layout.addWidget(self.upload_workers)
        workers_help = QLabel(
            _(
                "How many files to upload at the same time. Microsoft measures the "
                "rate per account, so a few parallel uploads use the line far better "
                "than one; the estimate in the review window follows automatically."
            )
        )
        workers_help.setWordWrap(True)
        layout.addWidget(workers_help)

        self.auto_upload = QCheckBox(_("Upload newly added books automatically"))
        layout.addWidget(self.auto_upload)
        auto_help = QLabel(
            _(
                "When calibre adds a book, it is uploaded to the selected folder "
                "right away, in the background, without opening the review window. "
                "Turn this off to keep every upload behind an explicit click."
            )
        )
        auto_help.setWordWrap(True)
        layout.addWidget(auto_help)

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
        self.upload_workers.setValue(settings.upload_workers)
        self.auto_upload.setChecked(settings.auto_upload_new_books)
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
            "export_format": self.export_format.currentData() or DEFAULT_EXPORT_FORMAT,
            "upload_workers": self.upload_workers.value(),
            "auto_upload_new_books": self.auto_upload.isChecked(),
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
            error = str((profile or {}).get("error") or "")
            if error:
                self._show_status(_("Sign-in failed: {message}").format(message=error))
            else:
                self._show_status(_("Sign-in cancelled."))
            return
        self._signed_in_client_id = client_id
        self._account_name = str(
            profile.get("displayName") or profile.get("userPrincipalName") or ""
        )
        self._refresh_auth_status()
        self._show_status(
            _(
                "Signed in to OneDrive. Choose a folder, click OK, and then use the "
                "toolbar button to review matching books before the first sync."
            ),
            ok=True,
        )

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

    def _update_preview(self, *_args) -> None:
        if self._folder is None:
            self.preview.setText("")
            return
        path = self._folder.display().rstrip("/")
        self.preview.setText(_("Books are uploaded directly to: {path}").format(path=path))

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
