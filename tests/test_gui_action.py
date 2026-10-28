"""Tests for the toolbar interface action."""

from __future__ import annotations

import threading
import types

from qt.core import QDialog, QMessageBox

from onedrive_sync.common.config import Settings
from onedrive_sync.common.errors import CancelledError
from onedrive_sync.common.models import BookLink, FolderRef, RemoteItem, SyncResult
from onedrive_sync.common.reconcile import LocalBook, build_pending_plan, build_reconcile_plan
from onedrive_sync.gui import action as action_mod
from onedrive_sync.gui.action import OneDriveSyncInterfaceAction
from onedrive_sync.gui.review_dialog import ReviewDialog


class FakeState:
    def __init__(self) -> None:
        self.closed = 0
        self.links = []
        self.meta = {}

    def close(self):
        self.closed += 1

    def get_meta(self, key, default=""):
        return self.meta.get(key, default)

    def set_meta(self, key, value):
        self.meta[key] = value

    def link_book(self, link):
        self.links.append(link)


class FakeLibrary:
    def __init__(self) -> None:
        self.identifiers = []
        self.cleared = 0

    def set_identifier(self, book_id, key, value):
        self.identifiers.append((book_id, key, value))

    def clear_identifier(self, key):
        self.cleared += 1
        self.identifiers = [item for item in self.identifiers if item[1] != key]
        return 1


def killed_job(result=None):
    """A job calibre killed: failed without an exception, callback not called."""
    return types.SimpleNamespace(
        killed=True,
        abort=threading.Event(),
        failed=True,
        exception=None,
        result=result,
        duration=None,
        worker_active=False,
    )


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


def sample_plan():
    locals_ = [
        LocalBook(book_id=1, title="Matched", authors="Author", formats=("epub",)),
        LocalBook(book_id=2, title="Upload", authors="Author", formats=("epub",)),
    ]
    remotes = [
        RemoteItem(
            id="m",
            name="Author - Matched.epub",
            drive_id="drive",
            web_url="https://onedrive.test/m",
        ),
        RemoteItem(id="extra", name="Author - Matched copy.epub", drive_id="drive"),
        RemoteItem(id="d", name="Unknown - Download.epub", drive_id="drive"),
    ]
    return build_reconcile_plan(locals_, remotes)


def make_action(monkeypatch):
    monkeypatch.setattr(action_mod, "get_icons", lambda *_args: "icon", raising=False)
    action = OneDriveSyncInterfaceAction(FakeGui())
    action.genesis()
    return action


def test_genesis_builds_icon_without_menu(monkeypatch):
    action = make_action(monkeypatch)
    assert action.qaction.icon == "icon"
    assert action.qaction.menu() is None
    assert action.sync_now in action.qaction.triggered._slots


def test_toolbar_click_triggers_sync(monkeypatch):
    called = []
    monkeypatch.setattr(
        OneDriveSyncInterfaceAction,
        "sync_now",
        lambda _self, *args: called.append(args),
    )
    action = make_action(monkeypatch)
    action.qaction.triggered.emit(False)
    assert called == [(False,)]


def test_shutting_down_closes_state(monkeypatch):
    action = make_action(monkeypatch)
    action._state = FakeState()
    action.shutting_down()
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
    action.sync_now("scheduled")
    assert action._job is None
    assert warnings
    assert "db closed" in warnings[0][2]


def test_on_job_done_failed(monkeypatch):
    action = make_action(monkeypatch)
    job = types.SimpleNamespace(failed=True, exception=RuntimeError("network"))
    warnings = []
    monkeypatch.setattr(action_mod.QMessageBox, "warning", lambda *args: warnings.append(args))
    action._on_job_done(job)
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


def test_on_job_done_cancelled(monkeypatch):
    action = make_action(monkeypatch)
    infos = []
    warnings = []
    monkeypatch.setattr(action_mod.QMessageBox, "information", lambda *args: infos.append(args))
    monkeypatch.setattr(action_mod.QMessageBox, "warning", lambda *args: warnings.append(args))
    result = SyncResult(imported=1, cancelled=True)
    action._on_job_done(types.SimpleNamespace(failed=False, result=result))
    assert infos
    assert warnings == []
    assert "Cancelled." in action.gui.status_bar.messages[-1]


def test_on_scan_done_cancelled(monkeypatch):
    action = review_action(monkeypatch)
    warnings = []
    monkeypatch.setattr(action_mod.QMessageBox, "warning", lambda *args: warnings.append(args))
    action._on_scan_done(types.SimpleNamespace(failed=True, exception=CancelledError("Cancelled.")))
    assert warnings == []
    assert action._state is None
    assert action.gui.status_bar.messages[-1] == "OneDrive matching cancelled."


def test_open_config_returns_the_dialog_result(monkeypatch):
    action = make_action(monkeypatch)
    monkeypatch.setattr(action_mod, "open_config_dialog", lambda _parent: True)
    assert action.open_config()
    monkeypatch.setattr(action_mod, "open_config_dialog", lambda _parent: False)
    assert not action.open_config()


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


# -- review flow -------------------------------------------------------------


def test_toolbar_click_opens_review(monkeypatch):
    action = make_action(monkeypatch)
    called = []
    monkeypatch.setattr(action, "review_now", lambda: called.append(1))
    action.sync_now()
    assert called == [1]


class FakeScanEngine:
    """Stands in for ``SyncEngine.scan`` and emits live plan snapshots."""

    def __init__(self, plan=None, *, error=None, has_index=True) -> None:
        self.plan = plan
        self.error = error
        self.has_index = has_index
        self.graph = None
        self.settings = types.SimpleNamespace(folder_path="", folder_id="f", folder_name="")
        self.on_progress = None
        self.on_plan = None
        self.should_abort = None
        self.phases = []

    def index_available(self):
        return self.has_index

    def scan(self):
        if self.should_abort is not None and self.should_abort():
            raise CancelledError("Cancelled.")
        if self.error is not None:
            raise self.error
        if self.on_plan is not None and self.plan is not None:
            self.on_plan(build_pending_plan(self.plan.local_books), "library")
            self.phases.append("library")
            self.on_plan(self.plan, "onedrive")
            self.phases.append("onedrive")
        return self.plan


def review_action(monkeypatch, engine=None):
    """Action with a fake engine; the review dialog stays the real one."""
    action = make_action(monkeypatch)
    action._state = FakeState()
    action._library = FakeLibrary()
    action._engine = engine or FakeScanEngine(sample_plan())
    action._adder = "adder"
    action._linker = "linker"
    settings = Settings(client_id="c", drive_id="d", folder_id="f")
    monkeypatch.setattr(action, "_settings_for", lambda: settings)
    monkeypatch.setattr(action, "_prepare_engine", lambda _settings: True)
    monkeypatch.setattr(action, "_pick_folder", lambda _settings: FolderRef("d", "f", "Folder"))
    monkeypatch.setattr(action_mod, "save_settings", lambda _settings: None)
    return action


def run_scan(action) -> None:
    """Run the enqueued scan job, as calibre's job server would."""
    action.gui.job_manager.jobs[-1].start_work()


def run_index_check(action) -> None:
    """Run the enqueued index check, as calibre's job server would."""
    action.gui.job_manager.jobs[-1].start_work()


def click_through(action, monkeypatch, *, accept=False, while_open=None, choice="apply"):
    """Simulate the review window: run ``while_open(dialog)``, then close it.

    ``choice`` mirrors the button the user clicked; ``"apply"`` is also what
    pressing Enter does.
    """
    captured = {}

    def fake_exec(dialog):
        captured["dialog"] = dialog
        if while_open is not None:
            while_open(dialog)
        if accept:
            dialog._request(choice)
            return QDialog.DialogCode.Accepted
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(action_mod.ReviewDialog, "exec", fake_exec)
    action.review_now()
    run_index_check(action)
    return captured["dialog"]


def test_review_window_opens_empty_and_fills_in(monkeypatch):
    action = review_action(monkeypatch)
    engine = action._engine
    seen = {}

    def while_open(dialog):
        seen["rows_at_open"] = dialog.tree.topLevelItemCount()
        seen["scanning"] = dialog._scanning
        seen["job_running"] = action._job is not None
        run_scan(action)
        seen["rows_after"] = dialog.tree.topLevelItemCount()
        seen["apply_after"] = dialog.apply_button.isEnabled()

    dialog = click_through(action, monkeypatch, while_open=while_open)
    assert seen == {
        "rows_at_open": 0,
        "scanning": True,
        "job_running": True,
        "rows_after": len(sample_plan().entries),
        "apply_after": True,
    }
    assert engine.phases == ["library", "onedrive"]
    assert dialog.plan is action._scan_plan
    assert action._state is None


def test_review_window_apply_starts_the_apply_job(monkeypatch):
    action = review_action(monkeypatch)
    started = []

    def fake_apply(manager, engine, plan, entries, adder, linker, callback, *, description=None):
        started.append((engine, plan, entries, adder, linker))
        return "apply"

    monkeypatch.setattr(action_mod, "start_apply_job", fake_apply)
    click_through(action, monkeypatch, accept=True, while_open=lambda _dialog: run_scan(action))

    assert action._job == "apply"
    assert action._state is not None  # kept open for the apply pass
    assert started[0][0] == action._engine
    assert started[0][1] is action._scan_plan
    assert {entry.status for entry in started[0][2]} == {"upload", "download"}
    assert (started[0][3], started[0][4]) == ("adder", "linker")


def test_review_window_accept_without_selection_shows_the_summary(monkeypatch):
    action = review_action(
        monkeypatch,
        FakeScanEngine(
            build_reconcile_plan(
                [LocalBook(book_id=9, title="Mobi", formats=("mobi",))],
                [],
            )
        ),
    )
    infos = []
    monkeypatch.setattr(action_mod.QMessageBox, "information", lambda *args: infos.append(args))
    click_through(action, monkeypatch, accept=True, while_open=lambda _dialog: run_scan(action))
    assert infos
    assert action._job is None
    assert action._state is None


def test_closing_the_window_while_scanning_aborts_the_scan(monkeypatch):
    action = review_action(monkeypatch)
    click_through(action, monkeypatch)  # closes immediately, scan still queued
    job = action.gui.job_manager.jobs[-1]
    assert job.abort.is_set()
    assert action._scan_finished is False
    assert action._scan_plan is None

    run_scan(action)  # the worker notices the abort
    assert action._state is None
    assert action.gui.status_bar.messages[-1] == "OneDrive matching cancelled."


def test_scan_failure_is_reported_inside_the_window(monkeypatch):
    action = review_action(monkeypatch, FakeScanEngine(error=RuntimeError("network down")))
    warnings = []
    monkeypatch.setattr(action_mod.QMessageBox, "warning", lambda *args: warnings.append(args))
    statuses = {}

    def while_open(dialog):
        run_scan(action)
        statuses["text"] = dialog.status.text()
        statuses["apply"] = dialog.apply_button.isEnabled()

    click_through(action, monkeypatch, while_open=while_open)
    assert warnings == []  # no modal box on top of the open window
    assert "network down" in statuses["text"]
    assert statuses["apply"] is False


def test_scan_progress_updates_the_open_window(monkeypatch):
    action = review_action(monkeypatch)
    dialog = ReviewDialog()
    action._review_dialog = dialog
    dialog.start_scanning()

    pending = build_pending_plan(sample_plan().local_books)
    action._on_scan_progress(pending, "library")
    assert dialog.tree.topLevelItemCount() == 2
    assert "Found 2 book(s)" in dialog.status.text()

    action._on_scan_progress(sample_plan(), "onedrive")
    assert "Reading book metadata" in dialog.status.text()

    # Without an open window the updates are ignored.
    action._review_dialog = None
    action._on_scan_progress(sample_plan(), "final")

    # A broken window must not break the running scan.
    class Broken:
        def set_plan(self, *_args):
            raise RuntimeError("paint failed")

    action._review_dialog = Broken()
    action._on_scan_progress(sample_plan(), "final")


def test_scan_status_updates_the_open_window(monkeypatch):
    action = review_action(monkeypatch)
    dialog = ReviewDialog()
    action._review_dialog = dialog
    dialog.start_scanning()

    action._on_scan_status(0.2, "Listing OneDrive… 2 folder(s), 3 file(s) found")
    assert dialog.progress.value() == 20
    assert "2 folder(s)" in dialog.status.text()

    action._on_scan_status(1.0, "1 already matched, 1 to download.")
    assert dialog.progress.value() == 100

    # Without an open window the updates are ignored.
    action._review_dialog = None
    action._on_scan_status(0.5, "ignored")

    # A broken window must not break the running scan.
    class Broken:
        def set_progress(self, *_args):
            raise RuntimeError("paint failed")

    action._review_dialog = Broken()
    action._on_scan_status(0.9, "boom")


def test_the_scan_job_forwards_progress_to_the_window(monkeypatch):
    action = review_action(monkeypatch)
    seen = []

    def while_open(dialog):
        job = action.gui.job_manager.jobs[-1]
        assert callable(job._on_progress)
        job._on_progress(0.25, "Listing OneDrive… 1 folder(s), 4 file(s) found")
        seen.append((dialog.progress.value(), dialog.status.text()))

    click_through(action, monkeypatch, while_open=while_open)
    assert seen == [(25, "Listing OneDrive… 1 folder(s), 4 file(s) found")]


def test_on_scan_done_without_a_window_persists_links(monkeypatch):
    action = review_action(monkeypatch)
    library = action._library
    plan = sample_plan()
    job = types.SimpleNamespace(failed=False, result=plan)
    action._job = job
    action._on_scan_done(job)
    assert action._job is None
    assert action._state is None
    matched = [entry for entry in plan.entries if entry.status == "matched"]
    assert len(library.identifiers) == len(matched)
    assert library.identifiers[0] == (1, "onedrive", "https://onedrive.test/m")


def test_on_scan_done_without_result_or_window(monkeypatch):
    action = review_action(monkeypatch)
    infos = []
    warnings = []
    monkeypatch.setattr(action_mod.QMessageBox, "information", lambda *args: infos.append(args))
    monkeypatch.setattr(action_mod.QMessageBox, "warning", lambda *args: warnings.append(args))

    action._on_scan_done(types.SimpleNamespace(failed=False, result=None))
    assert warnings
    assert action._state is None

    action = review_action(monkeypatch)
    action._on_scan_done(None)
    assert action._state is None


def test_on_apply_done_delegates(monkeypatch):
    action = make_action(monkeypatch)
    calls = []
    monkeypatch.setattr(action, "_on_job_done", lambda job: calls.append(job))
    action._on_apply_done("job")
    assert calls == ["job"]


def test_store_link_prefers_url_and_falls_back_to_item_id(monkeypatch):
    action = make_action(monkeypatch)
    library = FakeLibrary()
    action._library = library
    action._store_link(BookLink(1, "drive", "item", web_url="https://onedrive.test/1"))
    action._store_link(BookLink(2, "drive", "item-2"))
    assert library.identifiers == [
        (1, "onedrive", "https://onedrive.test/1"),
        (2, "onedrive", "item-2"),
    ]

    action._library = None

    def broken(_gui):
        raise RuntimeError("no library")

    monkeypatch.setattr(action_mod, "CalibreLibrary", broken)
    action._store_link(BookLink(3, "drive", "item-3"))


def test_on_job_done_killed_job_is_not_a_failure(monkeypatch):
    action = make_action(monkeypatch)
    job = killed_job(result=SyncResult(exported=2, cancelled=True))
    action._job = job
    infos = []
    warnings = []
    monkeypatch.setattr(action_mod.QMessageBox, "information", lambda *args: infos.append(args))
    monkeypatch.setattr(action_mod.QMessageBox, "warning", lambda *args: warnings.append(args))

    action._on_job_done(job)
    assert warnings == []
    assert infos
    assert "Cancelled." in infos[0][2]
    assert "exported 2" in infos[0][2]
    assert action._job is None


def test_on_job_done_killed_before_starting(monkeypatch):
    action = make_action(monkeypatch)
    job = killed_job()
    action._job = job
    infos = []
    warnings = []
    monkeypatch.setattr(action_mod.QMessageBox, "information", lambda *args: infos.append(args))
    monkeypatch.setattr(action_mod.QMessageBox, "warning", lambda *args: warnings.append(args))

    action._on_job_done(job)
    assert warnings == []
    assert infos[0][2] == "Cancelled."


def test_on_scan_done_killed_job_is_not_a_failure(monkeypatch):
    action = review_action(monkeypatch)
    job = killed_job()
    action._job = job
    warnings = []
    monkeypatch.setattr(action_mod.QMessageBox, "warning", lambda *args: warnings.append(args))

    action._on_scan_done(job)
    assert warnings == []
    assert action._state is None
    assert action.gui.status_bar.messages[-1] == "OneDrive matching cancelled."


def test_on_scan_done_cancelled_error_is_not_a_failure(monkeypatch):
    action = review_action(monkeypatch)
    job = types.SimpleNamespace(
        killed=False,
        failed=True,
        exception=CancelledError("Cancelled."),
        result=None,
        duration=0.1,
        abort=threading.Event(),
        worker_active=False,
    )
    action._job = job
    warnings = []
    monkeypatch.setattr(action_mod.QMessageBox, "warning", lambda *args: warnings.append(args))
    action._on_scan_done(job)
    assert warnings == []
    assert action.gui.status_bar.messages[-1] == "OneDrive matching cancelled."


def test_sync_now_waits_for_a_running_job(monkeypatch):
    action = make_action(monkeypatch)
    action._job = types.SimpleNamespace(
        killed=False,
        failed=False,
        exception=None,
        result=None,
        duration=None,
        abort=threading.Event(),
        worker_active=False,
    )
    called = []
    monkeypatch.setattr(action, "open_config", lambda: called.append(1) or True)
    action.sync_now()
    assert called == []
    assert action._job is not None


def test_late_callback_of_a_superseded_job_is_ignored(monkeypatch):
    action = make_action(monkeypatch)
    action._job = "new-job"
    warnings = []
    monkeypatch.setattr(action_mod.QMessageBox, "warning", lambda *args: warnings.append(args))
    old = types.SimpleNamespace(
        killed=True,
        failed=True,
        exception=None,
        result=None,
        duration=0.0,
        abort=threading.Event(),
        worker_active=False,
    )
    action._on_job_done(old)
    action._on_scan_done(old)
    assert action._job == "new-job"
    assert warnings == []


# -- bulk review actions -----------------------------------------------------


def test_review_window_bulk_actions_start_a_task(monkeypatch):
    for choice, operation, status in (
        ("upload_all", "upload_all", "Uploading"),
        ("download_all", "download_all", "Downloading"),
        ("clear_backup", "clear_backup", "Deleting"),
    ):
        action = review_action(monkeypatch)
        started = []

        def fake_task(
            manager,
            engine,
            task_operation,
            callback,
            *,
            description=None,
            _started=started,
        ):
            _started.append((engine, task_operation, callback, description))
            return "task"

        monkeypatch.setattr(action_mod, "start_task_job", fake_task)
        click_through(
            action,
            monkeypatch,
            accept=True,
            choice=choice,
            while_open=lambda _dialog, _action=action: run_scan(_action),
        )

        assert action._job == "task"
        assert action._task == operation
        assert started[0][0] == action._engine
        assert started[0][1] == operation
        assert status in action.gui.status_bar.messages[-1]


def task_action(monkeypatch, task="upload_all"):
    action = review_action(monkeypatch)
    action._task = task
    return action


def run_task(action, *, failed=False, result=None, killed=False, exception=None):
    """Call the completion callback with a job the action recognises as its own."""
    job = types.SimpleNamespace(failed=failed, result=result, killed=killed, exception=exception)
    action._job = job
    action._on_task_done(job)
    return job


def test_on_task_done_reports_the_summary(monkeypatch):
    action = task_action(monkeypatch)
    infos = []
    monkeypatch.setattr(action_mod.QMessageBox, "information", lambda *args: infos.append(args))

    run_task(action, result=SyncResult(exported=3))

    assert action._job is None
    assert action._task == ""
    assert action._state is None
    assert infos and "exported 3" in infos[-1][2]


def test_on_task_done_reports_errors(monkeypatch):
    action = task_action(monkeypatch, "download_all")
    warnings = []
    monkeypatch.setattr(action_mod.QMessageBox, "warning", lambda *args: warnings.append(args))

    result = SyncResult(imported=1, errors=["bad.epub: boom"])
    run_task(action, result=result)

    assert warnings and "boom" in warnings[-1][2]
    assert action._state is None


def test_on_task_done_reports_a_failed_job(monkeypatch):
    action = task_action(monkeypatch)
    warnings = []
    monkeypatch.setattr(action_mod.QMessageBox, "warning", lambda *args: warnings.append(args))

    run_task(action, failed=True, exception=RuntimeError("network down"))

    assert warnings and "network down" in warnings[-1][2]
    assert action._job is None
    assert action._state is None


def test_on_task_done_reports_cancellation(monkeypatch):
    action = task_action(monkeypatch)

    run_task(action, killed=True)

    assert action.gui.status_bar.messages[-1] == "OneDrive action cancelled."
    assert action._state is None


def test_on_task_done_without_a_job_or_result(monkeypatch):
    action = task_action(monkeypatch)
    action._on_task_done(None)
    assert action._state is None

    action = task_action(monkeypatch)
    run_task(action)
    assert action._state is None


def test_on_task_done_ignores_a_stale_job(monkeypatch):
    action = task_action(monkeypatch)
    action._job = object()

    action._on_task_done(types.SimpleNamespace(failed=False, result=SyncResult()))


def test_clear_backup_clears_the_calibre_identifiers(monkeypatch):
    action = task_action(monkeypatch, "clear_backup")
    library = action._library
    monkeypatch.setattr(action_mod.QMessageBox, "information", lambda *args: None)

    run_task(action, result=SyncResult(deleted=4))

    assert library.cleared == 1


def test_other_bulk_actions_keep_the_identifiers(monkeypatch):
    action = task_action(monkeypatch, "upload_all")
    library = action._library
    monkeypatch.setattr(action_mod.QMessageBox, "information", lambda *args: None)

    run_task(action, result=SyncResult(exported=1))

    assert library.cleared == 0


def test_clear_identifiers_ignores_a_broken_library(monkeypatch):
    action = task_action(monkeypatch)
    monkeypatch.setattr(action_mod.QMessageBox, "information", lambda *args: None)

    class Broken:
        def clear_identifier(self, _key):
            raise RuntimeError("db closed")

    action._library = Broken()
    action._task = "clear_backup"
    run_task(action, result=SyncResult(deleted=1))

    # The remote files are already gone; the failure is only logged.
    assert action._state is None


# -- folder picker and index gate -------------------------------------------


def test_pick_folder_uses_the_configured_folder_as_initial(monkeypatch):
    action = make_action(monkeypatch)
    action._engine = types.SimpleNamespace(graph="graph")
    settings = Settings(
        client_id="c",
        drive_id="d",
        folder_id="f",
        folder_path="/Books",
        folder_name="Books",
    )
    captured = []

    def fake_picker(parent, graph, *, initial=None):
        captured.append((parent, graph, initial))
        return FolderRef("d", "f", "Books", "/Books")

    monkeypatch.setattr(action_mod, "open_folder_picker", fake_picker)
    folder = action._pick_folder(settings)

    assert folder == FolderRef("d", "f", "Books", "/Books")
    assert captured[0][1] == "graph"
    assert captured[0][2] == FolderRef("d", "f", "Books", "/Books")


def test_pick_folder_reports_failures(monkeypatch):
    action = make_action(monkeypatch)
    action._engine = types.SimpleNamespace(graph="graph")
    warnings = []

    def fail(*_args, **_kwargs):
        raise RuntimeError("graph down")

    monkeypatch.setattr(action_mod, "open_folder_picker", fail)
    monkeypatch.setattr(action_mod.QMessageBox, "warning", lambda *args: warnings.append(args))

    assert action._pick_folder(Settings()) is None
    assert warnings and "graph down" in warnings[0][2]


def test_review_now_cancels_when_the_folder_picker_is_cancelled(monkeypatch):
    action = review_action(monkeypatch)
    monkeypatch.setattr(action, "_pick_folder", lambda _settings: None)
    opened = []
    monkeypatch.setattr(action, "_open_review", lambda: opened.append(1))

    action.review_now()

    assert opened == []
    assert action._state is None


def test_review_now_remembers_the_chosen_folder(monkeypatch):
    action = review_action(monkeypatch)
    settings = Settings(client_id="c", drive_id="old", folder_id="old")
    saved = []
    monkeypatch.setattr(action_mod, "save_settings", lambda value: saved.append(value))
    folder = FolderRef("new-drive", "new-folder", "Backup", "/Backup")

    action._remember_folder(settings, folder)

    assert (settings.drive_id, settings.folder_id) == ("new-drive", "new-folder")
    assert (settings.folder_name, settings.folder_path) == ("Backup", "/Backup")
    assert saved == [settings]


def test_review_now_without_index_offers_clear_and_upload(monkeypatch):
    action = review_action(monkeypatch, FakeScanEngine(sample_plan(), has_index=False))
    monkeypatch.setattr(
        action_mod.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    started = []

    def fake_task(_manager, _engine, task, _callback, *, description=None):
        started.append(task)
        return "task"

    monkeypatch.setattr(action_mod, "start_task_job", fake_task)
    opened = []
    monkeypatch.setattr(action, "_open_review", lambda: opened.append(1))

    action.review_now()
    run_index_check(action)

    assert started == ["clear_backup"]
    assert action._task == "clear_backup"
    assert action._next_task == "upload_all"
    assert opened == []
    assert action._state is not None


def test_review_now_without_index_can_be_cancelled(monkeypatch):
    action = review_action(monkeypatch, FakeScanEngine(sample_plan(), has_index=False))
    monkeypatch.setattr(
        action_mod.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.No,
    )
    started = []
    opened = []
    monkeypatch.setattr(action_mod, "start_task_job", lambda *_args, **_kwargs: started.append(1))
    monkeypatch.setattr(action, "_open_review", lambda: opened.append(1))

    action.review_now()
    run_index_check(action)

    assert opened == [] and started == []
    assert action._state is None
    assert "cancelled" in action.gui.status_bar.messages[-1].lower()


def test_review_now_with_an_index_opens_the_window(monkeypatch):
    action = review_action(monkeypatch)
    opened = []
    monkeypatch.setattr(action, "_open_review", lambda: opened.append(1))
    action.review_now()
    run_index_check(action)
    assert opened == [1]


def index_job(result, *, failed=False, killed=False, error=None):
    return types.SimpleNamespace(
        result=result,
        failed=failed,
        killed=killed,
        exception=error,
        abort=threading.Event(),
        duration=0.0,
        worker_active=False,
    )


def test_index_check_failure_is_reported_and_closes_the_state(monkeypatch):
    action = review_action(monkeypatch)
    warnings = []
    monkeypatch.setattr(action_mod.QMessageBox, "warning", lambda *args: warnings.append(args))

    action._job = index_job(None, failed=True, error="graph down")
    action._on_index_checked(action._job)

    assert warnings and "graph down" in warnings[0][2]
    assert action._state is None
    assert action._job is None


def test_index_check_without_result_warns_instead_of_clearing(monkeypatch):
    action = review_action(monkeypatch)
    warnings = []
    asked = []
    monkeypatch.setattr(action_mod.QMessageBox, "warning", lambda *args: warnings.append(args))
    monkeypatch.setattr(action_mod.QMessageBox, "question", lambda *args: asked.append(args))

    action._job = index_job(None)
    action._on_index_checked(action._job)

    assert warnings
    assert asked == []
    assert action._state is None


def test_cancelled_index_check_is_not_an_error(monkeypatch):
    action = review_action(monkeypatch)
    warnings = []
    monkeypatch.setattr(action_mod.QMessageBox, "warning", lambda *args: warnings.append(args))

    action._job = index_job(None, killed=True, failed=True)
    action._on_index_checked(action._job)

    assert warnings == []
    assert action._state is None
    assert "cancelled" in action.gui.status_bar.messages[-1].lower()


def test_clear_backup_chains_upload_all(monkeypatch):
    action = review_action(monkeypatch)
    action._next_task = "upload_all"
    started = []
    monkeypatch.setattr(action, "_start_task", lambda task, status=None: started.append(task))

    run_task(action, result=SyncResult(deleted=2))

    assert started == ["upload_all"]
    assert action._state is not None


def test_clear_backup_does_not_chain_after_an_error(monkeypatch):
    action = review_action(monkeypatch)
    action._next_task = "upload_all"
    started = []
    warnings = []
    monkeypatch.setattr(action, "_start_task", lambda task, status=None: started.append(task))
    monkeypatch.setattr(action_mod.QMessageBox, "warning", lambda *args: warnings.append(args))

    run_task(action, result=SyncResult(deleted=1, errors=["could not delete"]))

    assert started == []
    assert action._state is None
    assert warnings
