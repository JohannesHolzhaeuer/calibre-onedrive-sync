"""Tests for the threaded sync job and GUI-thread book adder."""

from __future__ import annotations

import types

import pytest

from onedrive_sync.common.models import SyncResult
from onedrive_sync.gui.sync_job import MainThreadBookAdder, SyncJob, start_sync_job


def test_main_thread_book_adder_returns_result():
    adder = MainThreadBookAdder(lambda path: len(path))
    assert adder("abcd") == 4


def test_main_thread_book_adder_propagates_errors():
    def fail(_path):
        raise ValueError("bad book")

    adder = MainThreadBookAdder(fail)
    with pytest.raises(ValueError, match="bad book"):
        adder("x")


class FakeEngine:
    def __init__(self) -> None:
        self.settings = types.SimpleNamespace(folder_path="", folder_id="folder")
        self.on_progress = None
        self.should_abort = None
        self.add_book = None
        self.result = SyncResult(imported=1)
        self.ran = False

    def run(self):
        self.ran = True
        self.on_progress(0.5, "half")
        return self.result


def test_sync_job_runs_engine():
    engine = FakeEngine()
    done = []
    job = SyncJob(engine, lambda _path: 1, done.append, description="Sync")
    job.start_work()
    assert job.result is engine.result
    assert done == [job]
    assert engine.ran
    assert callable(engine.add_book)
    assert engine.should_abort() is False


def test_sync_job_reports_engine_errors():
    engine = FakeEngine()

    def run():
        raise RuntimeError("boom")

    engine.run = run
    done = []
    job = SyncJob(engine, lambda _path: 1, done.append)
    job.start_work()
    assert job.failed
    assert str(job.exception) == "boom"
    assert done == [job]


def test_start_sync_job_enqueues():
    class Manager:
        def __init__(self):
            self.jobs = []

        def run_threaded_job(self, job):
            self.jobs.append(job)

    manager = Manager()
    engine = FakeEngine()
    job = start_sync_job(manager, engine, lambda _path: 1, lambda _job: None)
    assert manager.jobs == [job]
    assert job.engine is engine
