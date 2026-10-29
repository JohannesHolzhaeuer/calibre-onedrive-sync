"""Tests for the threaded sync job and GUI-thread book adder."""

from __future__ import annotations

import types

import pytest

from onedrive_sync.common.models import SyncResult
from onedrive_sync.gui import sync_job
from onedrive_sync.gui.sync_job import (
    ApplyJob,
    IndexJob,
    MainThreadBookAdder,
    MainThreadBookLinker,
    ScanJob,
    TaskJob,
    start_apply_job,
    start_index_job,
    start_scan_job,
    start_task_job,
)


def test_main_thread_book_adder_returns_result():
    adder = MainThreadBookAdder(lambda path: len(path))
    assert adder("abcd") == 4


def test_main_thread_book_adder_propagates_errors():
    def fail(_path):
        raise ValueError("bad book")

    adder = MainThreadBookAdder(fail)
    with pytest.raises(ValueError, match="bad book"):
        adder("x")


def test_main_thread_call_runs_inline_on_its_own_thread(monkeypatch):
    """A call already made from the owning (GUI) thread skips the signal."""
    thread = object()
    seen = []
    adder = MainThreadBookAdder(lambda path: seen.append(path) or len(path))
    adder.thread = lambda: thread
    monkeypatch.setattr(sync_job, "QThread", types.SimpleNamespace(currentThread=lambda: thread))

    assert adder("abcd") == 4
    assert seen == ["abcd"]


def test_main_thread_book_linker_returns_result_and_propagates_errors():
    linker = MainThreadBookLinker(lambda link: link * 2)
    assert linker("a") == "aa"

    def fail(_link):
        raise ValueError("no db")

    with pytest.raises(ValueError, match="no db"):
        MainThreadBookLinker(fail)("x")


class FakeReviewEngine:
    def __init__(self) -> None:
        self.settings = types.SimpleNamespace(folder_path="", folder_id="folder")
        self.on_progress = None
        self.should_abort = None
        self.add_book = None
        self.link_book = None
        self.plan = types.SimpleNamespace(summary=lambda: "scan summary")
        self.result = SyncResult(imported=1)
        self.scanned = False
        self.applied = None
        self.has_index = True
        self.index_checked = False

    def index_available(self):
        self.index_checked = True
        return self.has_index

    def scan(self):
        self.scanned = True
        self.on_progress(0.2, "scan")
        return self.plan

    def apply_plan(self, plan, entries):
        self.applied = (plan, list(entries))
        self.on_progress(0.8, "apply")
        return self.result


def test_scan_job_runs_scan():
    engine = FakeReviewEngine()
    done = []
    job = ScanJob(engine, done.append, description="Scan")
    job.start_work()
    assert job.result is engine.plan
    assert engine.scanned
    assert done == [job]
    assert callable(engine.should_abort)
    assert job.killable


def test_index_job_checks_the_folder():
    engine = FakeReviewEngine()
    done = []
    job = IndexJob(engine, done.append, description="Index")
    job.start_work()
    assert job.result is True
    assert engine.index_checked
    assert done == [job]
    assert callable(engine.should_abort)
    assert job.killable


def test_start_index_job_enqueues():
    class Manager:
        def __init__(self):
            self.jobs = []

        def run_threaded_job(self, job):
            self.jobs.append(job)

    manager = Manager()
    engine = FakeReviewEngine()
    job = start_index_job(manager, engine, lambda _job: None)
    assert manager.jobs == [job]
    assert job.engine is engine


def test_apply_job_sets_callbacks_and_runs():
    engine = FakeReviewEngine()
    done = []
    entries = [object()]
    job = ApplyJob(
        engine,
        engine.plan,
        entries,
        lambda _path: 1,
        lambda _link: None,
        done.append,
        description="Apply",
    )
    job.start_work()
    assert job.result is engine.result
    assert engine.applied == (engine.plan, entries)
    assert callable(engine.add_book)
    assert callable(engine.link_book)
    assert done == [job]
    assert job.killable


def test_start_scan_and_apply_jobs_enqueue():
    class Manager:
        def __init__(self):
            self.jobs = []

        def run_threaded_job(self, job):
            self.jobs.append(job)

    manager = Manager()
    engine = FakeReviewEngine()
    scan = start_scan_job(manager, engine, lambda _job: None)
    apply = start_apply_job(
        manager,
        engine,
        engine.plan,
        [],
        lambda _path: 1,
        lambda _link: None,
        lambda _job: None,
    )
    assert manager.jobs == [scan, apply]
    assert scan.engine is engine
    assert apply.entries == []


def test_kill_sets_abort_event_and_skips_callback():
    engine = FakeReviewEngine()
    done = []
    job = ScanJob(engine, done.append, description="Scan")
    job.kill()
    assert job.killed
    assert job.abort.is_set()
    assert job.worker_active is False
    # calibre never invokes the completion callback for a killed job.
    assert done == []


def test_worker_active_tracks_the_run():
    seen = []

    class Engine(FakeReviewEngine):
        def scan(self):
            seen.append(job.worker_active)
            return self.plan

    engine = Engine()
    job = ScanJob(engine, lambda _job: None, description="Scan")
    assert job.worker_active is False
    job.start_work()
    assert seen == [True]
    assert job.worker_active is False


def test_job_abort_hook_stops_the_engine():
    engine = FakeReviewEngine()
    job = ScanJob(engine, lambda _job: None, description="Scan")
    job.start_work()
    assert engine.should_abort() is False
    job.kill()
    assert engine.should_abort() is True
    assert job.killable


def test_apply_job_exposes_abort_to_the_engine():
    engine = FakeReviewEngine()
    job = ApplyJob(
        engine,
        engine.plan,
        [],
        lambda _path: 1,
        lambda _link: None,
        lambda _job: None,
        description="Apply",
    )
    job.start_work()
    assert callable(engine.should_abort)
    job.kill()
    assert engine.should_abort() is True


def test_apply_job_forwards_live_progress():
    engine = FakeReviewEngine()
    progress = []
    job = ApplyJob(
        engine,
        engine.plan,
        [],
        lambda _path: 1,
        lambda _link: None,
        lambda _job: None,
        description="Apply",
        on_progress=lambda fraction, message: progress.append((fraction, message)),
    )
    job.start_work()

    # FakeReviewEngine.apply_plan() reports one progress event.
    assert progress == [(0.8, "apply")]


def test_apply_job_ignores_a_broken_progress_callback():
    engine = FakeReviewEngine()

    def boom(*_args):
        raise RuntimeError("window closed")

    done = []
    job = ApplyJob(
        engine,
        engine.plan,
        [],
        lambda _path: 1,
        lambda _link: None,
        done.append,
        description="Apply",
        on_progress=boom,
    )
    job.start_work()

    assert job.result is engine.result
    assert not job.failed
    assert done == [job]


def test_scan_job_forwards_the_live_plan_callback():
    engine = FakeReviewEngine()
    plans = []

    def on_plan(plan, phase):
        plans.append((plan, phase))

    job = ScanJob(engine, lambda _job: None, description="Scan", on_plan=on_plan)
    job.start_work()
    assert engine.on_plan is on_plan
    engine.on_plan(engine.plan, "library")
    assert plans == [(engine.plan, "library")]


def test_scan_job_forwards_live_progress():
    engine = FakeReviewEngine()
    progress = []

    job = ScanJob(
        engine,
        lambda _job: None,
        description="Scan",
        on_progress=lambda *args: progress.append(args),
    )
    job.start_work()
    # FakeReviewEngine.scan() reports one progress event.
    assert progress == [(0.2, "scan")]


def test_scan_job_ignores_a_broken_progress_callback():
    engine = FakeReviewEngine()
    done = []

    def boom(*_args):
        raise RuntimeError("window closed")

    job = ScanJob(engine, done.append, description="Scan", on_progress=boom)
    job.start_work()
    assert job.result is engine.plan
    assert not job.failed
    assert done == [job]


def test_start_scan_job_accepts_a_plan_callback():
    class Manager:
        def __init__(self):
            self.jobs = []

        def run_threaded_job(self, job):
            self.jobs.append(job)

    manager = Manager()
    engine = FakeReviewEngine()
    job = start_scan_job(manager, engine, lambda _job: None, on_plan=lambda *_args: None)
    assert manager.jobs == [job]
    assert callable(job._on_plan)


def test_start_scan_job_accepts_a_progress_callback():
    class Manager:
        def __init__(self):
            self.jobs = []

        def run_threaded_job(self, job):
            self.jobs.append(job)

    manager = Manager()
    job = start_scan_job(
        manager,
        FakeReviewEngine(),
        lambda _job: None,
        on_progress=lambda *_args: None,
    )
    assert manager.jobs == [job]
    assert callable(job._on_progress)


# -- bulk task jobs ----------------------------------------------------------


def test_task_job_runs_the_named_operation():
    engine = FakeReviewEngine()
    engine.cleared = False

    def clear_backup():
        engine.cleared = True
        engine.on_progress(0.4, "deleting")
        return SyncResult(deleted=2)

    engine.clear_backup = clear_backup
    done = []
    job = TaskJob(engine, "clear_backup", done.append, description="Clear")
    job.start_work()

    assert engine.cleared is True
    assert job.result.deleted == 2
    assert done == [job]
    assert callable(engine.should_abort)
    assert job.killable


def test_task_job_forwards_live_progress():
    engine = FakeReviewEngine()

    def clear_backup():
        engine.on_progress(0.4, "deleting")
        engine.on_progress(0.9, "deleting more")
        return SyncResult(deleted=2)

    engine.clear_backup = clear_backup
    progress = []
    job = TaskJob(
        engine,
        "clear_backup",
        lambda _job: None,
        description="Clear",
        on_progress=lambda fraction, message: progress.append((fraction, message)),
    )
    job.start_work()

    assert progress == [(0.4, "deleting"), (0.9, "deleting more")]


def test_task_job_ignores_a_broken_progress_callback():
    engine = FakeReviewEngine()

    def clear_backup():
        engine.on_progress(0.4, "deleting")
        return SyncResult(deleted=1)

    def boom(*_args):
        raise RuntimeError("window closed")

    engine.clear_backup = clear_backup
    done = []
    job = TaskJob(engine, "clear_backup", done.append, on_progress=boom)
    job.start_work()

    assert job.result.deleted == 1
    assert not job.failed
    assert done == [job]


def test_task_job_reports_engine_errors():
    engine = FakeReviewEngine()

    def upload_all():
        raise RuntimeError("quota")

    engine.upload_all = upload_all
    done = []
    job = TaskJob(engine, "upload_all", done.append)
    job.start_work()

    assert job.failed
    assert str(job.exception) == "quota"
    assert done == [job]


def test_task_job_forwards_engine_errors_to_the_log():
    engine = FakeReviewEngine()
    errors = ["a.epub: quota exceeded", "b.pdf: locked"]
    engine.upload_all = lambda: SyncResult(exported=1, errors=list(errors))
    logged = []
    job = TaskJob(engine, "upload_all", lambda _job: None, description="Upload")

    # calibre hands the worker a ``log`` callable; the fake job's ``start_work``
    # always passes a no-op, so drive the worker entry point with a recording log.
    result = job.func(
        *job.args,
        notifications=types.SimpleNamespace(put=lambda item: None),
        abort=job.abort,
        log=logged.append,
    )

    assert result.errors == errors
    assert logged[-2:] == errors


def test_start_task_job_enqueues():
    class Manager:
        def __init__(self):
            self.jobs = []

        def run_threaded_job(self, job):
            self.jobs.append(job)

    manager = Manager()
    engine = FakeReviewEngine()
    engine.upload_all = lambda: SyncResult(exported=1)
    job = start_task_job(manager, engine, "upload_all", lambda _job: None)

    assert manager.jobs == [job]
    assert job.engine is engine
    assert job.operation == "upload_all"
