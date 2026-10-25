"""Small standard-library HTTP helper with an injectable test seam.

The plugin must not depend on ``requests`` (calibre-only runtime deps), so all
network access goes through ``urllib``. Tests inject a fake ``transport`` /
``downloader`` instead of touching the network.
"""

from __future__ import annotations

import json
import shutil
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field

DEFAULT_TIMEOUT = 30

Transport = Callable[..., "HttpResponse"]
Downloader = Callable[..., None]


class HttpError(Exception):
    """A non-2xx HTTP response (or a transport failure)."""

    def __init__(self, status: int, body: bytes | str = b"", headers: dict | None = None) -> None:
        self.status = status
        self.body = body if isinstance(body, bytes) else body.encode("utf-8", "replace")
        self.headers = headers or {}
        try:
            self.json_body = json.loads(self.body.decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001 - non-JSON error bodies are normal
            self.json_body = {}
        text = self.body.decode("utf-8", "replace")[:500]
        super().__init__(f"HTTP {status}: {text}" if text else f"HTTP {status}")


@dataclass
class HttpResponse:
    status: int
    body: bytes = b""
    headers: dict = field(default_factory=dict)


def urllib_request(
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    data: bytes | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> HttpResponse:
    """Perform a request with ``urllib`` and return the buffered response."""
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return HttpResponse(
                status=getattr(response, "status", 200),
                body=response.read(),
                headers=dict(response.headers),
            )
    except urllib.error.HTTPError as exc:
        raise HttpError(exc.code, exc.read(), dict(exc.headers or {})) from exc
    except urllib.error.URLError as exc:  # pragma: no cover - network failure
        raise HttpError(0, str(exc.reason)) from exc


def urllib_download(
    url: str,
    dest_path: str,
    *,
    headers: dict | None = None,
    timeout: int = 120,
) -> None:
    """Stream ``url`` into ``dest_path`` without buffering the whole file."""
    request = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            with open(dest_path, "wb") as handle:
                shutil.copyfileobj(response, handle)
    except urllib.error.HTTPError as exc:
        raise HttpError(exc.code, exc.read(), dict(exc.headers or {})) from exc
    except urllib.error.URLError as exc:  # pragma: no cover - network failure
        raise HttpError(0, str(exc.reason)) from exc


def request_json(
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    data: dict | bytes | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    transport: Transport | None = None,
) -> dict:
    """Perform a JSON request and return the decoded response object."""
    request_headers = {"Accept": "application/json", **(headers or {})}
    payload: bytes | None
    if isinstance(data, dict):
        payload = json.dumps(data).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    else:
        payload = data
    response = (transport or urllib_request)(
        method,
        url,
        headers=request_headers,
        data=payload,
        timeout=timeout,
    )
    if not 200 <= response.status < 300:
        raise HttpError(response.status, response.body, response.headers)
    if not response.body:
        return {}
    try:
        return json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HttpError(response.status, response.body, response.headers) from exc


def download_file(
    url: str,
    dest_path: str,
    *,
    headers: dict | None = None,
    timeout: int = 120,
    downloader: Downloader | None = None,
) -> None:
    """Download ``url`` to ``dest_path`` using an injectable downloader."""
    (downloader or urllib_download)(url, dest_path, headers=headers, timeout=timeout)
