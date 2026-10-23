"""Shared pytest fixtures.

calibre loads the plugin under the ``calibre_plugins.onedrive_sync`` namespace
from the zip; the tree's *relative* imports resolve the same way in both
environments. To mirror that exactly in tests, ``src/`` is registered as the
top-level package ``onedrive_sync`` so tests import
``from onedrive_sync.common.config import ...`` against the identical code path
calibre uses.

``pyproject.toml`` sets ``pythonpath = ["src"]``; this conftest also injects
``src/`` into ``sys.path`` as a belt-and-braces fallback for setups that ignore
the pytest config.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

_TESTS = os.path.dirname(__file__)
if _TESTS not in sys.path:
    sys.path.insert(0, _TESTS)

from calibre_shims import install_fake_calibre  # noqa: E402
from qt_shims import install_fake_qt  # noqa: E402

SRC = os.path.abspath(os.path.join(_TESTS, "..", "src"))
if SRC not in sys.path:
    sys.path.insert(0, SRC)

install_fake_calibre()
install_fake_qt()

_plugin_spec = importlib.util.spec_from_file_location(
    "onedrive_sync", os.path.join(SRC, "__init__.py")
)
_plugin_module = importlib.util.module_from_spec(_plugin_spec)
sys.modules["onedrive_sync"] = _plugin_module
_plugin_spec.loader.exec_module(_plugin_module)


class InMemoryStore:
    """Minimal stand-in for ``calibre.utils.config.JSONConfig``."""

    def __init__(self, defaults: dict):
        self._data = dict(defaults)
        self.defaults = {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value

    def get_store(self):
        return self._data


@pytest.fixture()
def prefs_backend(monkeypatch):
    """Point ``onedrive_sync.common.config.prefs`` at an in-memory store."""
    from onedrive_sync.common import config

    backend = InMemoryStore(config.DEFAULTS)
    monkeypatch.setattr(config, "prefs", backend)
    return backend
