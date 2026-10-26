"""OAuth device-code / refresh flow tests (no network)."""

from __future__ import annotations

import pytest

from onedrive_sync.common import oauth
from onedrive_sync.common.errors import AuthError, NotSignedInError
from onedrive_sync.common.http import HttpError, HttpResponse
from onedrive_sync.common.oauth import (
    DeviceCode,
    TokenSet,
    _token_from_response,
    poll_device_code,
    refresh_access_token,
    start_device_code,
)


class QueueTransport:
    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def device_code(**overrides) -> DeviceCode:
    values = {
        "device_code": "device",
        "user_code": "ABCD",
        "verification_uri": "https://microsoft.test/device",
        "expires_in": 10,
        "interval": 1,
    }
    values.update(overrides)
    return DeviceCode(**values)


def token_response(**overrides) -> HttpResponse:
    import json

    payload = {"access_token": "access", "refresh_token": "refresh", "expires_in": 3600}
    payload.update(overrides)
    return HttpResponse(200, json.dumps(payload).encode())


def test_start_device_code_success():
    transport = QueueTransport(
        HttpResponse(
            200,
            b'{"device_code":"d","user_code":"U","verification_uri":"https://x","expires_in":60}',
        )
    )
    code = start_device_code("client", transport=transport)
    assert code.device_code == "d"
    assert code.user_code == "U"
    assert code.verification_uri == "https://x"
    from urllib.parse import parse_qs

    assert transport.calls[0]["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert parse_qs(transport.calls[0]["data"].decode())["client_id"] == ["client"]


def test_start_device_code_requires_client_id():
    with pytest.raises(AuthError):
        start_device_code(" ")


def test_start_device_code_rejects_incomplete_response():
    transport = QueueTransport(HttpResponse(200, b'{"device_code":"d"}'))
    with pytest.raises(AuthError):
        start_device_code("client", transport=transport)


def test_start_device_code_maps_http_error():
    transport = QueueTransport(
        HttpError(400, '{"error":"invalid_client","error_description":"bad app"}')
    )
    with pytest.raises(AuthError, match="bad app"):
        start_device_code("client", transport=transport)


def test_token_from_response_requires_access_token():
    with pytest.raises(AuthError):
        _token_from_response({})


def test_token_set_expiry():
    import time

    old = TokenSet(access_token="a", expires_in=3600, obtained_at=0)
    assert old.is_expired()
    fresh = TokenSet(access_token="a", expires_in=3600, obtained_at=time.time())
    assert not fresh.is_expired()


def test_poll_device_code_pending_then_success():
    transport = QueueTransport(
        HttpError(400, '{"error":"authorization_pending"}'),
        token_response(),
    )
    sleeps = []
    token = poll_device_code(
        "client",
        device_code(),
        transport=transport,
        sleep=sleeps.append,
        clock=lambda: 0,
    )
    assert token.access_token == "access"
    assert sleeps == [1]


def test_poll_device_code_slow_down_increases_interval():
    transport = QueueTransport(
        HttpError(400, '{"error":"slow_down"}'),
        token_response(),
    )
    sleeps = []
    token = poll_device_code(
        "client",
        device_code(interval=2),
        transport=transport,
        sleep=sleeps.append,
        clock=lambda: 0,
    )
    assert token.access_token == "access"
    assert sleeps == [7]


def test_poll_device_code_expired_token():
    transport = QueueTransport(HttpError(400, '{"error":"expired_token"}'))
    with pytest.raises(AuthError, match="expired"):
        poll_device_code("client", device_code(), transport=transport, clock=lambda: 0)


def test_poll_device_code_declined():
    transport = QueueTransport(
        HttpError(400, '{"error":"authorization_declined","error_description":"no"}')
    )
    with pytest.raises(AuthError, match="no"):
        poll_device_code("client", device_code(), transport=transport, clock=lambda: 0)


def test_poll_device_code_unknown_error():
    transport = QueueTransport(HttpError(500, '{"error":"server_error"}'))
    with pytest.raises(AuthError):
        poll_device_code("client", device_code(), transport=transport, clock=lambda: 0)


def test_poll_device_code_honours_deadline():
    transport = QueueTransport()
    ticks = iter([0, 5])
    with pytest.raises(AuthError):
        poll_device_code(
            "client",
            device_code(expires_in=1),
            transport=transport,
            sleep=lambda _seconds: None,
            clock=lambda: next(ticks, 5),
        )


def test_poll_device_code_aborts():
    with pytest.raises(AuthError, match="cancelled"):
        poll_device_code(
            "client",
            device_code(),
            transport=QueueTransport(),
            should_abort=lambda: True,
            clock=lambda: 0,
        )


def test_refresh_access_token_success():
    transport = QueueTransport(token_response())
    token = refresh_access_token("client", "refresh", transport=transport)
    assert token.access_token == "access"
    from urllib.parse import parse_qs

    assert parse_qs(transport.calls[0]["data"].decode())["grant_type"] == ["refresh_token"]


def test_refresh_access_token_keeps_old_refresh_token():
    transport = QueueTransport(HttpResponse(200, b'{"access_token":"new","expires_in":3600}'))
    token = refresh_access_token("client", "old-refresh", transport=transport)
    assert token.refresh_token == "old-refresh"


def test_refresh_access_token_validation():
    with pytest.raises(AuthError):
        refresh_access_token("", "refresh")
    with pytest.raises(NotSignedInError):
        refresh_access_token("client", " ")


def test_refresh_access_token_invalid_grant():
    transport = QueueTransport(HttpError(400, '{"error":"invalid_grant"}'))
    with pytest.raises(NotSignedInError):
        refresh_access_token("client", "refresh", transport=transport)


def test_refresh_access_token_other_error():
    transport = QueueTransport(HttpError(400, '{"error":"bad","error_description":"broken"}'))
    with pytest.raises(AuthError, match="broken"):
        refresh_access_token("client", "refresh", transport=transport)


def test_default_app_uses_consumers_tenant():
    assert oauth.tenant_for(oauth.DEFAULT_CLIENT_ID) == "consumers"
    assert oauth.tenant_for("custom-client") == "common"
    transport = QueueTransport(
        HttpResponse(
            200,
            b'{"device_code":"d","user_code":"U","verification_uri":"https://x"}',
        )
    )
    start_device_code(oauth.DEFAULT_CLIENT_ID, transport=transport)
    assert "/consumers/oauth2/v2.0/devicecode" in transport.calls[0]["url"]

    transport = QueueTransport(token_response())
    poll_device_code(
        oauth.DEFAULT_CLIENT_ID,
        device_code(),
        transport=transport,
        clock=lambda: 0,
    )
    assert "/consumers/oauth2/v2.0/token" in transport.calls[0]["url"]


def test_constants():
    assert "Files.ReadWrite.All" in oauth.SCOPES
    assert oauth.TENANT == "common"
