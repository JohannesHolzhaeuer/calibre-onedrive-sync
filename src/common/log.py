"""Central logging for the onedrive-sync plugin.

All plugin loggers are obtained through :func:`get_logger` so this module is the
single place that decides where records go. Inside calibre, records propagate to
calibre's logging machinery (the ``calibre-debug`` console / log file).

With ``Settings.debug`` on, diagnostics are emitted at DEBUG level; otherwise
the threshold stays at WARNING so only actionable messages reach the log.
"""

from __future__ import annotations

import logging


def get_logger(name: str, *, debug: bool = False) -> logging.Logger:
    """Return a named logger for ``name``.

    ``debug`` (the plugin's ``Settings.debug``) raises the logger's threshold to
    DEBUG so sync progress becomes visible; otherwise it stays at WARNING.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG if debug else logging.WARNING)
    return logger
