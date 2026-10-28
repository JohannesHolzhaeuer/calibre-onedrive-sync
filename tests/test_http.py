"""Tests for the injectable HTTP helper."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from email.message import Message

import pytest

from onedrive_sync.common import http
from onedrive_sync.common.http import (
    HttpError,
    HttpResponse,
    download_file,
    is_trusted_redirect,
    request_json,
)


def _location(location: str) -> Message:
    headers = Message()
    headers["Location"] = location
    return headers


def _redirect(newurl: str, from_url: str = "https://graph.microsoft.com/v1.0/content"):
    request = urllib.request.Request(
        from_url, headers={"Authorization": "token", "Accept": "application/json"}
    )
    handler = http._KeepMicrosoftAuth()
    return handler.redirect_request(request, None, 302, "Found", _location(newurl), newurl)


class FakeTransport:
    def __init__(self, response=None, error=None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    def __call__(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if self.error is not None:
            raise self.error
        return self.response


class _Response:
    def __init__(self, body=b"", status=200, headers=None) -> None:
        self._body = io.BytesIO(body)
        self.status = status
        self.headers = headers or {}

    def read(self, size=-1):
        return self._body.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def test_http_error_parses_json_and_plain_text():
    json_error = HttpError(400, '{"error": {"code": "bad"}}')
    assert json_error.status == 400
    assert json_error.json_body["error"]["code"] == "bad"
    assert "HTTP 400" in str(json_error)

    text_error = HttpError(500, "boom")
    assert text_error.json_body == {}
    assert "boom" in str(text_error)
    assert str(HttpError(404)) == "HTTP 404"


def test_request_json_encodes_dict_and_sets_content_type():
    transport = FakeTransport(HttpResponse(200, b'{"ok": true}'))
    result = request_json("POST", "https://example.test", data={"a": 1}, transport=transport)
    assert result == {"ok": True}
    call = transport.calls[0]
    assert call["method"] == "POST"
    assert call["headers"]["Content-Type"] == "application/json"
    assert json.loads(call["data"]) == {"a": 1}


def test_request_json_bytes_and_empty_body():
    transport = FakeTransport(HttpResponse(204, b""))
    assert request_json("PUT", "https://example.test", data=b"raw", transport=transport) == {}
    assert transport.calls[0]["data"] == b"raw"
    assert "Content-Type" not in transport.calls[0]["headers"]


def test_request_json_rejects_non_2xx():
    transport = FakeTransport(HttpResponse(404, b"nope"))
    with pytest.raises(HttpError) as exc:
        request_json("GET", "https://example.test", transport=transport)
    assert exc.value.status == 404


def test_request_json_rejects_invalid_json():
    transport = FakeTransport(HttpResponse(200, b"not-json"))
    with pytest.raises(HttpError):
        request_json("GET", "https://example.test", transport=transport)


def test_download_file_uses_injected_downloader():
    calls = []

    def downloader(url, path, **kwargs):
        calls.append((url, path, kwargs))

    download_file("https://example.test/file", "dest", downloader=downloader)
    assert calls[0][0] == "https://example.test/file"
    assert calls[0][1] == "dest"


def test_urllib_request_reads_response(monkeypatch):
    monkeypatch.setattr(
        http,
        "_open",
        lambda _request, timeout: _Response(b"body", 201, {"X-Test": "1"}),
    )
    response = http.urllib_request("GET", "https://example.test")
    assert response.status == 201
    assert response.body == b"body"
    assert response.headers["X-Test"] == "1"


def test_urllib_request_maps_http_errors(monkeypatch):
    def raise_http_error(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            "https://example.test",
            403,
            "forbidden",
            {"X-Test": "1"},
            io.BytesIO(b"denied"),
        )

    monkeypatch.setattr(http, "_open", raise_http_error)
    with pytest.raises(HttpError) as exc:
        http.urllib_request("GET", "https://example.test")
    assert exc.value.status == 403
    assert exc.value.body == b"denied"


def test_urllib_download_writes_file(monkeypatch, tmp_path):
    monkeypatch.setattr(
        http,
        "_open",
        lambda _request, timeout: _Response(b"book-bytes"),
    )
    dest = tmp_path / "book.epub"
    http.urllib_download("https://example.test/book", str(dest))
    assert dest.read_bytes() == b"book-bytes"


def test_urllib_download_maps_http_errors(monkeypatch, tmp_path):
    def raise_http_error(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            "https://example.test",
            404,
            "missing",
            {},
            io.BytesIO(b"gone"),
        )

    monkeypatch.setattr(http, "_open", raise_http_error)
    with pytest.raises(HttpError) as exc:
        http.urllib_download("https://example.test/book", str(tmp_path / "x"))
    assert exc.value.status == 404


def test_a_bare_timeout_becomes_a_transport_error(monkeypatch):
    def raise_timeout(*_args, **_kwargs):
        raise TimeoutError("The read operation timed out")

    monkeypatch.setattr(http, "_open", raise_timeout)
    with pytest.raises(HttpError) as exc:
        http.urllib_request("GET", "https://example.test")

    assert exc.value.status == 0
    assert "read operation timed out" in str(exc.value)


def test_a_reset_connection_becomes_a_transport_error(monkeypatch, tmp_path):
    def raise_reset(*_args, **_kwargs):
        raise ConnectionResetError(10054, "Verbindung wurde zurueckgesetzt")

    monkeypatch.setattr(http, "_open", raise_reset)
    with pytest.raises(HttpError) as exc:
        http.urllib_download("https://example.test/book", str(tmp_path / "x"))

    assert exc.value.status == 0
    assert exc.value.body


# -- redirects to Microsoft storage ------------------------------------------


def test_a_redirect_to_microsoft_storage_keeps_the_token():
    redirect = _redirect("https://a1b2.files.1drv.com/y4mX/book.epub")
    assert redirect.get_header("Authorization") == "token"


def test_a_redirect_within_graph_keeps_the_token():
    redirect = _redirect("https://graph.microsoft.com/v1.0/drives/d/items/i/content")
    assert redirect.get_header("Authorization") == "token"


def test_a_redirect_to_a_foreign_host_drops_the_token():
    redirect = _redirect("https://evil.example.test/book.epub")
    assert redirect.get_header("Authorization") is None


def test_a_303_redirect_becomes_a_get():
    request = urllib.request.Request(
        "https://graph.microsoft.com/v1.0/x",
        data=b"payload",
        headers={"Authorization": "token"},
        method="POST",
    )
    redirect = http._KeepMicrosoftAuth().redirect_request(
        request,
        None,
        303,
        "See Other",
        _location("https://a.files.1drv.com/y"),
        "https://a.files.1drv.com/y",
    )
    assert redirect.get_method() == "GET"
    assert redirect.data is None
    assert redirect.get_header("Authorization") == "token"


def test_a_redirect_another_handler_must_handle_is_handed_back(monkeypatch):
    # ``HTTPRedirectHandler`` documents that ``redirect_request`` may return
    # ``None`` when the redirect is left to a different handler; the plugin's
    # handler must pass that through untouched instead of crashing on it.
    monkeypatch.setattr(
        urllib.request.HTTPRedirectHandler,
        "redirect_request",
        lambda _self, *_args, **_kwargs: None,
    )
    assert _redirect("https://a1b2.files.1drv.com/y4mX/book.epub") is None


def test_a_redirect_without_a_token_is_returned_unchanged():
    request = urllib.request.Request("https://graph.microsoft.com/v1.0/x")
    redirect = http._KeepMicrosoftAuth().redirect_request(
        request,
        None,
        302,
        "Found",
        _location("https://a1b2.files.1drv.com/y4mX/book.epub"),
        "https://a1b2.files.1drv.com/y4mX/book.epub",
    )
    assert redirect is not None
    assert redirect.get_header("Authorization") is None


def test_an_unredirected_token_is_restored_on_a_microsoft_redirect():
    # ``Request`` keeps unredirected headers (added by hand or by an opener) in
    # a separate dict that a redirect does not copy, so the token still has to
    # be put back for the storage host.
    request = urllib.request.Request("https://graph.microsoft.com/v1.0/content")
    request.add_unredirected_header("Authorization", "token")
    redirect = http._KeepMicrosoftAuth().redirect_request(
        request,
        None,
        302,
        "Found",
        _location("https://a1b2.files.1drv.com/y4mX/book.epub"),
        "https://a1b2.files.1drv.com/y4mX/book.epub",
    )
    assert redirect.get_header("Authorization") == "token"


def test_the_allow_list_covers_microsoft_hosts():
    assert is_trusted_redirect("https://graph.microsoft.com/v1.0/me")
    assert is_trusted_redirect("https://a1b2.files.1drv.com/x")
    assert is_trusted_redirect("https://tenant-my.sharepoint.com/x")
    assert is_trusted_redirect("https://login.microsoftonline.com/common/oauth2/v2.0/token")
    assert is_trusted_redirect("https://live.com/x")
    assert not is_trusted_redirect("https://notmicrosoft.com/x")
    assert not is_trusted_redirect("https://evil.example.test/x")
    assert not is_trusted_redirect("https://graph.microsoft.com.evil.test/x")
    assert not is_trusted_redirect("not a url")


def test_the_opener_is_built_once(monkeypatch):
    built = []
    monkeypatch.setattr(http, "_OPENER", None)
    monkeypatch.setattr(
        http.urllib.request,
        "build_opener",
        lambda handlers: built.append(handlers) or "opener",
    )

    assert http._opener() == "opener"
    assert http._opener() == "opener"

    assert len(built) == 1
    assert isinstance(built[0], http._KeepMicrosoftAuth)


def test_open_delegates_to_the_opener(monkeypatch):
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append((request, timeout))
            return "response"

    monkeypatch.setattr(http, "_opener", lambda: Opener())
    assert http._open("request", 30) == "response"
    assert calls == [("request", 30)]
