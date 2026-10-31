"""Tests for the calibre database listener behind the automatic upload."""

from __future__ import annotations

import types

from onedrive_sync.gui.library_watch import LibraryWatcher, event_name


class FakeEvent:
    """Stand-in for ``calibre.db.listeners.EventType`` (matched by ``name``)."""

    def __init__(self, name) -> None:
        self.name = name


class FakeCache:
    """Stand-in for ``gui.current_db.new_api`` with calibre's listener API."""

    def __init__(self, add_error=None, remove_error=None) -> None:
        self.add_error = add_error
        self.remove_error = remove_error
        self.listeners: list = []

    def add_listener(self, callback) -> None:
        if self.add_error is not None:
            raise self.add_error
        self.listeners.append(callback)

    def remove_listener(self, callback) -> None:
        if self.remove_error is not None:
            raise self.remove_error
        self.listeners.remove(callback)

    def dispatch(self, event_type, *args) -> None:
        for listener in list(self.listeners):
            listener(event_type, "library", args)


def watcher_with(cache) -> tuple[LibraryWatcher, list]:
    watcher = LibraryWatcher(cache)
    seen: list = []
    watcher.books_created.connect(seen.append)
    return watcher, seen


def test_event_names_work_for_enums_and_plain_strings():
    assert event_name(FakeEvent("book_created")) == "book_created"
    assert event_name("book_created") == "book_created"
    assert event_name(None) == ""


def test_a_created_book_is_reported_with_its_id():
    cache = FakeCache()
    watcher, seen = watcher_with(cache)
    assert watcher.start() is True

    cache.dispatch(FakeEvent("book_created"), 42)

    assert seen == [[42]]


def test_other_database_events_are_ignored():
    cache = FakeCache()
    watcher, seen = watcher_with(cache)
    watcher.start()

    cache.dispatch(FakeEvent("metadata_changed"), "title", {1})
    cache.dispatch(FakeEvent("format_added"), 1, "epub")
    cache.dispatch(FakeEvent("books_removed"), {1})

    assert seen == []


def test_a_payload_without_a_usable_book_id_is_ignored():
    cache = FakeCache()
    watcher, seen = watcher_with(cache)
    watcher.start()

    for payload in ((), ("abc",), (None,)):
        cache.dispatch(FakeEvent("book_created"), *payload)

    assert seen == []


def test_a_failing_gui_slot_does_not_break_calibre():
    cache = FakeCache()
    watcher, seen = watcher_with(cache)

    def broken(_ids):
        raise RuntimeError("gui is gone")

    watcher.books_created.connect(broken)
    watcher.start()

    cache.dispatch(FakeEvent("book_created"), 3)  # must not raise

    assert seen == [[3]]


def test_the_listener_is_kept_alive_by_the_watcher():
    """calibre stores only a weak reference, so a bare bound method would die."""
    cache = FakeCache()
    watcher = LibraryWatcher(cache)
    watcher.start()

    assert cache.listeners == [watcher._callback]


def test_an_old_calibre_cannot_be_watched():
    watcher = LibraryWatcher(types.SimpleNamespace())
    assert watcher.start() is False
    watcher.stop()  # nothing registered, nothing to remove


def test_a_broken_listener_hook_is_survived():
    watcher = LibraryWatcher(FakeCache(add_error=RuntimeError("db closed")))
    assert watcher.start() is False


def test_stop_unregisters_once():
    cache = FakeCache()
    watcher = LibraryWatcher(cache)
    watcher.start()

    watcher.stop()
    assert cache.listeners == []
    watcher.stop()  # idempotent
    assert cache.listeners == []


def test_stop_tolerates_a_cache_without_remove_listener():
    api = types.SimpleNamespace(add_listener=lambda _callback: None)
    watcher = LibraryWatcher(api)
    assert watcher.start() is True

    watcher.stop()  # must not raise


def test_stop_swallows_a_failing_removal():
    watcher = LibraryWatcher(FakeCache(remove_error=RuntimeError("db closed")))
    watcher.start()

    watcher.stop()  # must not raise
