from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from unittest.mock import patch


def _load_cloud_sync() -> ModuleType:
    path = Path(__file__).parents[2] / "fleet" / "cloud_sync.py"
    spec = importlib.util.spec_from_file_location("cloud_sync", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Response:
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return b'{"ok":true,"stored":1}'


def test_push_uses_fixed_path_and_separate_bearer() -> None:
    cloud_sync = _load_cloud_sync()

    with patch.object(cloud_sync.urllib.request, "urlopen", return_value=_Response()) as send:
        result = cloud_sync.push(
            "https://example.test/",
            "sync-secret",
            "pc-sync",
            {"foxlink_get_status": "{}"},
        )

    request = send.call_args.args[0]
    assert request.full_url == "https://example.test/sync/push"
    assert request.headers["Authorization"] == "Bearer sync-secret"
    assert json.loads(request.data) == {
        "source": "pc-sync",
        "snapshots": {"foxlink_get_status": "{}"},
    }
    assert result == {"ok": True, "stored": 1}


def test_journal_push_uses_bearer_and_entry_envelope() -> None:
    cloud_sync = _load_cloud_sync()
    entries = [{"sourceKey": "local:2026-07-27", "sourceRevision": 1}]

    with patch.object(cloud_sync.urllib.request, "urlopen", return_value=_Response()) as send:
        cloud_sync.push_journal("https://journal.example.test", "journal-sync", entries)

    request = send.call_args.args[0]
    assert request.full_url == "https://journal.example.test/sync/push"
    assert request.headers["Authorization"] == "Bearer journal-sync"
    assert json.loads(request.data) == {"source": "pc-sync", "entries": entries}


def test_partial_snapshot_status_preserves_each_items_last_good_push() -> None:
    cloud_sync = _load_cloud_sync()

    class Client:
        def __init__(self, _url: str) -> None:
            pass

        def connect(self) -> None:
            pass

        def call_tool(self, name: str) -> tuple[bool, str]:
            return (True, '{"value":1}') if name == "fresh_tool" else (False, "offline")

    previous = {
        "lastSuccessfulPushAt": "2026-07-27T10:00:00Z",
        "lastCompletePushAt": "2026-07-27T10:00:00Z",
        "items": {
            "stale_tool": {
                "lastSuccessfulPushAt": "2026-07-27T10:00:00Z",
            }
        },
    }
    project = {
        "name": "watch",
        "localMcp": "http://127.0.0.1:8768/mcp",
        "cloudBase": "https://example.test",
        "syncKey": "secret",
        "tools": ["fresh_tool", "stale_tool"],
    }
    with (
        patch.object(cloud_sync, "McpClient", Client),
        patch.object(
            cloud_sync,
            "push",
            return_value={"stored": 1, "syncedAt": "2026-07-27T11:00:00Z"},
        ),
    ):
        result = cloud_sync.sync_project(project, previous)

    assert result["result"] == "partial"
    assert result["source"] == "pc-sync"
    assert result["pushedItems"] == 1
    assert result["skippedItems"] == 1
    assert result["lastSuccessfulPushAt"] == "2026-07-27T11:00:00Z"
    assert result["lastCompletePushAt"] == "2026-07-27T10:00:00Z"
    assert result["items"]["fresh_tool"]["result"] == "success"
    assert result["items"]["stale_tool"] == {
        "result": "failed",
        "lastAttemptAt": result["lastAttemptAt"],
        "lastSuccessfulPushAt": "2026-07-27T10:00:00Z",
        "reason": "local_data_unavailable",
    }


def test_cloud_failure_does_not_erase_last_successful_snapshot() -> None:
    cloud_sync = _load_cloud_sync()

    class Client:
        def __init__(self, _url: str) -> None:
            pass

        def connect(self) -> None:
            pass

        def call_tool(self, _name: str) -> tuple[bool, str]:
            return True, '{"value":1}'

    previous = {
        "lastSuccessfulPushAt": "2026-07-27T10:00:00Z",
        "items": {"tool": {"lastSuccessfulPushAt": "2026-07-27T10:00:00Z"}},
    }
    project = {
        "name": "foxlink",
        "localMcp": "http://127.0.0.1:8770/mcp",
        "cloudBase": "https://example.test",
        "syncKey": "secret",
        "tools": ["tool"],
    }
    with (
        patch.object(cloud_sync, "McpClient", Client),
        patch.object(
            cloud_sync,
            "push",
            side_effect=cloud_sync.urllib.error.URLError("offline"),
        ),
    ):
        result = cloud_sync.sync_project(project, previous)

    assert result["result"] == "failed"
    assert result["reason"] == "cloud_push_failed"
    assert result["lastSuccessfulPushAt"] == "2026-07-27T10:00:00Z"
    assert result["items"]["tool"]["lastSuccessfulPushAt"] == "2026-07-27T10:00:00Z"


def test_status_file_round_trip_is_versioned_and_atomic(tmp_path: Path) -> None:
    cloud_sync = _load_cloud_sync()
    path = tmp_path / "cloud-sync-status.json"
    projects = {
        "watch": {
            "result": "failed",
            "source": "pc-sync",
            "lastAttemptAt": "2026-07-27T10:00:00Z",
        }
    }

    cloud_sync.write_status(path, projects)

    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["schemaVersion"] == 1
    assert document["generatedAt"]
    assert cloud_sync.load_status(path) == projects
    assert list(tmp_path.glob("*.tmp")) == []
