"""Tests for the shared translation hook."""

from __future__ import annotations

from onedrive_sync.common import i18n


def test_default_translation_is_identity():
    assert i18n._("Hello") == "Hello"


def test_bind_and_activate():
    original = i18n._._fn

    def fake_load_translations():
        namespace["_"] = lambda text: f"translated:{text}"

    namespace = {"load_translations": fake_load_translations}
    i18n.activate(namespace)
    assert i18n._("Hello") == "translated:Hello"

    i18n._._fn = original


def test_activate_without_injected_loader_is_noop():
    i18n.activate({})
    assert i18n._("Hello") == "Hello"


def test_activate_ignores_broken_loader():
    namespace = {"load_translations": lambda: (_ for _ in ()).throw(RuntimeError("broken"))}
    i18n.activate(namespace)
    assert i18n._("Hello") == "Hello"


def test_translator_survives_exception():
    original = i18n._._fn

    def broken(_text):
        raise RuntimeError("locale broken")

    i18n._.bind(broken)
    assert i18n._("Hello") == "Hello"
    i18n._._fn = original


def test_bind_non_callable_falls_back():
    original = i18n._._fn
    i18n._.bind(None)
    assert i18n._("Hello") == "Hello"
    i18n._._fn = original
