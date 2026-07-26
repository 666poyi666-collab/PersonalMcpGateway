from personal_mcp_gateway.service_bootstrap import redact_service_error


def test_service_bootstrap_redacts_sensitive_fields() -> None:
    value = redact_service_error("sk-secret tunnel_abcdef http://private.example/v1 192.168.1.20")

    assert "secret" not in value
    assert "abcdef" not in value
    assert "private.example" not in value
    assert "192.168.1.20" not in value
