"""Settings and refresh-token storage tests."""

from __future__ import annotations

from onedrive_sync.common import config
from onedrive_sync.common.config import ConfigTokenStore, Settings


class FakeKeyring:
    def __init__(self) -> None:
        self.prefs: dict = {}


def test_settings_from_dict_defaults_and_clamping():
    settings = Settings.from_dict(None)
    assert settings.export_format == config.DEFAULT_EXPORT_FORMAT
    assert settings.sync_interval_minutes == config.DEFAULT_SYNC_INTERVAL

    settings = Settings.from_dict(
        {
            "sync_interval_minutes": "9999",
            "export_format": "nope",
            "import_enabled": 0,
            "export_enabled": "yes",
            "client_id": None,
        }
    )
    assert settings.sync_interval_minutes == config.MAX_SYNC_INTERVAL
    assert settings.export_format == config.DEFAULT_EXPORT_FORMAT
    assert settings.import_enabled is False
    assert settings.export_enabled is True
    assert settings.client_id == config.DEFAULT_CLIENT_ID

    settings = Settings.from_dict({"sync_interval_minutes": "not-an-int"})
    assert settings.sync_interval_minutes == config.DEFAULT_SYNC_INTERVAL
    settings = Settings.from_dict({"sync_interval_minutes": -5})
    assert settings.sync_interval_minutes == config.MIN_SYNC_INTERVAL


def test_load_and_save_settings_round_trip(prefs_backend):
    defaults = config.load_settings()
    assert defaults.client_id == config.DEFAULT_CLIENT_ID
    settings = Settings(
        client_id="client",
        drive_id="drive",
        folder_id="folder",
        folder_path="/Books",
        folder_name="Books",
        export_subfolder="Export",
        export_format="pdf",
        import_enabled=False,
        export_enabled=True,
        auto_sync=False,
        sync_interval_minutes=30,
        debug=True,
    )
    config.save_settings(settings)
    loaded = config.load_settings()
    assert loaded == settings


def test_load_settings_uses_default_client_id_when_blank(prefs_backend):
    prefs_backend.set("client_id", "")
    assert config.load_settings().client_id == config.DEFAULT_CLIENT_ID
    prefs_backend.set("client_id", "custom")
    assert config.load_settings().client_id == "custom"


def test_is_configured():
    assert not config.is_configured(Settings())
    assert not config.is_configured(Settings(client_id="c", drive_id="d"))
    assert config.is_configured(Settings(client_id="c", drive_id="d", folder_id="f"))


def test_refresh_token_keyring_preferred(monkeypatch, prefs_backend):
    ring = FakeKeyring()
    monkeypatch.setattr(config, "_keyring", lambda: ring)
    config.set_refresh_token("token")
    assert ring.prefs[config._REFRESH_TOKEN_KEY] == "token"
    assert prefs_backend.get("refresh_token") == ""
    assert config.get_refresh_token() == "token"

    ring.prefs[config._REFRESH_TOKEN_KEY] = ""
    prefs_backend.set("refresh_token", "fallback")
    assert config.get_refresh_token() == "fallback"

    config.clear_refresh_token()
    assert config._REFRESH_TOKEN_KEY not in ring.prefs
    assert prefs_backend.get("refresh_token") == ""


def test_refresh_token_falls_back_when_keyring_unavailable(monkeypatch, prefs_backend):
    monkeypatch.setattr(config, "_keyring", lambda: None)
    config.set_refresh_token("token")
    assert prefs_backend.get("refresh_token") == "token"
    assert config.get_refresh_token() == "token"


def test_refresh_token_falls_back_when_keyring_raises(monkeypatch, prefs_backend):
    class Broken:
        @property
        def prefs(self):
            raise RuntimeError("no keyring")

    monkeypatch.setattr(config, "_keyring", lambda: Broken())
    config.set_refresh_token("token")
    assert prefs_backend.get("refresh_token") == "token"


def test_refresh_token_corrupt_value(monkeypatch, prefs_backend):
    monkeypatch.setattr(config, "_keyring", lambda: None)

    def broken_get(_key, _default=None):
        raise ValueError("corrupt")

    prefs_backend.get = broken_get
    assert config.get_refresh_token() == ""


def test_no_prefs_runtime(monkeypatch):
    monkeypatch.setattr(config, "prefs", None)
    assert config.load_settings() == Settings()
    config.save_settings(Settings(client_id="x"))
    assert config.get_refresh_token() == ""
    config.set_refresh_token("token")
    config.clear_refresh_token()


def test_config_token_store(monkeypatch):
    store = ConfigTokenStore()
    values = {}
    monkeypatch.setattr(config, "get_refresh_token", lambda: values.get("token", ""))
    monkeypatch.setattr(
        config, "set_refresh_token", lambda token: values.__setitem__("token", token)
    )
    monkeypatch.setattr(config, "clear_refresh_token", lambda: values.clear())
    assert store.get() == ""
    store.set("abc")
    assert store.get() == "abc"
    store.clear()
    assert store.get() == ""
