from personal_mcp_gateway.service_bootstrap import redact_service_error


def test_service_bootstrap_redacts_sensitive_fields() -> None:
    value = redact_service_error(
        "sk-secret tunnel_abcdef http://private.example/v1?token=query-secret "
        "192.168.1.20 Authorization: Bearer header-secret\n"
        "Cookie: session=cookie-secret\n"
        "client_secret=form-secret api_key=key-secret "
        "dj1.device.secret msr1.capability "
        "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.signature "
        "postgresql://user:db-secret@db.example/private"
    )

    assert "query-secret" not in value
    assert "header-secret" not in value
    assert "cookie-secret" not in value
    assert "form-secret" not in value
    assert "key-secret" not in value
    assert "db-secret" not in value
    assert "abcdef" not in value
    assert "private.example" not in value
    assert "192.168.1.20" not in value
    assert "dj1.device.secret" not in value
    assert "eyJhbGciOiJSUzI1NiJ9" not in value
