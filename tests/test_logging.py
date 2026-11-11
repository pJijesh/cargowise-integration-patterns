import io
import json
import logging

from mini_middleware.logging_setup import configure_logging, correlation, log_event


def test_json_logs_carry_the_correlation_id_and_redact_secrets():
    stream = io.StringIO()
    configure_logging("INFO", stream)
    log = logging.getLogger("mini_middleware.test")
    with correlation("abc-123"):
        log_event(log, "something_happened", key="K1", api_token="s3cret", authorization="Bearer x")
    log_event(log, "outside")
    first, second = (json.loads(line) for line in stream.getvalue().splitlines())
    assert first["event"] == "something_happened"
    assert first["correlation_id"] == "abc-123"
    assert first["key"] == "K1"
    assert first["api_token"] == "[REDACTED]" and first["authorization"] == "[REDACTED]"
    assert "s3cret" not in stream.getvalue()
    assert second["correlation_id"] == "-"
