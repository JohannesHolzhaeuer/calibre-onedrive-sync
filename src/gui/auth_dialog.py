"""Device-code sign-in dialog.

The worker thread starts the device-code flow, shows the short code, and polls
Microsoft until the user finishes in the browser. Network I/O never runs on the
GUI thread.
"""

from __future__ import annotations

import threading
import webbrowser

from qt.core import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    Qt,
    QThread,
    QVBoxLayout,
    pyqtSignal,
)

from ..common.auth import AuthManager
from ..common.i18n import _
from ..common.oauth import DeviceCode

_ACTIVE_WORKERS: set[QThread] = set()


class _AuthWorker(QThread):
    """Start + poll the device-code flow in a background Qt thread."""

    code_ready = pyqtSignal(object)
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, auth: AuthManager, profile_loader=None) -> None:
        super().__init__()
        self.auth = auth
        self.profile_loader = profile_loader
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def run(self) -> None:
        try:
            code = self.auth.start_device_code()
            self.code_ready.emit(code)
            token = self.auth.complete_device_code(code, should_abort=self._cancelled.is_set)
            profile = {}
            if self.profile_loader is not None:
                try:
                    profile = self.profile_loader() or {}
                except Exception:  # noqa: BLE001 - profile is cosmetic, the token is valid
                    profile = {}
            self.succeeded.emit({"token": token, "profile": profile})
        except Exception as exc:  # noqa: BLE001 - surfaced to the dialog
            self.failed.emit(str(exc))


class DeviceCodeDialog(QDialog):
    """Show the device code and wait for the browser sign-in to finish."""

    def __init__(self, auth: AuthManager, parent=None, *, profile_loader=None) -> None:
        super().__init__(parent)
        self.auth = auth
        self.profile: dict = {}
        self.signed_in = False
        self._device_code: DeviceCode | None = None
        self.setMinimumSize(560, 260)
        self.resize(620, 320)
        self._build_ui()
        self._worker = _AuthWorker(auth, profile_loader)
        self._worker.code_ready.connect(self._show_code)
        self._worker.succeeded.connect(self._on_success)
        self._worker.failed.connect(self._on_failure)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self.setWindowTitle(_("Sign in to OneDrive"))
        self.instructions = QLabel(
            _(
                "Open the sign-in page and enter the code below. "
                "This dialog closes automatically when sign-in succeeds."
            )
        )
        self.instructions.setWordWrap(True)
        layout.addWidget(self.instructions)

        self.code = QLabel(_("Starting sign-in…"))
        self.code.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.code)

        row = QHBoxLayout()
        self.open_button = QPushButton(_("Open sign-in page"))
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self._open_browser)
        row.addWidget(self.open_button)
        layout.addLayout(row)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        self.cancel_button = QPushButton(_("Cancel"))
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)

    def start(self) -> None:
        """Start the worker; call before ``exec()``."""
        if self._worker is None:
            return
        worker = self._worker
        if isinstance(worker, QThread):
            _ACTIVE_WORKERS.add(worker)
            worker.finished.connect(lambda w=worker: _ACTIVE_WORKERS.discard(w))
        worker.start()

    def _show_code(self, code: DeviceCode) -> None:
        self._device_code = code
        self.code.setText(f"<b>{code.user_code}</b>")
        self.open_button.setEnabled(True)
        self.status.setText(code.message or code.verification_uri)
        try:
            webbrowser.open(code.verification_uri)
        except Exception:  # noqa: BLE001 - the user can click the button instead
            pass

    def _open_browser(self) -> None:
        if self._device_code is not None:
            webbrowser.open(self._device_code.verification_uri)

    def _on_success(self, payload) -> None:
        self.signed_in = True
        self.profile = dict((payload or {}).get("profile") or {})
        self.status.setText(_("Signed in."))
        self.accept()

    def _on_failure(self, message: str) -> None:
        self.signed_in = False
        self.status.setText(_("Sign-in failed: {message}").format(message=message))

    def reject(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
        super().reject()


def sign_in(parent, auth: AuthManager, *, profile_loader=None) -> tuple[bool, dict]:
    """Show the dialog and return ``(signed_in, profile)``."""
    dialog = DeviceCodeDialog(auth, parent, profile_loader=profile_loader)
    dialog.start()
    result = dialog.exec()
    if result != QDialog.DialogCode.Accepted or not dialog.signed_in:
        dialog.reject()
        return False, {}
    return True, dialog.profile
