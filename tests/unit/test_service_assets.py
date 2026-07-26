from pathlib import Path
from xml.etree import ElementTree


def test_gateway_service_uses_direct_private_python() -> None:
    root = Path(__file__).parents[2]
    service = ElementTree.parse(root / "service" / "gateway-service.xml").getroot()
    runner = (root / "service" / "run-gateway-service.ps1").read_text(encoding="utf-8")
    installer = (root / "service" / "install.ps1").read_text(encoding="utf-8")

    assert service.findtext("executable") == "%PRIVATE_PYTHON%"
    assert service.findtext("arguments") == ("-s -m personal_mcp_gateway.service_bootstrap serve")
    environment = {item.attrib["name"]: item.attrib["value"] for item in service.findall("env")}
    assert environment["PYTHONUTF8"] == "1"
    assert environment["PYTHONUNBUFFERED"] == "1"
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert "WATCH_MCP_PHONE_TOKEN" not in runner
    assert "watch-token.dpapi" not in runner
    assert "modules\\watch.yaml" in installer
    assert "watch-token.dpapi" in installer
