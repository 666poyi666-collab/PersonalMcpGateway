from __future__ import annotations

import json
import platform
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from personal_mcp_gateway.core.redaction import redact
from personal_mcp_gateway.core.runtime import GatewayRuntime


async def create_support_bundle(runtime: GatewayRuntime) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = runtime.settings.data_dir / f"support-bundle-{timestamp}.zip"
    status = redact(await runtime.system_status())
    errors = redact(await runtime.recent_errors(50))
    log_tail = ""
    if runtime.settings.log_path.exists():
        lines = runtime.settings.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        log_tail = "\n".join(lines[-500:])
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("status.json", json.dumps(status, indent=2, ensure_ascii=False))
        archive.writestr("errors.json", json.dumps(errors, indent=2, ensure_ascii=False))
        archive.writestr("gateway.log", log_tail)
        archive.writestr(
            "environment.json",
            json.dumps(
                {
                    "gatewayVersion": runtime.settings.version,
                    "python": platform.python_version(),
                    "platform": platform.platform(),
                },
                indent=2,
            ),
        )
        doctor = runtime.settings.data_dir / "tunnel-doctor-redacted.txt"
        if doctor.exists():
            archive.write(doctor, "tunnel-doctor-redacted.txt")
    return path
