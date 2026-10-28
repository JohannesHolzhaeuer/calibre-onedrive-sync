"""Microsoft identity platform OAuth 2.0 device-code flow.

A calibre plugin runs on the user's desktop, so the **device code** flow is the
best fit: no redirect URI, no local web server, and it works for personal and
work/school accounts. The user opens the verification URL, enters the short
code, and the plugin polls for the resulting tokens.

Only the standard library is used (``urllib`` via :mod:`common.http`).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlencode

from .defaults import DEFAULT_CLIENT_ID
from .errors import AuthError, NotSignedInError
from .http import HttpError, Transport, request_json

TENANT = "common"
PERSONAL_TENANT = "consumers"


def tenant_for(client_id: str) -> str:
    """Return the authority that matches the app registration."""
    if client_id.strip().casefold() == DEFAULT_CLIENT_ID.casefold():
        return PERSONAL_TENANT
    return TENANT


def _auth_base(client_id: str) -> str:
    return f"https://login.microsoftonline.com/{tenant_for(client_id)}/oauth2/v2.0"


#: ``Files.ReadWrite.All`` is required to write into the selected OneDrive
#: folder. ``offline_access`` gives us the refresh token.
SCOPES = "offline_access Files.ReadWrite.All User.Read"


@dataclass(frozen=True)
class DeviceCode:
    """Details shown to the user while signing in."""

    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int = 900
    interval: int = 5
    message: str = ""


@dataclass
class TokenSet:
    """Access + refresh tokens returned by the identity platform."""

    access_token: str
    refresh_token: str = ""
    expires_in: int = 3600
    token_type: str = "Bearer"
    obtained_at: float = field(default_factory=time.time)

    def is_expired(self, *, skew: int = 120) -> bool:
        """True when the access token is within ``skew`` seconds of expiry."""
        return time.time() >= self.obtained_at + max(0, self.expires_in - skew)


def _error_from(exc: HttpError) -> tuple[str, str]:
    payload = exc.json_body or {}
    return str(payload.get("error", "")), str(payload.get("error_description", ""))


#: Sign-in runs in a dialog the user is waiting on, so a hung socket must be
#: given up quickly and picked up by the retry loop instead of blocking for the
#: general 30 s default.
AUTH_TIMEOUT = 12


def _post_form(url: str, fields: dict, transport: Transport | None) -> dict:
    return request_json(
        "POST",
        url,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=urlencode(fields).encode("utf-8"),
        timeout=AUTH_TIMEOUT,
        transport=transport,
    )


#: A reset/aborted socket has no HTTP status; retry it a few times before
#: giving up, because Microsoft's sign-in endpoints occasionally drop a
#: connection (WinError 10054 / similar) during the device-code flow.
TRANSPORT_RETRY_LIMIT = 5
DEVICE_CODE_ATTEMPTS = 3


def _is_transport_error(exc: HttpError) -> bool:
    return exc.status == 0


def start_device_code(
    client_id: str,
    *,
    scopes: str = SCOPES,
    transport: Transport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> DeviceCode:
    """Start the device-code flow and return the code the user must enter."""
    if not client_id.strip():
        raise AuthError("No Microsoft app (client) ID is configured.")
    data: dict = {}
    for attempt in range(DEVICE_CODE_ATTEMPTS):
        try:
            data = _post_form(
                f"{_auth_base(client_id)}/devicecode",
                {"client_id": client_id, "scope": scopes},
                transport,
            )
            break
        except HttpError as exc:
            if _is_transport_error(exc) and attempt + 1 < DEVICE_CODE_ATTEMPTS:
                sleep(1.0 + attempt)
                continue
            error, description = _error_from(exc)
            if _is_transport_error(exc):
                raise AuthError(f"Could not reach the Microsoft sign-in service: {exc}") from exc
            raise AuthError(description or error or str(exc)) from exc

    device_code = str(data.get("device_code", ""))
    user_code = str(data.get("user_code", ""))
    verification_uri = str(data.get("verification_uri") or data.get("verification_url") or "")
    if not device_code or not user_code or not verification_uri:
        raise AuthError("The identity platform returned an incomplete device-code response.")
    return DeviceCode(
        device_code=device_code,
        user_code=user_code,
        verification_uri=verification_uri,
        expires_in=int(data.get("expires_in", 900) or 900),
        interval=int(data.get("interval", 5) or 5),
        message=str(data.get("message", "")),
    )


def _token_from_response(data: dict) -> TokenSet:
    access_token = str(data.get("access_token", ""))
    if not access_token:
        raise AuthError("The identity platform did not return an access token.")
    return TokenSet(
        access_token=access_token,
        refresh_token=str(data.get("refresh_token", "")),
        expires_in=int(data.get("expires_in", 3600) or 3600),
        token_type=str(data.get("token_type", "Bearer")),
    )


def poll_device_code(
    client_id: str,
    device_code: DeviceCode,
    *,
    transport: Transport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    should_abort: Callable[[], bool] | None = None,
) -> TokenSet:
    """Poll the token endpoint until the user finishes signing in.

    Handles Microsoft's ``authorization_pending`` / ``slow_down`` responses,
    honours the device-code deadline, and stops early when ``should_abort()``
    returns True (the dialog was closed).
    """
    deadline = clock() + max(1, device_code.expires_in)
    wait = max(1, device_code.interval)
    transport_failures = 0
    while clock() < deadline:
        if should_abort is not None and should_abort():
            raise AuthError("Sign-in cancelled.")
        try:
            data = _post_form(
                f"{_auth_base(client_id)}/token",
                {
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": client_id,
                    "device_code": device_code.device_code,
                },
                transport,
            )
            return _token_from_response(data)
        except HttpError as exc:
            error, description = _error_from(exc)
            if _is_transport_error(exc):
                transport_failures += 1
                if transport_failures >= TRANSPORT_RETRY_LIMIT:
                    raise AuthError(
                        f"Could not reach the Microsoft sign-in service: {exc}"
                    ) from exc
            elif error == "authorization_pending":
                transport_failures = 0
            elif error == "slow_down":
                transport_failures = 0
                wait += 5
            elif error == "expired_token":
                raise AuthError("The sign-in code expired. Start sign-in again.") from exc
            elif error in {"authorization_declined", "bad_verification_code"}:
                raise AuthError(description or "Sign-in was declined.") from exc
            else:
                raise AuthError(description or error or str(exc)) from exc
        sleep(wait)
    raise AuthError("The sign-in code expired. Start sign-in again.")


def refresh_access_token(
    client_id: str,
    refresh_token: str,
    *,
    transport: Transport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> TokenSet:
    """Exchange a stored refresh token for a fresh access token."""
    if not client_id.strip():
        raise AuthError("No Microsoft app (client) ID is configured.")
    if not refresh_token.strip():
        raise NotSignedInError("Sign in to OneDrive first.")
    data: dict = {}
    for attempt in range(DEVICE_CODE_ATTEMPTS):
        try:
            data = _post_form(
                f"{_auth_base(client_id)}/token",
                {
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "refresh_token": refresh_token,
                    "scope": SCOPES,
                },
                transport,
            )
            break
        except HttpError as exc:
            if _is_transport_error(exc) and attempt + 1 < DEVICE_CODE_ATTEMPTS:
                sleep(1.0 + attempt)
                continue
            error, description = _error_from(exc)
            if _is_transport_error(exc):
                raise AuthError(f"Could not reach the Microsoft sign-in service: {exc}") from exc
            if error == "invalid_grant":
                raise NotSignedInError(
                    "The saved OneDrive session expired. Sign in again."
                ) from exc
            raise AuthError(description or error or str(exc)) from exc
    token = _token_from_response(data)
    # Some responses omit the refresh token; keep the old one in that case.
    if not token.refresh_token:
        token.refresh_token = refresh_token
    return token
