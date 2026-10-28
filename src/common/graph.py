"""Microsoft Graph client for OneDrive (standard library only).

Supports the pieces the sync needs:

* fetch the signed-in user's drive and list folder children,
* streamed downloads,
* simple uploads and chunked upload sessions for large books.

Authentication is delegated to :class:`common.auth.AuthManager`; this module
never touches tokens except to put them in the ``Authorization`` header.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from urllib.parse import quote

from .auth import AuthManager
from .errors import GraphError
from .http import Downloader, HttpError, Transport, download_file, request_json
from .models import DriveInfo, RemoteItem

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
SIMPLE_UPLOAD_LIMIT = 4 * 1024 * 1024
UPLOAD_CHUNK_SIZE = 10 * 1024 * 1024


def _quote(value: str) -> str:
    return quote(str(value), safe="")


def _item_url(drive_id: str, item_id: str) -> str:
    return f"{GRAPH_BASE}/drives/{_quote(drive_id)}/items/{_quote(item_id)}"


def _children_url(drive_id: str, item_id: str | None) -> str:
    if item_id:
        return f"{_item_url(drive_id, item_id)}/children"
    return f"{GRAPH_BASE}/drives/{_quote(drive_id)}/root/children"


def drive_from_json(data: dict) -> DriveInfo:
    """Build a :class:`DriveInfo` from a Graph drive payload."""
    owner = ""
    owner_data = data.get("owner") or {}
    user = owner_data.get("user") or {}
    if user:
        owner = str(user.get("displayName") or user.get("email") or "")
    return DriveInfo(
        id=str(data.get("id", "")),
        name=str(data.get("name", "")),
        drive_type=str(data.get("driveType", "")),
        owner=owner,
    )


def remote_from_json(data: dict, drive_id: str) -> RemoteItem:
    """Build a :class:`RemoteItem`, resolving shortcuts (``remoteItem``)."""
    remote = data.get("remoteItem") or {}
    source = remote or data
    parent = source.get("parentReference") or data.get("parentReference") or {}
    resolved_drive = str(parent.get("driveId") or drive_id)
    is_folder = "folder" in source
    file_data = source.get("file") or {}
    return RemoteItem(
        id=str(source.get("id") or data.get("id") or ""),
        name=str(data.get("name") or source.get("name") or ""),
        drive_id=resolved_drive,
        parent_id=str(parent.get("id", "")),
        path=str(parent.get("path", "")),
        size=int(source.get("size") or data.get("size") or 0),
        e_tag=str(source.get("eTag") or data.get("eTag") or ""),
        c_tag=str(source.get("cTag") or data.get("cTag") or ""),
        last_modified=str(source.get("lastModifiedDateTime", "")),
        is_folder=is_folder,
        deleted=data.get("deleted") is not None,
        mime_type=str(file_data.get("mimeType", "")),
        web_url=str(source.get("webUrl") or data.get("webUrl") or ""),
    )


class GraphClient:
    """Thin, testable wrapper around the Microsoft Graph REST API."""

    def __init__(
        self,
        auth: AuthManager,
        *,
        transport: Transport | None = None,
        downloader: Downloader | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.auth = auth
        self._transport = transport
        self._downloader = downloader
        self._sleep = sleep

    # -- low-level requests -------------------------------------------------

    def _headers(self, extra: dict | None = None) -> dict:
        headers = {
            "Authorization": f"Bearer {self.auth.get_access_token()}",
            "Accept": "application/json",
        }
        headers.update(extra or {})
        return headers

    def _url(self, path_or_url: str) -> str:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            return path_or_url
        return f"{GRAPH_BASE}{path_or_url}"

    @staticmethod
    def _error(exc: HttpError) -> GraphError:
        payload = exc.json_body or {}
        error = payload.get("error") or {}
        if isinstance(error, dict):
            message = str(error.get("message") or error.get("code") or "")
        else:
            message = str(error)
        return GraphError(exc.status, message or str(exc), exc.body.decode("utf-8", "replace"))

    def _request(
        self,
        method: str,
        path_or_url: str,
        *,
        data: dict | bytes | None = None,
        headers: dict | None = None,
        retry_auth: bool = True,
    ) -> dict:
        try:
            return request_json(
                method,
                self._url(path_or_url),
                headers=self._headers(headers),
                data=data,
                transport=self._transport,
            )
        except HttpError as exc:
            if exc.status == 401 and retry_auth:
                self.auth.invalidate()
                self.auth.get_access_token(force=True)
                return self._request(
                    method,
                    path_or_url,
                    data=data,
                    headers=headers,
                    retry_auth=False,
                )
            raise self._error(exc) from exc

    def get(self, path_or_url: str) -> dict:
        return self._request("GET", path_or_url)

    def post(self, path_or_url: str, data: dict | None = None) -> dict:
        return self._request("POST", path_or_url, data=data or {})

    def put_bytes(self, path_or_url: str, data: bytes, *, content_type: str | None = None) -> dict:
        headers = {"Content-Type": content_type} if content_type else {}
        return self._request("PUT", path_or_url, data=data, headers=headers)

    def _paged(self, path_or_url: str) -> list[dict]:
        values: list[dict] = []
        url = path_or_url
        while url:
            data = self.get(url)
            values.extend(data.get("value") or [])
            url = str(data.get("@odata.nextLink") or "")
        return values

    # -- drives / folders ---------------------------------------------------

    def get_me(self) -> dict:
        """Return the signed-in user's Graph profile."""
        return self.get("/me")

    def list_drives(self) -> list[DriveInfo]:
        """Return the signed-in user's own OneDrive only."""
        try:
            drive = drive_from_json(self.get("/me/drive"))
        except GraphError:
            return []
        return [drive] if drive.id else []

    def list_children(self, drive_id: str, item_id: str | None = None) -> list[RemoteItem]:
        """List direct children of a folder (``None`` means the drive root)."""
        entries = self._paged(_children_url(drive_id, item_id))
        children = [remote_from_json(entry, drive_id) for entry in entries]
        return [child for child in children if child.drive_id == drive_id]

    def get_item(self, drive_id: str, item_id: str) -> RemoteItem:
        """Fetch one drive item by id."""
        return remote_from_json(self.get(_item_url(drive_id, item_id)), drive_id)

    def get_root_item(self, drive_id: str) -> RemoteItem:
        """Return the drive's root folder item (it has a real item id)."""
        return remote_from_json(
            self.get(f"{GRAPH_BASE}/drives/{_quote(drive_id)}/root"),
            drive_id,
        )

    # -- download / upload --------------------------------------------------

    def download(self, drive_id: str, item_id: str, dest_path: str) -> None:
        """Download an item's content to ``dest_path``."""
        url = f"{_item_url(drive_id, item_id)}/content"

        def download_once() -> None:
            download_file(
                url,
                dest_path,
                headers=self._headers(),
                downloader=self._downloader,
            )

        try:
            download_once()
        except HttpError as exc:
            if exc.status != 401:
                raise self._error(exc) from exc
            self.auth.invalidate()
            self.auth.get_access_token(force=True)
            try:
                download_once()
            except HttpError as retry_exc:
                raise self._error(retry_exc) from retry_exc

    def delete_item(self, drive_id: str, item_id: str) -> None:
        """Move an item to the OneDrive recycle bin."""
        self._request("DELETE", _item_url(drive_id, item_id))

    def create_upload_session(
        self,
        drive_id: str,
        parent_id: str,
        name: str,
        *,
        item_id: str | None = None,
        conflict: str = "rename",
    ) -> dict:
        """Create a resumable upload session for a new or existing file."""
        if item_id:
            url = f"{_item_url(drive_id, item_id)}/createUploadSession"
            body: dict = {}
        else:
            url = (
                f"{_children_url(drive_id, parent_id)[: -len('/children')]}"
                f":/{_quote(name)}:/createUploadSession"
            )
            body = {"item": {"@microsoft.graph.conflictBehavior": conflict}}
        return self.post(url, body)

    def upload_file(
        self,
        drive_id: str,
        parent_id: str,
        name: str,
        path: str,
        *,
        item_id: str | None = None,
        conflict: str = "rename",
    ) -> RemoteItem:
        """Upload ``path`` as ``name`` (simple PUT or chunked session)."""
        size = os.path.getsize(path)
        if size <= SIMPLE_UPLOAD_LIMIT:
            if item_id:
                url = f"{_item_url(drive_id, item_id)}/content"
            else:
                url = (
                    f"{_children_url(drive_id, parent_id)[: -len('/children')]}"
                    f":/{_quote(name)}:/content?@microsoft.graph.conflictBehavior={conflict}"
                )
            with open(path, "rb") as handle:
                data = handle.read()
            return remote_from_json(
                self.put_bytes(url, data, content_type="application/octet-stream"),
                drive_id,
            )

        session = self.create_upload_session(
            drive_id, parent_id, name, item_id=item_id, conflict=conflict
        )
        upload_url = str(session.get("uploadUrl", ""))
        if not upload_url:
            raise GraphError(0, "Microsoft Graph did not return an upload URL.")
        return self._upload_chunks(drive_id, parent_id, name, upload_url, path, size)

    def _upload_chunks(
        self,
        drive_id: str,
        parent_id: str,
        name: str,
        upload_url: str,
        path: str,
        size: int,
    ) -> RemoteItem:
        offset = 0
        result: dict = {}
        with open(path, "rb") as handle:
            while offset < size:
                handle.seek(offset)
                chunk = handle.read(UPLOAD_CHUNK_SIZE)
                end = offset + len(chunk) - 1
                try:
                    result = request_json(
                        "PUT",
                        upload_url,
                        data=chunk,
                        headers={
                            "Content-Type": "application/octet-stream",
                            "Content-Range": f"bytes {offset}-{end}/{size}",
                        },
                        transport=self._transport,
                    )
                except HttpError as exc:
                    raise self._error(exc) from exc
                offset = end + 1
                ranges = result.get("nextExpectedRanges") or []
                if ranges:
                    try:
                        offset = int(str(ranges[0]).split("-", 1)[0])
                    except (TypeError, ValueError):
                        pass
        if result.get("id"):
            return remote_from_json(result, drive_id)
        for child in self.list_children(drive_id, parent_id):
            if child.name == name:
                return child
        raise GraphError(0, "Upload finished but the resulting file could not be located.")
