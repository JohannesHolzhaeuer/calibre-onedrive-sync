"""Minimal calibre shims for unit tests.

CI has no calibre install, but importing the plugin package requires
``calibre.customize`` and importing the GUI action requires
``calibre.gui2.actions``. These stand-ins are injected into ``sys.modules`` once
at conftest import time; they expose only the surface the plugin touches.
"""

from __future__ import annotations

import sys
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


class FakeQAction:
    """Minimal ``QAction`` stand-in (icon + menu)."""

    def __init__(self) -> None:
        self.icon = None
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


def install_fake_calibre() -> None:
    """Idempotently install fake ``calibre`` modules for unit tests."""
    if "calibre.customize" in sys.modules:
        return

    actions_mod = types.ModuleType("calibre.gui2.actions")
    actions_mod.InterfaceAction = FakeInterfaceAction

    gui2 = types.ModuleType("calibre.gui2")
    gui2.actions = actions_mod

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
    sys.modules.setdefault("calibre.customize", customize_mod)
