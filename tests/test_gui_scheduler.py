"""Tests for event-driven sync scheduling."""

from __future__ import annotations

import types

from onedrive_sync.gui.scheduler import SyncScheduler


class FakeApi:
    def __init__(self) -> None:
        self.listeners = []
        self.removed = []

    def add_listener(self, callback):
        self.listeners.append(callback)

    def remove_listener(self, callback):
        self.removed.append(callback)


class FakeLibrary:
    def __init__(self, with_api=True) -> None:
        self.api = FakeApi() if with_api else None
        self.gui = types.SimpleNamespace(current_db=types.SimpleNamespace(library_path="C:/Books"))


def make_scheduler(monkeypatch):
    scheduler = SyncScheduler(debounce_seconds=0)
    requested = []
    scheduler.sync_requested.connect(requested.append)
    return scheduler, requested


def test_start_and_stop(monkeypatch):
    scheduler, requested = make_scheduler(monkeypatch)
    library = FakeLibrary()
    scheduler.start(library, interval_minutes=15)
    assert scheduler._active
    assert scheduler._timer.isActive()
    assert library.api.listeners
    assert "C:/Books" in scheduler._watched

    scheduler.notify_change("test")
    scheduler._on_debounce()
    assert requested == ["change"]

    scheduler.stop()
    assert not scheduler._active
    assert not scheduler._timer.isActive()
    assert library.api.removed
    assert scheduler._watched == set()


def test_listener_filters_events(monkeypatch):
    scheduler, requested = make_scheduler(monkeypatch)
    library = FakeLibrary()
    scheduler.start(library, interval_minutes=15)
    listener = library.api.listeners[0]

    listener(types.SimpleNamespace(name="book_created"), "lib", {})
    scheduler._on_debounce()
    assert requested == ["change"]

    listener(types.SimpleNamespace(name="indexing_progress_changed"), "lib", {})
    scheduler._on_debounce()
    assert requested == ["change"]

    listener("metadata_changed", "lib", {})
    scheduler._on_debounce()
    assert requested == ["change", "change"]


def test_suspend_blocks_events_until_resume(monkeypatch):
    scheduler, requested = make_scheduler(monkeypatch)
    library = FakeLibrary()
    scheduler.start(library, interval_minutes=15)
    scheduler.suspend()
    scheduler._on_debounce()
    scheduler._on_periodic()
    assert requested == []
    scheduler.resume()
    scheduler._on_periodic()
    assert requested == ["scheduled"]


def test_timer_interval_and_path_events(monkeypatch):
    scheduler, requested = make_scheduler(monkeypatch)
    library = FakeLibrary()
    scheduler.start(library, interval_minutes=0)
    assert scheduler._timer._interval == 60 * 1000
    scheduler._on_path_changed("C:/Books/metadata.db")
    scheduler._on_debounce()
    assert requested == ["change"]


def test_no_api_or_listener_errors(monkeypatch):
    scheduler, requested = make_scheduler(monkeypatch)
    library = FakeLibrary(with_api=False)
    scheduler.start(library, interval_minutes=1)
    scheduler.notify_change("test")
    scheduler._on_debounce()
    assert requested == ["change"]
    scheduler.stop()

    class BrokenApi:
        def add_listener(self, _callback):
            raise RuntimeError("db closed")

        def remove_listener(self, _callback):
            raise RuntimeError("db closed")

    library = FakeLibrary()
    library.api = BrokenApi()
    scheduler.start(library, interval_minutes=1)
    assert scheduler._listener is None
    scheduler.stop()
