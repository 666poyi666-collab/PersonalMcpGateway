from pathlib import Path
from xml.etree import ElementTree


def test_gateway_service_uses_direct_private_python() -> None:
    root = Path(__file__).parents[2]
    service = ElementTree.parse(root / "service" / "gateway-service.xml").getroot()

    assert service.findtext("executable") == "%PRIVATE_PYTHON%"
    assert service.findtext("arguments") == ("-s -m personal_mcp_gateway.service_bootstrap serve")
    environment = {item.attrib["name"]: item.attrib["value"] for item in service.findall("env")}
    assert environment["PYTHONUTF8"] == "1"
    assert environment["PYTHONUNBUFFERED"] == "1"
    assert environment["PYTHONNOUSERSITE"] == "1"
