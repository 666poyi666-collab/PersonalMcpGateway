from __future__ import annotations

import ctypes
import os
import re
import sys
import traceback
from ctypes import wintypes
from pathlib import Path


def redact_service_error(value: str) -> str:
    value = re.sub(r"sk-[A-Za-z0-9_-]+", "sk-[REDACTED]", value)
    value = re.sub(r"tunnel_[A-Za-z0-9_-]+", "tunnel_[REDACTED]", value)
    value = re.sub(
        r"\b(?:fl2|dj1|ds1|msr1|jor1|wor1|for1)\.[A-Za-z0-9._~-]+",
        "[REDACTED_CAPABILITY]",
        value,
    )
    value = re.sub(
        r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b",
        "[REDACTED_JWT]",
        value,
    )
    value = re.sub(
        r"(?i)\b(authorization|proxy-authorization|cookie|set-cookie)\s*[:=]\s*[^\r\n]+",
        r"\1: [REDACTED]",
        value,
    )
    value = re.sub(
        r"(?i)\b(token|api[_-]?key|client[_-]?secret|password|passwd|secret)\s*=\s*[^&\s]+",
        r"\1=[REDACTED]",
        value,
    )
    # Remove the complete URL, not only its host: paths and query strings often
    # carry one-time codes, signed database DSNs, or bearer-like capabilities.
    value = re.sub(
        r"(?i)\b(?:https?|postgres(?:ql)?|mysql|redis)://[^\s'\"<>]+",
        "[REDACTED_URL]",
        value,
    )
    return re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "[REDACTED_IP]", value)


def _report_to_event_log(message: str) -> None:
    if os.name != "nt":
        return
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.RegisterEventSourceW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    advapi32.RegisterEventSourceW.restype = wintypes.HANDLE
    advapi32.ReportEventW.argtypes = [
        wintypes.HANDLE,
        wintypes.WORD,
        wintypes.WORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.WORD,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPCWSTR),
        wintypes.LPVOID,
    ]
    advapi32.ReportEventW.restype = wintypes.BOOL
    advapi32.DeregisterEventSource.argtypes = [wintypes.HANDLE]
    advapi32.DeregisterEventSource.restype = wintypes.BOOL
    source = advapi32.RegisterEventSourceW(None, "PoyiPersonalMcpGateway")
    if not source:
        return
    try:
        strings = (wintypes.LPCWSTR * 1)(message)
        advapi32.ReportEventW(
            source,
            1,
            0,
            1002,
            None,
            1,
            0,
            strings,
            None,
        )
    finally:
        advapi32.DeregisterEventSource(source)


def report_service_error(message: str) -> None:
    redacted = redact_service_error(message)[-12000:]
    data_dir = Path(
        os.environ.get(
            "PERSONAL_MCP_DATA_DIR",
            str(Path(os.environ.get("PROGRAMDATA", ".")) / "Poyi" / "PersonalMcpGateway"),
        )
    )
    try:
        error_dir = data_dir / "recent-errors"
        error_dir.mkdir(parents=True, exist_ok=True)
        (error_dir / "gateway-bootstrap.log").write_text(redacted, encoding="utf-8")
    except OSError:
        pass
    _report_to_event_log(redacted)


def main() -> None:
    try:
        from personal_mcp_gateway.main import main as gateway_main

        gateway_main()
    except Exception:
        message = traceback.format_exc()
        report_service_error(message)
        print(redact_service_error(message), file=sys.stderr, flush=True)
        raise


if __name__ == "__main__":
    main()
