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
