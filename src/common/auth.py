"""Session management on top of the OAuth device-code flow.

``AuthManager`` keeps the short-lived access token in memory and persists only
the refresh token through a :class:`TokenStore` (the calibre keyring when
available). It is the single entry point used by the Graph client and the GUI.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Protocol

from .errors import NotSignedInError
from .http import Transport
from .oauth import (
    DeviceCode,
    TokenSet,
    poll_device_code,
    refresh_access_token,
    start_device_code,
)


class TokenStore(Protocol):
    """Persistence for the refresh token (never the short-lived access token)."""

    def get(self) -> str: ...

    def set(self, token: str) -> None: ...

    def clear(self) -> None: ...


class AuthManager:
    """Owns the OAuth session and refreshes access tokens on demand."""

    def __init__(
        self,
        client_id: str,
        token_store: TokenStore,
        *,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client_id = client_id.strip()
        self.token_store = token_store
        self.transport = transport
        self._sleep = sleep
        self._clock = clock
        self._token: TokenSet | None = None
        #: Guards the refresh path; Graph requests run in a worker pool.
        self._lock = threading.Lock()

    def has_refresh_token(self) -> bool:
        """True when a refresh token is available (a session can be restored)."""
        return bool(self.token_store.get().strip())

    def get_access_token(self, *, force: bool = False) -> str:
        """Return a valid access token, refreshing it when needed.

        Parallel uploads share this manager, and Microsoft rotates refresh
        tokens: two refreshes at once would burn the token the first one just
        stored. The lock serialises the refresh, and a thread that had to wait
        takes the token its predecessor fetched instead of refreshing again.
        """
        token = self._token
        if not force and token is not None and not token.is_expired():
            return token.access_token
        with self._lock:
            token = self._token
            if not force and token is not None and not token.is_expired():
                return token.access_token
            refresh_token = self.token_store.get()
            if not refresh_token.strip():
                raise NotSignedInError("Sign in to OneDrive first.")
            token = refresh_access_token(
                self.client_id,
                refresh_token,
                transport=self.transport,
            )
            self._install(token)
            return token.access_token

    def start_device_code(self) -> DeviceCode:
        """Start the device-code flow (the GUI displays the returned code)."""
        return start_device_code(self.client_id, transport=self.transport)

    def complete_device_code(
        self,
        code: DeviceCode,
        *,
        should_abort: Callable[[], bool] | None = None,
    ) -> TokenSet:
        """Poll until the user completes sign-in, then persist the refresh token."""
        token = poll_device_code(
            self.client_id,
            code,
            transport=self.transport,
            sleep=self._sleep,
            clock=self._clock,
            should_abort=should_abort,
        )
        self._install(token)
        return token

    def sign_out(self) -> None:
        """Drop the in-memory access token and the persisted refresh token."""
        self._token = None
        self.token_store.clear()

    def invalidate(self) -> None:
        """Forget the cached access token (used after an unexpected 401)."""
        self._token = None

    def _install(self, token: TokenSet) -> None:
        self._token = token
        if token.refresh_token:
            self.token_store.set(token.refresh_token)
