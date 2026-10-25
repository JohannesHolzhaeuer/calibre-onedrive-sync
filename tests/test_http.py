"""Tests for the injectable HTTP helper."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from onedrive_sync.common import http
from onedrive_sync.common.http import HttpError, HttpResponse, download_file, request_json


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
        http.urllib.request,
        "urlopen",
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

    monkeypatch.setattr(http.urllib.request, "urlopen", raise_http_error)
    with pytest.raises(HttpError) as exc:
        http.urllib_request("GET", "https://example.test")
    assert exc.value.status == 403
    assert exc.value.body == b"denied"


def test_urllib_download_writes_file(monkeypatch, tmp_path):
    monkeypatch.setattr(
        http.urllib.request,
        "urlopen",
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

    monkeypatch.setattr(http.urllib.request, "urlopen", raise_http_error)
    with pytest.raises(HttpError) as exc:
        http.urllib_download("https://example.test/book", str(tmp_path / "x"))
    assert exc.value.status == 404
