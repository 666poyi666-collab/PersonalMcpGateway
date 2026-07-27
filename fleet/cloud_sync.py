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
import sys
import urllib.error
import urllib.request

UA = "poyi-cloud-sync/0.1"


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


def sync_journal(project: dict) -> None:
    name = project["name"]
    client = McpClient(project["localMcp"])
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
            return
        result = push_journal(project["cloudBase"], project["syncKey"], entries)
        print(
            f"[{name}] received {result.get('received')} local entry(s); "
            f"updated {result.get('stored')} cloud row(s)"
        )
    except (urllib.error.URLError, OSError, ValueError, TypeError) as exc:
        print(f"[{name}] journal sync failed: {exc}")


def sync_project(project: dict) -> None:
    name = project["name"]
    client = McpClient(project["localMcp"])
    try:
        client.connect()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"[{name}] local MCP unreachable: {exc}")
        return
    snapshots: dict[str, str] = {}
    skipped: list[str] = []
    for tool in project["tools"]:
        try:
            ok, text = client.call_tool(tool)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            ok, text = False, str(exc)
        if ok:
            snapshots[tool] = text
        else:
            skipped.append(f"{tool} ({text[:80]})")
    if not snapshots:
        print(f"[{name}] nothing to push; skipped: {'; '.join(skipped) or 'all tools failed'}")
        return
    try:
        result = push(project["cloudBase"], project["syncKey"], "pc-sync", snapshots)
        print(
            f"[{name}] pushed {result.get('stored')} snapshot(s) at {result.get('syncedAt')}"
            + (f"; skipped {len(skipped)}: {'; '.join(skipped)}" if skipped else "")
        )
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"[{name}] cloud push failed: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    with open(args.config, encoding="utf-8") as handle:
        config = json.load(handle)
    for project in config.get("projects", []):
        if project.get("kind") == "journal":
            sync_journal(project)
        else:
            sync_project(project)
    return 0


if __name__ == "__main__":
    sys.exit(main())
