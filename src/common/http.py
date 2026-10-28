"""Small standard-library HTTP helper with an injectable test seam.

The plugin must not depend on ``requests`` (calibre-only runtime deps), so all
network access goes through ``urllib``. Tests inject a fake ``transport`` /
``downloader`` instead of touching the network.

OneDrive answers ``/content`` requests with a ``302`` to a Microsoft storage
host. Python 3.12+ drops the ``Authorization`` header when a redirect changes
host, and the storage URL for a personal OneDrive is not usable without it, so
the redirect handler below puts the bearer token back for the Microsoft hosts in
:data:`REDIRECT_TRUSTED_SUFFIXES` and leaves it off everywhere else.
"""

from __future__ import annotations

import json
import shutil
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

DEFAULT_TIMEOUT = 30

#: Hosts (and subdomains) a redirect may carry the bearer token to.
REDIRECT_TRUSTED_SUFFIXES = (
    ".microsoft.com",
    ".microsoftonline.com",
    ".live.com",
    ".live.net",
    ".1drv.com",
    ".sharepoint.com",
    ".sharepoint-df.com",
)

Transport = Callable[..., "HttpResponse"]
Downloader = Callable[..., None]


def is_trusted_redirect(url: str) -> bool:
    """True when ``url`` is a Microsoft host the bearer token may follow."""
    host = (urlsplit(str(url)).hostname or "").casefold()
    if not host:
        return False
    trusted = REDIRECT_TRUSTED_SUFFIXES
    return any(host.endswith(suffix) or host == suffix.lstrip(".") for suffix in trusted)


class _KeepMicrosoftAuth(urllib.request.HTTPRedirectHandler):
    """Follow redirects without losing the token for Microsoft storage hosts.

    Depending on the Python version, urllib strips, redacts (to ``"***"``) or keeps
    the ``Authorization`` header of a redirected request. Both ends are wrong for
    OneDrive: the storage host answers ``401 Unauthenticated`` without the real
    token, and a token sent to a foreign host is a leak. So the token is restored
    for the Microsoft allow-list and removed for every other host.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        redirect = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirect is None:
            return None
        token = req.get_header("Authorization") or req.unredirected_hdrs.get("Authorization")
        if not token:
            return redirect
        if is_trusted_redirect(newurl):
            if redirect.get_header("Authorization") != token:
                redirect.add_header("Authorization", token)
        else:
            redirect.headers.pop("Authorization", None)
            redirect.unredirected_hdrs.pop("Authorization", None)
        return redirect


_OPENER: urllib.request.OpenerDirector | None = None


def _opener() -> urllib.request.OpenerDirector:
    global _OPENER
    if _OPENER is None:
        _OPENER = urllib.request.build_opener(_KeepMicrosoftAuth())
    return _OPENER


def _open(request: urllib.request.Request, timeout: int):
    """Single place that talks to urllib, so tests can replace it."""
    return _opener().open(request, timeout=timeout)


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
        with _open(request, timeout) as response:
            return HttpResponse(
                status=getattr(response, "status", 200),
                body=response.read(),
                headers=dict(response.headers),
            )
    except urllib.error.HTTPError as exc:
        raise HttpError(exc.code, exc.read(), dict(exc.headers or {})) from exc
    except OSError as exc:  # pragma: no cover - network failure
        # urllib wraps most of these in URLError, but a stalled read surfaces as a
        # bare TimeoutError and a refused connection as ConnectionRefusedError.
        raise HttpError(0, str(exc) or type(exc).__name__) from exc


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
        with _open(request, timeout) as response:
            with open(dest_path, "wb") as handle:
                shutil.copyfileobj(response, handle)
    except urllib.error.HTTPError as exc:
        raise HttpError(exc.code, exc.read(), dict(exc.headers or {})) from exc
    except OSError as exc:  # pragma: no cover - network failure
        raise HttpError(0, str(exc) or type(exc).__name__) from exc


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
