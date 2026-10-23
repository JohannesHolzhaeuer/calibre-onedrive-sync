"""GNU gettext wrapper for the plugin UI.

Calibre injects a pre-bound ``load_translations()`` into every plugin module
loaded from its zip. Calling it installs ``_`` for the UI's current language
into that module's globals.

Pure modules in ``src/common/`` must stay importable headlessly (no calibre), so
they cannot rely on the injected builtin. Instead every module that renders
user-facing text imports the single shared callable from here::

    from ..common.i18n import _

``_`` is a swappable dispatch object: :func:`activate` binds it to calibre's
real gettext after plugin startup; outside calibre (tests, CI) it degrades to
the identity function. Always call ``_(...)`` at the point of use — never bind
it to a module constant — so the current language is applied.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast


def _identity(text: str) -> str:
    return text


class _Translator:
    """Lazily-swappable translation callable shared by all modules."""

    def __init__(self) -> None:
        self._fn: Callable[[str], str] = _identity

    def __call__(self, text: str) -> str:
        try:
            return self._fn(text)
        except Exception:  # noqa: BLE001 - a broken gettext must not break the UI
            return text

    def bind(self, fn: Callable[[str], str]) -> None:
        self._fn = fn if callable(fn) else _identity


#: Plugin-wide translation function. Use ``from common.i18n import _``.
_: _Translator = _Translator()


def activate(module=None) -> None:
    """Bind ``_`` to calibre's real gettext; no-op outside calibre.

    Inside calibre, every plugin module loaded from the zip carries an injected
    ``load_translations`` builtin. ``module`` is the ``globals()`` dict of the
    plugin module calling this. Headless imports see no injected builtin and
    keep the identity fallback.
    """
    module = module if isinstance(module, dict) else globals()
    injected = module.get("load_translations")
    if injected is None or injected is activate:
        return
    try:
        injected()
        candidate = module.get("_")
        if callable(candidate) and candidate is not _:
            _.bind(cast("Callable[[str], str]", candidate))
    except Exception:  # noqa: BLE001 - a broken locale must not break the plugin
        pass
