"""Minimal calibre shims for unit tests.

CI has no calibre install, but importing the plugin package requires
``calibre.customize`` and importing the GUI action requires
``calibre.gui2.actions``. These stand-ins are injected into ``sys.modules`` once
at conftest import time; they expose only the surface the plugin touches.
"""

from __future__ import annotations

import sys
import threading
import types


class FakeCustomizeBase:
    """Stand-in base shared by ``calibre.customize`` plugin classes."""

    name = "Trivial Plugin"
    description = ""
    author = ""
    version = (0, 0, 0)
    minimum_calibre_version = (0, 0, 0)

    def initialize(self):
        pass

    def customization_help(self, gui=False):
        raise NotImplementedError


class FakeInterfaceActionBase(FakeCustomizeBase):
    """Stand-in for ``calibre.customize.InterfaceActionBase``."""

    actual_plugin = ""


class FakeSignal:
    def __init__(self) -> None:
        self._slots: list = []

    def connect(self, slot, *_args) -> None:
        self._slots.append(slot)

    def emit(self, *args) -> None:
        for slot in list(self._slots):
            slot(*args)


class FakeQAction:
    """Minimal ``QAction`` stand-in (icon + triggered signal)."""

    def __init__(self) -> None:
        self.icon = None
        self.triggered = FakeSignal()
        self._menu = None

    def setIcon(self, icon) -> None:
        self.icon = icon

    def setMenu(self, menu) -> None:
        self._menu = menu

    def menu(self):
        return self._menu


class FakeInterfaceAction:
    """Stand-in for ``calibre.gui2.actions.InterfaceAction``."""

    def __init__(self, parent=None, gui=None) -> None:
        self.gui = gui if gui is not None else parent
        self.qaction = FakeQAction()


class FakeThreadedJob:
    """Stand-in for ``calibre.gui2.threaded_jobs.ThreadedJob``."""

    def __init__(
        self,
        type_,
        description,
        func,
        args,
        kwargs,
        callback,
        max_concurrent_count=1,
        killable=True,
        log=None,
    ) -> None:
        self.type = type_
        self.description = description
        self.func = func
        self.args = args
        self.kwargs = dict(kwargs)
        self.callback = callback
        self.max_concurrent_count = max_concurrent_count
        self.killable = killable
        self.log = log
        self.result = None
        self.exception = None
        self.failed = False
        self.killed = False

    def start_work(self) -> None:
        self.kwargs.update(
            notifications=_FakeNotifications(),
            abort=threading.Event(),
            log=lambda *_args: None,
        )
        try:
            self.result = self.func(*self.args, **self.kwargs)
        except Exception as exc:  # noqa: BLE001 - mirrors calibre
            self.exception = exc
            self.failed = True
        self.callback(self)

    def kill(self) -> None:
        self.killed = True
        self.failed = True


class _FakeNotifications:
    def __init__(self) -> None:
        self.items: list = []

    def put(self, item) -> None:
        self.items.append(item)


class FakeJobManager:
    """Collects jobs so tests can run them explicitly."""

    def __init__(self) -> None:
        self.jobs: list = []

    def run_threaded_job(self, job) -> None:
        self.jobs.append(job)


def install_fake_calibre() -> None:
    """Idempotently install fake ``calibre`` modules for unit tests."""
    if "calibre.customize" in sys.modules:
        return

    actions_mod = types.ModuleType("calibre.gui2.actions")
    actions_mod.InterfaceAction = FakeInterfaceAction

    threaded_jobs_mod = types.ModuleType("calibre.gui2.threaded_jobs")
    threaded_jobs_mod.ThreadedJob = FakeThreadedJob

    gui2 = types.ModuleType("calibre.gui2")
    gui2.actions = actions_mod
    gui2.threaded_jobs = threaded_jobs_mod

    customize_mod = types.ModuleType("calibre.customize")
    customize_mod.Plugin = FakeCustomizeBase
    customize_mod.InterfaceActionBase = FakeInterfaceActionBase
    customize_mod.InvalidPlugin = Exception
    customize_mod.PluginNotFound = Exception

    calibre = types.ModuleType("calibre")
    calibre.gui2 = gui2
    calibre.customize = customize_mod

    sys.modules.setdefault("calibre", calibre)
    sys.modules.setdefault("calibre.gui2", gui2)
    sys.modules.setdefault("calibre.gui2.actions", actions_mod)
    sys.modules.setdefault("calibre.gui2.threaded_jobs", threaded_jobs_mod)
    sys.modules.setdefault("calibre.customize", customize_mod)
