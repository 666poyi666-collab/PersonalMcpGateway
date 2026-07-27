"""PC-side cloud sync agent.

Reads each local MCP's read-only tools over loopback and pushes the results to
that project's Cloudflare mirror (`/sync/push`). Tools that fail — e.g. the
watch MCP while the phone is unreachable — are skipped so the cloud keeps its
last good snapshot; nothing ever pretends a device was online.

Run by the fleet watchdog every few minutes with the gateway's private Python:
    python cloud_sync.py --config C:\\ProgramData\\Poyi\\FleetWatchdog\\cloud-sync-config.json
Stdlib only. Exit code 0 even on partial failure (partial sync is normal).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

UA = "poyi-cloud-sync/0.1"
STATUS_SCHEMA_VERSION = 1


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _project_id(project: dict) -> str:
    raw = str(project.get("id") or project.get("name") or "unknown").lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", raw).strip("_") or "unknown"
    aliases = {
        "watch_mcp": "watch",
        "watchintervals": "watch",
        "focuslink": "foxlink",
        "foxlink_mcp": "foxlink",
        "daylight_journal": "journal",
        "journal_mcp": "journal",
    }
    return aliases.get(normalized, normalized)


def _previous_timestamp(previous: dict, field: str) -> str | None:
    if not isinstance(previous, dict):
        return None
    value = previous.get(field)
    return value if isinstance(value, str) and value else None


def _item_observation(
    previous: dict,
    *,
    result: str,
    attempted_at: str,
    successful_at: str | None = None,
    reason: str | None = None,
) -> dict:
    observation = {
        "result": result,
        "lastAttemptAt": attempted_at,
    }
    last_success = successful_at or _previous_timestamp(previous, "lastSuccessfulPushAt")
    if last_success:
        observation["lastSuccessfulPushAt"] = last_success
    if reason:
        observation["reason"] = reason
    return observation


def _project_observation(
    previous: dict,
    *,
    result: str,
    attempted_at: str,
    pushed_items: int,
    skipped_items: int,
    total_items: int,
    items: dict[str, dict] | None = None,
    successful_at: str | None = None,
    complete_at: str | None = None,
    reason: str | None = None,
) -> dict:
    observation = {
        "result": result,
        "source": "pc-sync",
        "lastAttemptAt": attempted_at,
        "pushedItems": pushed_items,
        "skippedItems": skipped_items,
        "totalItems": total_items,
        "items": items or {},
    }
    last_success = successful_at or _previous_timestamp(previous, "lastSuccessfulPushAt")
    if last_success:
        observation["lastSuccessfulPushAt"] = last_success
    last_complete = complete_at or _previous_timestamp(previous, "lastCompletePushAt")
    if last_complete:
        observation["lastCompletePushAt"] = last_complete
    if reason:
        observation["reason"] = reason
    return observation


def load_status(path: Path) -> dict:
    try:
        if path.stat().st_size > 256_000:
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict) or payload.get("schemaVersion") != STATUS_SCHEMA_VERSION:
        return {}
    projects = payload.get("projects")
    return projects if isinstance(projects, dict) else {}


def write_status(path: Path, projects: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    document = {
        "schemaVersion": STATUS_SCHEMA_VERSION,
        "generatedAt": _now_iso(),
        "projects": projects,
    }
    try:
        temporary.write_text(
            json.dumps(document, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


class McpClient:
    def __init__(self, url: str, timeout: int = 10) -> None:
        self.url = url
        self.timeout = timeout
        self.session_id: str | None = None
        self._next_id = 0

    def _post(self, payload: dict) -> str:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": UA,
        }
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        request = urllib.request.Request(self.url, json.dumps(payload).encode(), headers)
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            sid = response.headers.get("mcp-session-id")
            if sid:
                self.session_id = sid
            return response.read().decode("utf-8", "replace")

    def rpc(self, method: str, params: dict | None = None, *, notify: bool = False):
        payload: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        if not notify:
            self._next_id += 1
            payload["id"] = self._next_id
        body = self._post(payload)
        if notify:
            return None
        for line in body.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        return json.loads(body) if body.strip() else None

    def connect(self) -> None:
        self.rpc(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "poyi-cloud-sync", "version": "0.1"},
            },
        )
        self.rpc("notifications/initialized", notify=True)

    def call_tool(self, name: str, arguments: dict | None = None) -> tuple[bool, str]:
        result = self.rpc("tools/call", {"name": name, "arguments": arguments or {}})
        if not result or "result" not in result:
            return False, json.dumps(result.get("error") if result else {"error": "no_response"})
        outcome = result["result"]
        texts = [c.get("text", "") for c in outcome.get("content", []) if c.get("type") == "text"]
        text = "\n".join(texts)
        if outcome.get("isError"):
            return False, text
        return True, text


def push(cloud_base: str, key: str, source: str, snapshots: dict[str, str]) -> dict:
    url = f"{cloud_base.rstrip('/')}/sync/push"
    body = json.dumps({"source": source, "snapshots": snapshots}).encode()
    request = urllib.request.Request(
        url,
        body,
        {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": UA,
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())


def push_journal(cloud_base: str, key: str, entries: list[dict]) -> dict:
    url = f"{cloud_base.rstrip('/')}/sync/push"
    body = json.dumps({"source": "pc-sync", "entries": entries}).encode()
    request = urllib.request.Request(
        url,
        body,
        {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": UA,
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())


def _tool_data(text: str) -> dict:
    payload = json.loads(text)
    data = payload.get("data", payload)
    return data if isinstance(data, dict) else {}


def sync_journal(project: dict, previous: dict | None = None) -> dict:
    name = project["name"]
    prior = previous or {}
    attempted_at = _now_iso()
    client = McpClient(project["localMcp"])
    stage = "local"
    try:
        client.connect()
        cursor = ""
        entries: list[dict] = []
        while True:
            ok, text = client.call_tool("journal_list_recent", {"limit": 100, "cursor": cursor})
            if not ok:
                raise ValueError(text)
            page = _tool_data(text)
            for summary in page.get("items", []):
                if not isinstance(summary, dict) or not summary.get("date"):
                    continue
                date = str(summary["date"])
                chunks: list[str] = []
                offset = 0
                detail: dict = {}
                while True:
                    ok, text = client.call_tool(
                        "journal_get_entry",
                        {"date": date, "offset": offset, "maxChars": 12_000},
                    )
                    if not ok:
                        raise ValueError(text)
                    detail = _tool_data(text)
                    chunks.append(str(detail.get("contentChunk", "")))
                    next_offset = detail.get("nextOffset")
                    if next_offset is None:
                        break
                    offset = int(next_offset)
                metadata = detail.get("entry", {})
                entries.append(
                    {
                        "sourceKey": f"local:{date}",
                        "sourceRevision": int(detail.get("revision", summary.get("revision", 1))),
                        "date": date,
                        "title": str(metadata.get("title", summary.get("title", ""))),
                        "body": "".join(chunks),
                        "tags": metadata.get("tags", summary.get("tags", [])),
                        "mood": metadata.get("mood", summary.get("mood")),
                        "updatedAt": str(metadata.get("updatedAt", summary.get("updatedAt", ""))),
                    }
                )
            cursor = str(page.get("nextCursor") or "")
            if not cursor:
                break
        if not entries:
            print(f"[{name}] no local entries to push")
            return _project_observation(
                prior,
                result="no_changes",
                attempted_at=attempted_at,
                pushed_items=0,
                skipped_items=0,
                total_items=0,
                reason="no_local_entries",
            )
        stage = "cloud"
        result = push_journal(project["cloudBase"], project["syncKey"], entries)
        print(
            f"[{name}] received {result.get('received')} local entry(s); "
            f"updated {result.get('stored')} cloud row(s)"
        )
        synced_at = _now_iso()
        return _project_observation(
            prior,
            result="success",
            attempted_at=attempted_at,
            pushed_items=len(entries),
            skipped_items=0,
            total_items=len(entries),
            successful_at=synced_at,
            complete_at=synced_at,
        )
    except (urllib.error.URLError, OSError, ValueError, TypeError) as exc:
        print(f"[{name}] journal sync failed: {exc}")
        reason = "cloud_push_failed" if stage == "cloud" else "local_data_unavailable"
        return _project_observation(
            prior,
            result="failed",
            attempted_at=attempted_at,
            pushed_items=0,
            skipped_items=0,
            total_items=0,
            reason=reason,
        )


def sync_project(project: dict, previous: dict | None = None) -> dict:
    name = project["name"]
    prior = previous or {}
    attempted_at = _now_iso()
    tools = [str(tool) for tool in project["tools"]]
    previous_items = prior.get("items")
    prior_items = previous_items if isinstance(previous_items, dict) else {}
    client = McpClient(project["localMcp"])
    try:
        client.connect()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"[{name}] local MCP unreachable: {exc}")
        items = {
            tool: _item_observation(
                prior_items.get(tool, {}),
                result="failed",
                attempted_at=attempted_at,
                reason="local_mcp_unreachable",
            )
            for tool in tools
        }
        return _project_observation(
            prior,
            result="failed",
            attempted_at=attempted_at,
            pushed_items=0,
            skipped_items=len(tools),
            total_items=len(tools),
            items=items,
            reason="local_mcp_unreachable",
        )
    snapshots: dict[str, str] = {}
    skipped: list[str] = []
    skipped_tools: set[str] = set()
    for tool in tools:
        try:
            ok, text = client.call_tool(tool)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            ok, text = False, str(exc)
        if ok:
            snapshots[tool] = text
        else:
            skipped.append(f"{tool} ({text[:80]})")
            skipped_tools.add(tool)
    if not snapshots:
        print(f"[{name}] nothing to push; skipped: {'; '.join(skipped) or 'all tools failed'}")
        items = {
            tool: _item_observation(
                prior_items.get(tool, {}),
                result="failed",
                attempted_at=attempted_at,
                reason="local_data_unavailable",
            )
            for tool in tools
        }
        return _project_observation(
            prior,
            result="failed",
            attempted_at=attempted_at,
            pushed_items=0,
            skipped_items=len(tools),
            total_items=len(tools),
            items=items,
            reason="local_data_unavailable",
        )
    try:
        result = push(project["cloudBase"], project["syncKey"], "pc-sync", snapshots)
        synced_at_raw = result.get("syncedAt")
        synced_at = (
            synced_at_raw if isinstance(synced_at_raw, str) and synced_at_raw else attempted_at
        )
        print(
            f"[{name}] pushed {result.get('stored')} snapshot(s) at {result.get('syncedAt')}"
            + (f"; skipped {len(skipped)}: {'; '.join(skipped)}" if skipped else "")
        )
        items = {
            tool: _item_observation(
                prior_items.get(tool, {}),
                result="failed" if tool in skipped_tools else "success",
                attempted_at=attempted_at,
                successful_at=None if tool in skipped_tools else synced_at,
                reason="local_data_unavailable" if tool in skipped_tools else None,
            )
            for tool in tools
        }
        complete_at = synced_at if not skipped_tools else None
        return _project_observation(
            prior,
            result="partial" if skipped_tools else "success",
            attempted_at=attempted_at,
            pushed_items=len(snapshots),
            skipped_items=len(skipped_tools),
            total_items=len(tools),
            items=items,
            successful_at=synced_at,
            complete_at=complete_at,
            reason="local_items_unavailable" if skipped_tools else None,
        )
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"[{name}] cloud push failed: {exc}")
        items = {
            tool: _item_observation(
                prior_items.get(tool, {}),
                result="failed",
                attempted_at=attempted_at,
                reason=(
                    "local_data_unavailable" if tool in skipped_tools else "cloud_push_failed"
                ),
            )
            for tool in tools
        }
        return _project_observation(
            prior,
            result="failed",
            attempted_at=attempted_at,
            pushed_items=0,
            skipped_items=len(tools),
            total_items=len(tools),
            items=items,
            reason="cloud_push_failed",
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--status")
    args = parser.parse_args()
    with open(args.config, encoding="utf-8") as handle:
        config = json.load(handle)
    status_path = Path(args.status) if args.status else None
    statuses = load_status(status_path) if status_path else {}
    for project in config.get("projects", []):
        project_id = _project_id(project)
        previous = statuses.get(project_id)
        prior = previous if isinstance(previous, dict) else {}
        if project.get("kind") == "journal":
            statuses[project_id] = sync_journal(project, prior)
        else:
            statuses[project_id] = sync_project(project, prior)
    if status_path:
        try:
            write_status(status_path, statuses)
        except OSError as exc:
            print(f"[status] unable to write sync status: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
