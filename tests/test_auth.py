"""Tests for the session manager on top of OAuth."""

from __future__ import annotations

import threading
import time

import pytest

from onedrive_sync.common import auth
from onedrive_sync.common.auth import AuthManager
from onedrive_sync.common.errors import NotSignedInError
from onedrive_sync.common.oauth import DeviceCode, TokenSet


class Store:
    def __init__(self, token="") -> None:
        self.token = token
        self.set_calls: list[str] = []
        self.clear_calls = 0

    def get(self):
        return self.token

    def set(self, token):
        self.token = token
        self.set_calls.append(token)

    def clear(self):
        self.clear_calls += 1
        self.token = ""


def test_has_refresh_token_and_missing_token():
    manager = AuthManager("client", Store(""))
    assert not manager.has_refresh_token()
    with pytest.raises(NotSignedInError):
        manager.get_access_token()


def test_get_access_token_refreshes_and_caches(monkeypatch):
    calls = []

    def fake_refresh(client_id, refresh_token, *, transport=None):
        calls.append((client_id, refresh_token))
        return TokenSet(access_token="access", refresh_token="new-refresh", expires_in=3600)

    monkeypatch.setattr(auth, "refresh_access_token", fake_refresh)
    store = Store("old-refresh")
    manager = AuthManager("client", store)
    assert manager.get_access_token() == "access"
    assert manager.get_access_token() == "access"
    assert calls == [("client", "old-refresh")]
    assert store.token == "new-refresh"


def test_get_access_token_force_refresh(monkeypatch):
    calls = []
    monkeypatch.setattr(
        auth,
        "refresh_access_token",
        lambda *args, **kwargs: (
            calls.append(1) or TokenSet(access_token="access", refresh_token="r")
        ),
    )
    manager = AuthManager("client", Store("r"))
    manager.get_access_token()
    manager.get_access_token(force=True)
    assert len(calls) == 2


def test_expired_cached_token_is_refreshed(monkeypatch):
    calls = []
    monkeypatch.setattr(
        auth,
        "refresh_access_token",
        lambda *args, **kwargs: (
            calls.append(1)
            or TokenSet(access_token="access", refresh_token="r", expires_in=3600, obtained_at=0)
        ),
    )
    manager = AuthManager("client", Store("r"))
    manager.get_access_token()
    manager.get_access_token()
    assert len(calls) == 2


def test_start_and_complete_device_code(monkeypatch):
    code = DeviceCode("device", "USER", "https://x")
    seen = {}

    monkeypatch.setattr(auth, "start_device_code", lambda client_id, *, transport=None: code)

    def fake_poll(client_id, received, **kwargs):
        seen["client"] = client_id
        seen["code"] = received
        seen["abort"] = kwargs["should_abort"]
        return TokenSet(access_token="access", refresh_token="refresh")

    monkeypatch.setattr(auth, "poll_device_code", fake_poll)
    store = Store()
    manager = AuthManager("client", store)
    assert manager.start_device_code() is code
    token = manager.complete_device_code(code, should_abort=lambda: False)
    assert token.access_token == "access"
    assert seen["client"] == "client"
    assert seen["abort"]() is False
    assert store.token == "refresh"


def test_install_without_refresh_token_does_not_store(monkeypatch):
    monkeypatch.setattr(
        auth,
        "refresh_access_token",
        lambda *args, **kwargs: TokenSet(access_token="access", refresh_token=""),
    )
    store = Store("r")
    manager = AuthManager("client", store)
    manager.get_access_token()
    assert store.set_calls == []


def test_sign_out_and_invalidate():
    store = Store("refresh")
    manager = AuthManager("client", store)
    manager._token = TokenSet(access_token="access")
    manager.invalidate()
    assert manager._token is None
    manager._token = TokenSet(access_token="access")
    manager.sign_out()
    assert manager._token is None
    assert store.clear_calls == 1


def test_a_refresh_a_waiting_worker_already_done_is_not_repeated(monkeypatch):
    calls = []
    monkeypatch.setattr(
        auth,
        "refresh_access_token",
        lambda *args, **kwargs: (
            calls.append(1) or TokenSet(access_token="late", refresh_token="r", expires_in=3600)
        ),
    )
    manager = AuthManager("client", Store("r"))
    manager._lock.acquire()  # a parallel request is refreshing right now
    results = []
    worker = threading.Thread(target=lambda: results.append(manager.get_access_token()))
    worker.start()
    manager._install(TokenSet(access_token="fresh", refresh_token="r", expires_in=3600))
    manager._lock.release()
    worker.join(5)

    assert results == ["fresh"]
    assert calls == []


def test_parallel_requests_share_one_token_refresh(monkeypatch):
    gate = threading.Event()
    calls = []

    def fake_refresh(*args, **kwargs):
        calls.append(1)
        gate.wait(5)  # stay inside the lock while the other workers pile up
        return TokenSet(access_token="access", refresh_token="r", expires_in=3600)

    monkeypatch.setattr(auth, "refresh_access_token", fake_refresh)
    manager = AuthManager("client", Store("r"))
    tokens = []

    def worker():
        tokens.append(manager.get_access_token())

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + 5
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)
    gate.set()
    for thread in threads:
        thread.join(5)

    # Microsoft rotates refresh tokens, so a second concurrent refresh would
    # invalidate the one the first request just stored.
    assert len(calls) == 1
    assert set(tokens) == {"access"}
