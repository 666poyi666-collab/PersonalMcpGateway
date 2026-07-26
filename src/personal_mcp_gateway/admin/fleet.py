"""Fleet visibility and repair triggering.

The board shows, next to each probe, whether the underlying Windows service is
actually running, plus the state of the PoyiFleetWatchdog service that keeps the
whole fleet alive. Service states are read through ``advapi32`` with plain
``ctypes`` so the gateway gains no new dependency and non-Windows test runs
degrade to ``unknown``.

Repairs are requested by dropping a file into the watchdog's trigger directory.
The watchdog (LocalSystem) picks it up within one loop and runs a forced
remediation pass. Writing a file — instead of calling a gateway endpoint — keeps
the repair path alive even when the gateway itself is the thing that is down.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

WATCHDOG_SERVICE = "PoyiFleetWatchdog"

_TRIGGER_DIR_ENV = "POYI_FLEET_TRIGGER_DIR"
_DEFAULT_TRIGGER_DIR = r"C:\ProgramData\Poyi\FleetWatchdog\triggers"

_STATE_LABELS = {
    1: "stopped",
    2: "start_pending",
    3: "stop_pending",
    4: "running",
    5: "continue_pending",
    6: "pause_pending",
    7: "paused",
}


def trigger_dir() -> Path:
    return Path(os.environ.get(_TRIGGER_DIR_ENV, _DEFAULT_TRIGGER_DIR))


def query_service_state(name: str) -> str:
    """Return one service's SCM state, or ``missing`` / ``unknown``."""
    if os.name != "nt":
        return "unknown"
    import ctypes
    from ctypes import wintypes

    SC_MANAGER_CONNECT = 0x0001
    SERVICE_QUERY_STATUS = 0x0004
    ERROR_SERVICE_DOES_NOT_EXIST = 1060

    class ServiceStatus(ctypes.Structure):
        _fields_ = [
            ("dwServiceType", wintypes.DWORD),
            ("dwCurrentState", wintypes.DWORD),
            ("dwControlsAccepted", wintypes.DWORD),
            ("dwWin32ExitCode", wintypes.DWORD),
            ("dwServiceSpecificExitCode", wintypes.DWORD),
            ("dwCheckPoint", wintypes.DWORD),
            ("dwWaitHint", wintypes.DWORD),
        ]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    # Handles are pointer-sized: without explicit restypes ctypes truncates
    # them to 32-bit ints and every call fails on 64-bit Windows.
    advapi32.OpenSCManagerW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    advapi32.OpenSCManagerW.restype = ctypes.c_void_p
    advapi32.OpenServiceW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, wintypes.DWORD]
    advapi32.OpenServiceW.restype = ctypes.c_void_p
    advapi32.QueryServiceStatus.argtypes = [ctypes.c_void_p, ctypes.POINTER(ServiceStatus)]
    advapi32.QueryServiceStatus.restype = wintypes.BOOL
    advapi32.CloseServiceHandle.argtypes = [ctypes.c_void_p]
    advapi32.CloseServiceHandle.restype = wintypes.BOOL
    manager = advapi32.OpenSCManagerW(None, None, SC_MANAGER_CONNECT)
    if not manager:
        return "unknown"
    try:
        service = advapi32.OpenServiceW(manager, name, SERVICE_QUERY_STATUS)
        if not service:
            if ctypes.get_last_error() == ERROR_SERVICE_DOES_NOT_EXIST:
                return "missing"
            return "unknown"
        try:
            status = ServiceStatus()
            if not advapi32.QueryServiceStatus(service, ctypes.byref(status)):
                return "unknown"
            return _STATE_LABELS.get(int(status.dwCurrentState), "unknown")
        finally:
            advapi32.CloseServiceHandle(service)
    finally:
        advapi32.CloseServiceHandle(manager)


def query_service_states(names: list[str]) -> dict[str, str]:
    return {name: query_service_state(name) for name in names}


def repair_supported() -> bool:
    """The board offers one-click repair only when the watchdog is deployed."""
    return trigger_dir().is_dir()


def request_repair(source: str) -> Path:
    """Drop a repair request for the watchdog; returns the file written.

    Raises ``OSError`` when the trigger directory is absent or not writable, so
    callers can surface a useful message instead of pretending it worked.
    """
    directory = trigger_dir()
    if not directory.is_dir():
        raise FileNotFoundError(f"trigger directory missing: {directory}")
    payload = {
        "requestedAt": datetime.now(UTC).isoformat(),
        "source": source,
    }
    path = directory / f"repair-{uuid.uuid4().hex}.json"
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    return path
