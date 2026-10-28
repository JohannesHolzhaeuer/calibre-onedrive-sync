"""Tests for the device-code sign-in dialog."""

from __future__ import annotations

import pytest
from qt.core import QDialog, pyqtSignal

from onedrive_sync.common.oauth import DeviceCode, TokenSet
from onedrive_sync.gui import auth_dialog
from onedrive_sync.gui.auth_dialog import DeviceCodeDialog, _AuthWorker, sign_in


class FakeAuth:
    def __init__(self) -> None:
        self.code = DeviceCode("device", "USER", "https://microsoft.test/device")
        self.fail = False
        self.abort = None

    def start_device_code(self):
        if self.fail:
            raise RuntimeError("cannot start")
        return self.code

    def complete_device_code(self, code, *, should_abort=None):
        self.abort = should_abort
        if self.fail:
            raise RuntimeError("cannot finish")
        return TokenSet("access", "refresh")


def test_worker_emits_code_and_success():
    auth = FakeAuth()
    worker = _AuthWorker(auth, lambda: {"displayName": "Alice"})
    codes = []
    successes = []
    failures = []
    worker.code_ready.connect(codes.append)
    worker.succeeded.connect(successes.append)
    worker.failed.connect(failures.append)
    worker.run()
    assert codes == [auth.code]
    assert successes[0]["token"].access_token == "access"
    assert successes[0]["profile"]["displayName"] == "Alice"
    assert failures == []


def test_worker_without_profile_loader():
    auth = FakeAuth()
    worker = _AuthWorker(auth)
    successes = []
    worker.succeeded.connect(successes.append)
    worker.run()
    assert successes[0]["profile"] == {}


def test_worker_ignores_profile_loader_failure():
    auth = FakeAuth()

    def broken_loader():
        raise RuntimeError("profile unavailable")

    worker = _AuthWorker(auth, broken_loader)
    successes = []
    failures = []
    worker.succeeded.connect(successes.append)
    worker.failed.connect(failures.append)
    worker.run()
    assert successes[0]["profile"] == {}
    assert failures == []


def test_worker_emits_failure():
    auth = FakeAuth()
    auth.fail = True
    worker = _AuthWorker(auth)
    failures = []
    worker.failed.connect(failures.append)
    worker.run()
    assert failures == ["cannot start"]


def test_worker_cancel_sets_abort_flag():
    auth = FakeAuth()
    worker = _AuthWorker(auth)
    worker.cancel()
    worker.run()
    assert auth.abort() is True


class FakeWorker:
    code_ready = pyqtSignal(object)
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, auth, profile_loader) -> None:
        self.auth = auth
        self.profile_loader = profile_loader
        self.started = False
        self.cancelled = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True


@pytest.fixture()
def dialog(monkeypatch):
    monkeypatch.setattr(auth_dialog, "_AuthWorker", FakeWorker)
    opened = []
    monkeypatch.setattr(auth_dialog.webbrowser, "open", lambda url: opened.append(url))
    widget = DeviceCodeDialog(FakeAuth(), profile_loader=lambda: {"displayName": "A"})
    return widget, opened


def test_dialog_has_readable_minimum_size(dialog):
    widget, _opened = dialog
    assert widget._minimum == (560, 260)
    assert widget._size == (620, 320)


def test_dialog_start_and_show_code(dialog):
    widget, opened = dialog
    widget.start()
    assert widget._worker.started
    code = DeviceCode("d", "USER", "https://microsoft.test/device", message="Use this code")
    widget._show_code(code)
    assert "USER" in widget.code.text()
    assert widget.open_button.isEnabled()
    assert widget.status.text() == "Use this code"
    assert opened == ["https://microsoft.test/device"]

    widget._open_browser()
    assert opened[-1] == "https://microsoft.test/device"


def test_dialog_show_code_ignores_browser_errors(dialog, monkeypatch):
    widget, _opened = dialog
    monkeypatch.setattr(
        auth_dialog.webbrowser, "open", lambda _url: (_ for _ in ()).throw(RuntimeError("no"))
    )
    widget._show_code(DeviceCode("d", "USER", "https://x"))
    assert "USER" in widget.code.text()


def test_dialog_success(dialog):
    widget, _opened = dialog
    widget._on_success({"profile": {"displayName": "Alice"}})
    assert widget.signed_in
    assert widget.profile == {"displayName": "Alice"}
    assert widget._result == QDialog.DialogCode.Accepted


def test_dialog_failure(dialog):
    widget, _opened = dialog
    widget._on_failure("denied")
    assert not widget.signed_in
    assert "denied" in widget.status.text()


def test_dialog_reject_cancels_worker(dialog):
    widget, _opened = dialog
    widget.reject()
    assert widget._worker.cancelled
    assert widget._result == QDialog.DialogCode.Rejected


class FakeDialog:
    def __init__(self, auth, parent, *, profile_loader=None) -> None:
        self.auth = auth
        self.parent = parent
        self.profile_loader = profile_loader
        self.signed_in = True
        self.profile = {"displayName": "Alice"}
        self.started = False
        self.rejected = False
        self.result = QDialog.DialogCode.Accepted

    def start(self):
        self.started = True

    def exec(self):
        return self.result

    def reject(self):
        self.rejected = True


def test_sign_in_success(monkeypatch):
    monkeypatch.setattr(auth_dialog, "DeviceCodeDialog", FakeDialog)
    ok, profile = sign_in(None, FakeAuth())
    assert ok
    assert profile["displayName"] == "Alice"


def test_sign_in_rejected(monkeypatch):
    monkeypatch.setattr(auth_dialog, "DeviceCodeDialog", FakeDialog)
    original = auth_dialog.DeviceCodeDialog

    class Rejected(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.result = QDialog.DialogCode.Rejected

    monkeypatch.setattr(auth_dialog, "DeviceCodeDialog", Rejected)
    ok, profile = sign_in(None, FakeAuth())
    assert not ok
    assert profile == {}


def test_sign_in_accepted_but_not_signed_in(monkeypatch):
    class NotSignedIn(FakeDialog):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.signed_in = False

    monkeypatch.setattr(auth_dialog, "DeviceCodeDialog", NotSignedIn)
    ok, profile = sign_in(None, FakeAuth())
    assert not ok
    assert profile == {}


def test_sign_in_returns_the_failure_reason(monkeypatch):
    class Failed(FakeDialog):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.signed_in = False
            self.error = "connection reset by peer"

    monkeypatch.setattr(auth_dialog, "DeviceCodeDialog", Failed)
    ok, profile = sign_in(None, FakeAuth())
    assert not ok
    assert profile == {"error": "connection reset by peer"}
