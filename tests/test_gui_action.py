"""Tests for the toolbar interface action."""

from __future__ import annotations

import types

from onedrive_sync.common.config import Settings
from onedrive_sync.common.models import SyncResult
from onedrive_sync.gui import action as action_mod
from onedrive_sync.gui.action import OneDriveSyncInterfaceAction


class FakeScheduler:
    def __init__(self) -> None:
        self.stopped = 0
        self.started = []
        self.suspended = 0
        self.resumed = 0
        self.requested = []

    def stop(self):
        self.stopped += 1

    def start(self, library, *, interval_minutes):
        self.started.append((library, interval_minutes))

    def suspend(self):
        self.suspended += 1

    def resume(self):
        self.resumed += 1


class FakeState:
    def __init__(self) -> None:
        self.closed = 0

    def close(self):
        self.closed += 1


class FakeStatusBar:
    def __init__(self) -> None:
        self.messages = []
        self.fail = False

    def show_message(self, message, _timeout):
        if self.fail:
            raise RuntimeError("no status")
        self.messages.append(message)


class FakeJobManager:
    def __init__(self) -> None:
        self.jobs = []

    def run_threaded_job(self, job):
        self.jobs.append(job)


class FakeGui:
    def __init__(self) -> None:
        self.job_manager = FakeJobManager()
        self.status_bar = FakeStatusBar()
        self.current_db = types.SimpleNamespace(
            new_api=types.SimpleNamespace(), library_path="C:/Books"
        )


def make_action(monkeypatch):
    monkeypatch.setattr(action_mod, "get_icons", lambda *_args: "icon", raising=False)
    action = OneDriveSyncInterfaceAction(FakeGui())
    action.genesis()
    action._scheduler = FakeScheduler()
    return action


def test_genesis_builds_icon_and_menu(monkeypatch):
    action = make_action(monkeypatch)
    assert action.qaction.icon == "icon"
    assert [text for text, _slot in action.menu.actions()] == [
        "Sync now",
        "Open configuration…",
    ]


def test_shutting_down_stops_scheduler_and_closes_state(monkeypatch):
    action = make_action(monkeypatch)
    action._state = FakeState()
    action.shutting_down()
    assert action._scheduler.stopped == 1
    assert action._state is None


def test_sync_now_ignores_while_job_runs(monkeypatch):
    action = make_action(monkeypatch)
    action._job = object()
    called = []
    monkeypatch.setattr(action, "open_config", lambda: called.append(1) or True)
    action.sync_now()
    assert called == []


def test_manual_sync_not_configured_opens_config(monkeypatch):
    action = make_action(monkeypatch)
    monkeypatch.setattr(action_mod, "load_settings", lambda: Settings())
    monkeypatch.setattr(action_mod, "is_configured", lambda _settings: False)
    monkeypatch.setattr(action, "open_config", lambda: False)
    action.sync_now()
    assert action._job is None


def test_automatic_sync_not_configured_is_silent(monkeypatch):
    action = make_action(monkeypatch)
    monkeypatch.setattr(action_mod, "load_settings", lambda: Settings())
    monkeypatch.setattr(action_mod, "is_configured", lambda _settings: False)
    called = []
    monkeypatch.setattr(action, "open_config", lambda: called.append(1) or True)
    action.sync_now("scheduled")
    assert called == []


def test_sync_now_starts_job(monkeypatch):
    action = make_action(monkeypatch)
    settings = Settings(client_id="c", drive_id="d", folder_id="f")
    monkeypatch.setattr(action_mod, "load_settings", lambda: settings)
    monkeypatch.setattr(action_mod, "is_configured", lambda _settings: True)
    monkeypatch.setattr(action_mod, "get_refresh_token", lambda: "refresh")
    monkeypatch.setattr(action_mod, "CalibreLibrary", lambda _gui: "library")
    monkeypatch.setattr(action_mod, "AuthManager", lambda *_args: "auth")
    monkeypatch.setattr(action_mod, "GraphClient", lambda *_args: "graph")
    monkeypatch.setattr(action_mod, "StateStore", lambda _path: FakeState())
    monkeypatch.setattr(action_mod, "MainThreadBookAdder", lambda *_args, **_kwargs: "adder")
    monkeypatch.setattr(action_mod, "SyncEngine", lambda *_args, **_kwargs: "engine")
    started = []

    def fake_start(_manager, engine, adder, callback, *, description=None):
        started.append((engine, adder, callback, description))
        return "job"

    monkeypatch.setattr(action_mod, "start_sync_job", fake_start)
    action.sync_now()
    assert action._job == "job"
    assert started[0][0] == "engine"
    assert action._scheduler.suspended == 1
    assert action.gui.status_bar.messages


def test_sync_now_start_failure_warns(monkeypatch):
    action = make_action(monkeypatch)
    settings = Settings(client_id="c", drive_id="d", folder_id="f")
    monkeypatch.setattr(action_mod, "load_settings", lambda: settings)
    monkeypatch.setattr(action_mod, "is_configured", lambda _settings: True)
    monkeypatch.setattr(action_mod, "get_refresh_token", lambda: "refresh")

    def fail(_gui):
        raise RuntimeError("db closed")

    monkeypatch.setattr(action_mod, "CalibreLibrary", fail)
    warnings = []
    monkeypatch.setattr(action_mod.QMessageBox, "warning", lambda *args: warnings.append(args))
    action.sync_now()
    assert action._job is None
    assert warnings
    assert "db closed" in warnings[0][2]


def test_on_job_done_failed(monkeypatch):
    action = make_action(monkeypatch)
    job = types.SimpleNamespace(failed=True, exception=RuntimeError("network"))
    warnings = []
    monkeypatch.setattr(action_mod.QMessageBox, "warning", lambda *args: warnings.append(args))
    action._on_job_done(job)
    assert action._scheduler.resumed == 1
    assert "network" in warnings[0][2]


def test_on_job_done_success_and_errors(monkeypatch):
    action = make_action(monkeypatch)
    infos = []
    warnings = []
    monkeypatch.setattr(action_mod.QMessageBox, "information", lambda *args: infos.append(args))
    monkeypatch.setattr(action_mod.QMessageBox, "warning", lambda *args: warnings.append(args))

    result = SyncResult(imported=1)
    action._on_job_done(types.SimpleNamespace(failed=False, result=result))
    assert infos
    assert action.gui.status_bar.messages[-1] == result.summary()

    result = SyncResult(failed=12, errors=[f"error {index}" for index in range(12)])
    action._on_job_done(types.SimpleNamespace(failed=False, result=result))
    assert warnings
    assert "…" in warnings[-1][2]

    action._on_job_done(types.SimpleNamespace(failed=False, result=None))
    action._on_job_done(None)


def test_open_config_reconfigures(monkeypatch):
    action = make_action(monkeypatch)
    monkeypatch.setattr(action_mod, "open_config_dialog", lambda _parent: True)
    configured = []
    monkeypatch.setattr(action, "_configure_scheduler", lambda: configured.append(1))
    assert action.open_config()
    assert configured == [1]


def test_configure_scheduler(monkeypatch):
    action = make_action(monkeypatch)
    settings = Settings(
        client_id="c", drive_id="d", folder_id="f", auto_sync=True, sync_interval_minutes=7
    )
    monkeypatch.setattr(action_mod, "load_settings", lambda: settings)
    monkeypatch.setattr(action_mod, "is_configured", lambda _settings: True)
    monkeypatch.setattr(action_mod, "get_refresh_token", lambda: "refresh")
    monkeypatch.setattr(action_mod, "CalibreLibrary", lambda _gui: "library")
    action._configure_scheduler()
    assert action._scheduler.started == [("library", 7)]

    settings.auto_sync = False
    action._configure_scheduler()
    assert action._scheduler.stopped == 2


def test_configure_scheduler_handles_missing_library(monkeypatch):
    action = make_action(monkeypatch)
    settings = Settings(client_id="c", drive_id="d", folder_id="f", auto_sync=True)
    monkeypatch.setattr(action_mod, "load_settings", lambda: settings)
    monkeypatch.setattr(action_mod, "is_configured", lambda _settings: True)
    monkeypatch.setattr(action_mod, "get_refresh_token", lambda: "refresh")

    def fail(_gui):
        raise RuntimeError("no library")

    monkeypatch.setattr(action_mod, "CalibreLibrary", fail)
    action._configure_scheduler()
    assert action._scheduler.started == []


def test_library_changed_reconfigures(monkeypatch):
    action = make_action(monkeypatch)
    configured = []
    monkeypatch.setattr(action, "_configure_scheduler", lambda: configured.append(1))
    action.library_changed(None)
    action.initialization_complete()
    assert configured == [1, 1]


def test_close_state_and_status_errors(monkeypatch):
    action = make_action(monkeypatch)

    class BrokenState:
        def close(self):
            raise RuntimeError("closed")

    action._state = BrokenState()
    action._close_state()
    assert action._state is None

    action.gui.status_bar.fail = True
    action._show_status("hello")
