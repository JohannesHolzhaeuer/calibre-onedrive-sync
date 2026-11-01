"""Single source of truth for the plugin's version.

The version string and tuple used across the plugin and by the build tooling
are defined here and **nowhere else**. ``src/__init__.py`` exposes the tuple on
the plugin class, so bumping one value updates every consumer.

release-please keeps this file in sync: it rewrites ``__version__`` (the
``# x-release-please-version`` marker below defines the replace target) when a
release PR is created. See ``build/sync_version.py``.
"""

from __future__ import annotations

__version__ = "0.6.0"  # x-release-please-version

#: ``(major, minor, patch)`` tuple consumed by the calibre plugin class.
VERSION_TUPLE: tuple[int, int, int] = tuple(int(part) for part in __version__.split("."))  # type: ignore[assignment]
