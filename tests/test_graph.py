"""Microsoft Graph client tests using an injected transport."""

from __future__ import annotations

import json
import os

import pytest

from onedrive_sync.common import graph
from onedrive_sync.common.errors import GraphError
from onedrive_sync.common.graph import (
    GraphClient,
    drive_from_json,
    remote_from_json,
)
from onedrive_sync.common.http import HttpError, HttpResponse


class FakeAuth:
    def __init__(self) -> None:
        self.invalidated = 0
        self.forced = 0

    def get_access_token(self, *, force=False):
        if force:
            self.forced += 1
        return "token"

    def invalidate(self):
        self.invalidated += 1


class Transport:
    def __init__(self, handler) -> None:
        self.handler = handler
        self.calls: list[dict] = []

    def __call__(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        result = self.handler(method, url, kwargs)
        if isinstance(result, Exception):
            raise result
        if isinstance(result, HttpResponse):
            return result
        return HttpResponse(200, json.dumps(result).encode())


def client(handler, **kwargs):
    auth = FakeAuth()
    return GraphClient(auth, transport=Transport(handler), **kwargs), auth


def test_drive_and_item_parsing():
    drive = drive_from_json(
        {
            "id": "d",
            "name": "OneDrive",
            "driveType": "personal",
            "owner": {"user": {"displayName": "A"}},
        }
    )
    assert drive.id == "d" and drive.owner == "A"

    shortcut = remote_from_json(
        {
            "id": "shortcut",
            "name": "Shared folder",
            "remoteItem": {
                "id": "remote",
                "name": "Remote name",
                "folder": {},
                "parentReference": {"driveId": "other", "id": "parent", "path": "/drive/root:"},
            },
        },
        "drive",
    )
    assert shortcut.id == "remote"
    assert shortcut.drive_id == "other"
    assert shortcut.is_folder
    assert shortcut.name == "Shared folder"

    deleted = remote_from_json({"id": "x", "name": "x.epub", "deleted": {}}, "d")
    assert deleted.deleted
    assert deleted.extension == "epub"
    folder = remote_from_json({"id": "f", "name": "folder", "folder": {}}, "d")
    assert folder.extension == ""

    linked = remote_from_json(
        {"id": "y", "name": "y.epub", "file": {}, "webUrl": "https://onedrive.test/y"},
        "d",
    )
    assert linked.web_url == "https://onedrive.test/y"
    shortcut_web = remote_from_json(
        {
            "id": "s",
            "name": "shortcut",
            "remoteItem": {
                "id": "z",
                "name": "z.epub",
                "file": {},
                "webUrl": "https://onedrive.test/z",
            },
        },
        "d",
    )
    assert shortcut_web.web_url == "https://onedrive.test/z"


def test_get_me_and_own_drive_only():
    urls = []

    def handler(method, url, _kwargs):
        urls.append(url)
        if url.endswith("/me/drive"):
            return {"id": "d1", "name": "One"}
        if url.endswith("/me"):
            return {"displayName": "Alice"}
        raise HttpError(404, b"missing")

    graph_client, _auth = client(handler)
    assert graph_client.get_me()["displayName"] == "Alice"
    drives = graph_client.list_drives()
    assert [drive.id for drive in drives] == ["d1"]
    assert not any(url.endswith("/me/drives") for url in urls)


def test_list_drives_survives_missing_endpoints():
    graph_client, _auth = client(lambda *_args: HttpError(404, b"missing"))
    assert graph_client.list_drives() == []


def test_list_children_follows_next_link():
    def handler(method, url, _kwargs):
        if "page=2" in url:
            return {"value": [{"id": "b", "name": "b.epub", "file": {}}]}
        return {
            "value": [{"id": "a", "name": "a.epub", "file": {}}],
            "@odata.nextLink": "https://graph.test/page=2",
        }

    graph_client, _auth = client(handler)
    children = graph_client.list_children("drive", "folder")
    assert [child.id for child in children] == ["a", "b"]


def test_list_children_skips_shortcuts_to_other_drives():
    def handler(method, url, _kwargs):
        return {
            "value": [
                {"id": "a", "name": "a.epub", "file": {}},
                {
                    "id": "shortcut",
                    "name": "Shared",
                    "remoteItem": {
                        "id": "remote",
                        "name": "Shared",
                        "folder": {},
                        "parentReference": {"driveId": "other"},
                    },
                },
            ]
        }

    graph_client, _auth = client(handler)
    children = graph_client.list_children("drive", "folder")
    assert [child.id for child in children] == ["a"]


def test_get_item_and_root():
    graph_client, _auth = client(
        lambda method, url, _kwargs: {
            "id": "root" if url.endswith("/root") else "item",
            "name": "Root" if url.endswith("/root") else "Item",
            "folder": {},
        }
    )
    assert graph_client.get_item("d", "i").id == "item"
    assert graph_client.get_root_item("d").id == "root"


def test_download_uses_downloader(tmp_path):
    calls = []
    auth = FakeAuth()
    graph_client = GraphClient(
        auth,
        transport=Transport(lambda *_args: {}),
        downloader=lambda url, path, **kwargs: calls.append((url, path, kwargs)),
    )
    graph_client.download("d", "i", str(tmp_path / "book.epub"))
    assert calls[0][0].endswith("/items/i/content")
    assert calls[0][2]["headers"]["Authorization"] == "Bearer token"


def test_download_retries_after_401(tmp_path):
    state = {"calls": 0}
    headers = []

    def downloader(_url, _path, **kwargs):
        state["calls"] += 1
        headers.append(kwargs["headers"])
        if state["calls"] == 1:
            raise HttpError(401, b"expired")

    auth = FakeAuth()
    graph_client = GraphClient(auth, downloader=downloader)
    graph_client.download("d", "i", str(tmp_path / "book.epub"))
    assert state["calls"] == 2
    assert auth.invalidated == 1
    assert auth.forced == 1
    assert headers[0]["Authorization"] == "Bearer token"


def test_delete_item_uses_item_endpoint():
    transport = Transport(lambda *_args: {})
    graph_client = GraphClient(FakeAuth(), transport=transport)
    graph_client.delete_item("drive", "item")
    assert transport.calls[0]["method"] == "DELETE"
    assert transport.calls[0]["url"].endswith("/drives/drive/items/item")


def test_upload_simple(tmp_path):
    path = tmp_path / "book.epub"
    path.write_bytes(b"data")
    graph_client, _auth = client(
        lambda method, url, _kwargs: {"id": "uploaded", "name": "book.epub", "file": {}}
    )
    item = graph_client.upload_file("d", "parent", "book.epub", str(path))
    assert item.id == "uploaded"


def test_upload_simple_replaces_existing_item(tmp_path):
    path = tmp_path / "book.epub"
    path.write_bytes(b"data")
    transport = Transport(lambda *_args: {"id": "uploaded", "name": "book.epub", "file": {}})
    graph_client = GraphClient(FakeAuth(), transport=transport)
    graph_client.upload_file("d", "parent", "book.epub", str(path), item_id="old")
    assert "/items/old/content" in transport.calls[0]["url"]


def test_upload_chunked(tmp_path, monkeypatch):
    path = tmp_path / "big.epub"
    path.write_bytes(b"0123456789")
    monkeypatch.setattr(graph, "SIMPLE_UPLOAD_LIMIT", 1)
    monkeypatch.setattr(graph, "UPLOAD_CHUNK_SIZE", 4)

    def handler(method, url, kwargs):
        if url.endswith("/createUploadSession"):
            return {"uploadUrl": "https://upload.test/session"}
        if url == "https://upload.test/session":
            return {"id": "uploaded", "name": "big.epub", "file": {}}
        return {"value": []}

    transport = Transport(handler)
    graph_client = GraphClient(FakeAuth(), transport=transport)
    item = graph_client.upload_file("d", "parent", "big.epub", str(path))
    assert item.id == "uploaded"
    ranges = [
        call["headers"]["Content-Range"]
        for call in transport.calls
        if "Content-Range" in call.get("headers", {})
    ]
    assert ranges == ["bytes 0-3/10", "bytes 4-7/10", "bytes 8-9/10"]


def test_upload_chunked_falls_back_to_lookup(tmp_path, monkeypatch):
    path = tmp_path / "big.epub"
    path.write_bytes(b"0123456789")
    monkeypatch.setattr(graph, "SIMPLE_UPLOAD_LIMIT", 1)
    monkeypatch.setattr(graph, "UPLOAD_CHUNK_SIZE", 10)

    def handler(method, url, _kwargs):
        if url.endswith("/createUploadSession"):
            return {"uploadUrl": "https://upload.test/session"}
        if url == "https://upload.test/session":
            return {"nextExpectedRanges": []}
        return {"value": [{"id": "found", "name": "big.epub", "file": {}}]}

    graph_client, _auth = client(handler)
    item = graph_client.upload_file("d", "parent", "big.epub", str(path))
    assert item.id == "found"


def test_upload_session_without_url_raises(tmp_path, monkeypatch):
    path = tmp_path / "big.epub"
    path.write_bytes(b"x")
    monkeypatch.setattr(graph, "SIMPLE_UPLOAD_LIMIT", 0)
    graph_client, _auth = client(lambda *_args: {})
    with pytest.raises(GraphError):
        graph_client.upload_file("d", "parent", "big.epub", str(path))


def test_401_invalidates_and_retries():
    state = {"calls": 0}

    def handler(method, url, _kwargs):
        state["calls"] += 1
        if state["calls"] == 1:
            return HttpError(401, b"expired")
        return {"displayName": "Alice"}

    graph_client, auth = client(handler)
    assert graph_client.get_me()["displayName"] == "Alice"
    assert auth.invalidated == 1
    assert auth.forced == 1


def test_graph_error_message_mapping():
    graph_client, _auth = client(
        lambda *_args: HttpError(400, b'{"error":{"code":"bad","message":"failed"}}')
    )
    with pytest.raises(GraphError, match="failed") as exc:
        graph_client.get_me()
    assert exc.value.status == 400

    graph_client, _auth = client(lambda *_args: HttpError(400, b'{"error":"string"}'))
    with pytest.raises(GraphError, match="string"):
        graph_client.get_me()


def test_create_upload_session_for_new_and_existing():
    transport = Transport(lambda *_args: {"uploadUrl": "https://upload.test"})
    graph_client = GraphClient(FakeAuth(), transport=transport)
    graph_client.create_upload_session("d", "parent", "book.epub")
    assert "/parent:/book.epub:/createUploadSession" in transport.calls[0]["url"]
    graph_client.create_upload_session("d", "parent", "book.epub", item_id="old")
    assert "/items/old/createUploadSession" in transport.calls[1]["url"]


def test_quote_path_is_safe():
    graph_client, _auth = client(lambda *_args: {"id": "x", "name": "x", "file": {}})
    graph_client.get_item("drive/with slash", "item/with slash")
    assert "%2F" in graph_client._transport.calls[0]["url"]


def test_drive_from_json_without_owner():
    drive = drive_from_json({"id": "d", "name": "n"})
    assert drive.owner == ""
    assert os.path.basename("a.epub") == "a.epub"


# -- transient network failures ---------------------------------------------


def test_a_stalled_read_is_retried():
    sleeps = []
    state = {"calls": 0}

    def handler(_method, _url, _kwargs):
        state["calls"] += 1
        if state["calls"] < 3:
            raise HttpError(0, "The read operation timed out")
        return {"id": "i", "name": "ebook", "folder": {}}

    graph_client, _auth = client(handler, sleep=sleeps.append)
    item = graph_client.get_item("d", "i")

    assert item.name == "ebook"
    assert state["calls"] == 3
    assert sleeps == [1.0, 2.0]


def test_retries_are_bounded_and_reported():
    sleeps = []

    def handler(_method, _url, _kwargs):
        raise HttpError(0, "connection reset")

    graph_client, _auth = client(handler, sleep=sleeps.append)
    with pytest.raises(GraphError) as caught:
        graph_client.get_item("d", "i")

    assert caught.value.status == 0
    assert sleeps == [1.0, 2.0]


def test_uploads_are_not_retried():
    state = {"calls": 0}

    def handler(_method, _url, _kwargs):
        state["calls"] += 1
        raise HttpError(0, "The read operation timed out")

    graph_client, _auth = client(handler, sleep=lambda _seconds: None)
    with pytest.raises(GraphError):
        graph_client.post("/me/drive", {})

    assert state["calls"] == 1


def test_reads_get_the_long_graph_timeout():
    graph_client, _auth = client(lambda *_args: {"value": []})
    graph_client.list_children("d", "i")
    assert graph_client._transport.calls[0]["timeout"] == graph.GRAPH_TIMEOUT


def test_download_retries_a_stalled_stream(tmp_path):
    sleeps = []
    state = {"calls": 0}

    def downloader(_url, _path, **_kwargs):
        state["calls"] += 1
        if state["calls"] < 3:
            raise HttpError(0, "The read operation timed out")

    graph_client = GraphClient(
        FakeAuth(),
        downloader=downloader,
        sleep=sleeps.append,
    )
    graph_client.download("d", "i", str(tmp_path / "book.epub"))

    assert state["calls"] == 3
    assert sleeps == [1.0, 2.0]


def test_get_item_by_path_addresses_the_root(tmp_path):
    graph_client, _auth = client(lambda *_args: {"id": "i", "name": "index.json", "file": {}})
    graph_client.get_item_by_path("d", "/data/ebook/calibre-onedrive-index.json")
    assert graph_client._transport.calls[0]["url"].endswith(
        "/drives/d/root:/data/ebook/calibre-onedrive-index.json:"
    )


def test_a_path_without_a_leading_slash_becomes_absolute():
    graph_client, _auth = client(lambda *_args: {"id": "i", "name": "index.json", "file": {}})
    graph_client.get_item_by_path("d", "Books/index.json")
    assert "/root:/Books/index.json:" in graph_client._transport.calls[0]["url"]


def test_a_path_with_spaces_is_percent_encoded():
    graph_client, _auth = client(lambda *_args: {"id": "i", "name": "book.epub", "file": {}})
    graph_client.get_item_by_path("d", "/data/ebook/A Author - Buch (German Edition).epub")
    url = graph_client._transport.calls[0]["url"]

    assert " " not in url
    assert "%20" in url
    assert "/root:/data/ebook/" in url
    assert url.endswith(".epub:")


def test_download_reports_a_server_error(tmp_path):
    def downloader(_url, _path, **_kwargs):
        raise HttpError(500, b"server exploded")

    graph_client = GraphClient(FakeAuth(), downloader=downloader)
    with pytest.raises(GraphError) as caught:
        graph_client.download("d", "i", str(tmp_path / "book.epub"))

    assert caught.value.status == 500


def test_download_path_streams_the_root_content(tmp_path):
    calls = []
    graph_client = GraphClient(
        FakeAuth(),
        transport=Transport(lambda *_args: {}),
        downloader=lambda url, path, **kwargs: calls.append((url, path, kwargs)),
    )
    graph_client.download_path("d", "/Books/index.json", str(tmp_path / "index.json"))

    assert calls[0][0].endswith("/drives/d/root:/Books/index.json:/content")
    assert calls[0][2]["timeout"] == graph.GRAPH_TIMEOUT


def test_list_children_reports_every_page():
    pages = [
        {
            "value": [
                {"id": "1", "name": "a.epub", "file": {}, "parentReference": {"driveId": "d"}}
            ],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/next",
        },
        {"value": [{"id": "2", "name": "b.epub", "file": {}, "parentReference": {"driveId": "d"}}]},
    ]

    def handler(_method, url, _kwargs):
        return pages[0] if "next" not in url else pages[1]

    seen = []
    graph_client, _auth = client(handler)
    children = graph_client.list_children("d", "i", on_page=seen.append)

    assert [child.name for child in children] == ["a.epub", "b.epub"]
    assert [[child.name for child in page] for page in seen] == [["a.epub"], ["b.epub"]]
