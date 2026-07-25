from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from personal_mcp_gateway.settings import Settings


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            payload = {"event": "log", "message": message}
        payload.update(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "level": record.levelname,
                "component": record.name,
            }
        )
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(settings: Settings) -> None:
    settings.log_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = JsonFormatter()
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    file_handler = logging.FileHandler(settings.log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(stream)
    root.addHandler(file_handler)
    root.setLevel(settings.log_level.upper())
