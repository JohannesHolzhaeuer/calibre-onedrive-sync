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

    Loggers inherit the plugin-wide level set by :func:`configure_logging`.
    Passing ``debug=True`` pins one logger to DEBUG.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG if debug else logging.NOTSET)
    return logger


def configure_logging(debug: bool) -> None:
    """Apply the ``Settings.debug`` switch to all plugin loggers."""
    logging.getLogger("onedrive_sync").setLevel(logging.DEBUG if debug else logging.WARNING)
