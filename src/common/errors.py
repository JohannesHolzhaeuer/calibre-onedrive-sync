"""Typed errors for the OneDrive sync.

Keeping these separate from the transport/orchestration modules lets the GUI map
them to actionable messages without importing any calibre or Qt code.
"""

from __future__ import annotations


class OneDriveError(Exception):
    """Base class for every expected plugin error."""


class NotConfiguredError(OneDriveError):
    """The plugin has no Microsoft app ID / target folder yet."""


class NotSignedInError(OneDriveError):
    """No usable refresh token; the user must sign in."""


class AuthError(OneDriveError):
    """The OAuth device-code or refresh flow failed."""


class GraphError(OneDriveError):
    """Microsoft Graph returned a non-success response."""

    def __init__(self, status: int, message: str, body: str = "") -> None:
        self.status = status
        self.body = body
        super().__init__(message or f"Microsoft Graph error (HTTP {status})")


class SyncError(OneDriveError):
    """The sync engine could not complete a step."""
