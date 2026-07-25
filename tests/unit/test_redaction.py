from personal_mcp_gateway.core.redaction import redact


def test_redacts_nested_sensitive_values() -> None:
    value = {
        "pairingCode": "123456",
        "nested": {"apiKey": "secret", "durationMs": 5},
        "route": [{"latitude": 1, "longitude": 2}],
    }
    assert redact(value) == {
        "pairingCode": "[REDACTED]",
        "nested": {"apiKey": "[REDACTED]", "durationMs": 5},
        "route": "[REDACTED]",
    }
