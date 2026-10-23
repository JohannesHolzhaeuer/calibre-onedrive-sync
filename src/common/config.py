"""Persistent settings for the onedrive-sync plugin.

This module is the single source of truth for stored configuration. It is
GUI-free: it may import ``calibre.utils.config`` (JSONConfig) but must NOT
import ``qt`` or ``calibre.gui2``, so it stays importable in a non-GUI /
headless process.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields

try:
    from calibre.utils.config import JSONConfig
except ImportError:  # pragma: no cover - only hit outside the calibre runtime
    JSONConfig = None  # type: ignore[assignment,misc]

#: JSONConfig root key for this plugin's settings.
_PLUGIN_KEY = "plugins/onedrive_sync"

#: Supported sync strategies. ``backup`` only adds/updates files in OneDrive;
#: ``mirror`` also removes files that no longer exist in the calibre library.
SYNC_MODES = ("backup", "mirror")
DEFAULT_SYNC_MODE = "backup"


@dataclass
class Settings:
    """User-visible configuration for the OneDrive sync."""

    library_path: str = ""
    onedrive_folder: str = ""
    remote_subfolder: str = "calibre-library"
    sync_mode: str = DEFAULT_SYNC_MODE
    delete_removed: bool = False
    include_metadata_db: bool = True
    debug: bool = False

    def as_dict(self) -> dict:
        """Return the settings as a plain JSON-serialisable dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> Settings:
        """Build settings from a possibly partial/legacy dict."""
        data = data or {}
        values: dict = {}
        for field in fields(cls):
            default = field.default
            value = data.get(field.name, default)
            if isinstance(default, bool):
                values[field.name] = bool(value)
            elif isinstance(default, str):
                values[field.name] = default if value is None else str(value)
            else:  # pragma: no cover - every current field is bool/str
                values[field.name] = value
        settings = cls(**values)
        if settings.sync_mode not in SYNC_MODES:
            settings.sync_mode = DEFAULT_SYNC_MODE
        return settings


DEFAULTS = Settings().as_dict()

prefs = None
if JSONConfig is not None:
    prefs = JSONConfig(_PLUGIN_KEY)
    for _key, _value in DEFAULTS.items():
        prefs.defaults[_key] = _value


def load_settings() -> Settings:
    """Load the persisted settings into a fresh :class:`Settings`."""
    if prefs is None:
        return Settings.from_dict(DEFAULTS)
    data = {key: prefs.get(key, default) for key, default in DEFAULTS.items()}
    return Settings.from_dict(data)


def save_settings(settings: Settings) -> None:
    """Persist ``settings`` (no-op outside the calibre runtime)."""
    if prefs is None:
        return
    for key, value in settings.as_dict().items():
        prefs.set(key, value)


def is_configured(settings: Settings | None = None) -> bool:
    """True when enough paths are set for a sync attempt."""
    current = settings if settings is not None else load_settings()
    return bool(
        current.library_path.strip()
        and current.onedrive_folder.strip()
        and current.remote_subfolder.strip()
    )
