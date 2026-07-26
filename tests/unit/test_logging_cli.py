import json
import logging
from pathlib import Path

import pytest

from personal_mcp_gateway.core.logging import JsonFormatter, configure_logging
from personal_mcp_gateway.main import build_runtime, doctor, parser
from personal_mcp_gateway.settings import Settings


def test_json_formatter_and_logging_configuration(tmp_path: Path) -> None:
    formatter = JsonFormatter()
    plain = formatter.format(logging.LogRecord("test", logging.INFO, "", 0, "hello", (), None))
    structured = formatter.format(
        logging.LogRecord("test", logging.WARNING, "", 0, '{"event":"ready"}', (), None)
    )
    assert json.loads(plain)["message"] == "hello"
    assert json.loads(structured)["event"] == "ready"

    settings = Settings(data_dir=tmp_path)
    configure_logging(settings)
    logging.getLogger("test").info("configured")
    assert settings.log_path.exists()


def test_logging_falls_back_to_stderr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(*args: object, **kwargs: object) -> logging.Handler:
        raise PermissionError("denied")

    monkeypatch.setattr(logging, "FileHandler", denied)
    configure_logging(Settings(data_dir=tmp_path))

    assert len(logging.getLogger().handlers) == 1
    assert isinstance(logging.getLogger().handlers[0], logging.StreamHandler)


@pytest.mark.asyncio
async def test_doctor_and_parser(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    runtime = build_runtime(Settings(data_dir=tmp_path, modules_dir=tmp_path / "missing"))
    assert await doctor(runtime) == 0
    assert json.loads(capsys.readouterr().out)["modules"] == []
    assert parser().parse_args(["migrate-db"]).command == "migrate-db"
