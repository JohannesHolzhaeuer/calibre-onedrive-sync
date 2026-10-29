"""Persistent settings and refresh-token storage for the OneDrive sync.

This module is the single source of truth for stored configuration. It is
GUI-free: it may import ``calibre.utils.config`` (JSONConfig) and calibre's
keyring helper, but must NOT import ``qt`` or ``calibre.gui2``.

The refresh token is a secret. It is stored in the platform keyring when
available and only falls back to calibre's JSON config when the keyring is not
usable. The short-lived access token is never persisted.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields

from .auth import TokenStore
from .defaults import DEFAULT_CLIENT_ID

try:
    from calibre.utils.config import JSONConfig
except ImportError:  # pragma: no cover - only hit outside the calibre runtime
    JSONConfig = None  # type: ignore[assignment,misc]

#: JSONConfig root key for this plugin's settings.
_PLUGIN_KEY = "plugins/onedrive_sync"
_REFRESH_TOKEN_KEY = "onedrive_sync.refresh_token"

EXPORT_FORMATS = ("epub_or_pdf", "epub", "pdf")
DEFAULT_EXPORT_FORMAT = "epub_or_pdf"
DEFAULT_SYNC_INTERVAL = 15
MIN_SYNC_INTERVAL = 1
MAX_SYNC_INTERVAL = 1440

#: Graph throttles per account, not per file, so a few uploads at once scale
#: almost linearly. Above ~8 the service starts answering 429/503.
DEFAULT_UPLOAD_WORKERS = 3
MIN_UPLOAD_WORKERS = 1
MAX_UPLOAD_WORKERS = 8


@dataclass
class Settings:
    """User-visible configuration for the OneDrive sync."""

    client_id: str = DEFAULT_CLIENT_ID
    account_name: str = ""
    drive_id: str = ""
    folder_id: str = ""
    folder_path: str = ""
    folder_name: str = ""
    #: Legacy configuration fields, kept so old settings still load. The plugin
    #: now uploads directly into the selected folder and never syncs by itself.
    export_subfolder: str = "Calibre Export"
    export_directly: bool = False
    export_format: str = DEFAULT_EXPORT_FORMAT
    #: Concurrent uploads of a bulk action; 1 is the plain sequential pass.
    upload_workers: int = DEFAULT_UPLOAD_WORKERS
    import_enabled: bool = True
    export_enabled: bool = True
    auto_sync: bool = True
    sync_interval_minutes: int = DEFAULT_SYNC_INTERVAL
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
            elif isinstance(default, int):
                try:
                    values[field.name] = int(value)
                except (TypeError, ValueError):
                    values[field.name] = default
            elif isinstance(default, str):
                values[field.name] = default if value is None else str(value)
            else:  # pragma: no cover - every current field is bool/int/str
                values[field.name] = value
        settings = cls(**values)
        settings.sync_interval_minutes = max(
            MIN_SYNC_INTERVAL, min(MAX_SYNC_INTERVAL, settings.sync_interval_minutes)
        )
        settings.upload_workers = min(
            MAX_UPLOAD_WORKERS, max(MIN_UPLOAD_WORKERS, settings.upload_workers)
        )
        if settings.export_format not in EXPORT_FORMATS:
            settings.export_format = DEFAULT_EXPORT_FORMAT
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
    if not str(data.get("client_id") or "").strip():
        data["client_id"] = DEFAULT_CLIENT_ID
    return Settings.from_dict(data)


def save_settings(settings: Settings) -> None:
    """Persist ``settings`` (no-op outside the calibre runtime)."""
    if prefs is None:
        return
    for key, value in settings.as_dict().items():
        prefs.set(key, value)


def is_configured(settings: Settings | None = None) -> bool:
    """True when the app ID and target folder are selected."""
    current = settings if settings is not None else load_settings()
    return bool(current.client_id.strip() and current.drive_id and current.folder_id)


# -- refresh-token storage --------------------------------------------------


def _keyring():
    try:
        from calibre.utils import keyring

        return keyring
    except Exception:  # noqa: BLE001 - keyring is best-effort
        return None


def get_refresh_token() -> str:
    """Return the stored refresh token (keyring first, JSON fallback)."""
    ring = _keyring()
    if ring is not None:
        try:
            token = ring.prefs.get(_REFRESH_TOKEN_KEY, "")
            if token:
                return str(token)
        except Exception:  # noqa: BLE001
            pass
    if prefs is not None:
        try:
            return str(prefs.get("refresh_token", "") or "")
        except Exception:  # noqa: BLE001 - a corrupt value means "signed out"
            return ""
    return ""


def set_refresh_token(token: str) -> None:
    """Persist the refresh token, preferring the platform keyring."""
    token = token or ""
    stored_in_keyring = False
    ring = _keyring()
    if ring is not None:
        try:
            if token:
                ring.prefs[_REFRESH_TOKEN_KEY] = token
            else:
                ring.prefs.pop(_REFRESH_TOKEN_KEY, None)
            stored_in_keyring = True
        except Exception:  # noqa: BLE001
            stored_in_keyring = False
    if prefs is not None:
        prefs.set("refresh_token", "" if stored_in_keyring else token)


def clear_refresh_token() -> None:
    """Forget the persisted refresh token."""
    set_refresh_token("")


class ConfigTokenStore(TokenStore):
    """TokenStore backed by calibre's keyring/JSON config."""

    def get(self) -> str:
        return get_refresh_token()

    def set(self, token: str) -> None:
        set_refresh_token(token)

    def clear(self) -> None:
        clear_refresh_token()
