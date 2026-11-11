"""A small HTTP inbound endpoint.

    POST /messages   body = XML, optional X-Correlation-Id header
    GET  /healthz    liveness
    GET  /metrics    counters in Prometheus text format

Processing is synchronous: the response is sent after the pipeline has finished. That keeps
the sketch easy to follow, but a sender that times out before retries complete will resend.
Idempotency makes that safe. A production design would persist first and acknowledge with
202 (see docs/adr/0001).
"""

from __future__ import annotations

import hmac
import json
import logging
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ..dlq import KEY_REUSE_CONFLICT, MALFORMED_XML, VALIDATION_FAILED
from ..logging_setup import log_event
from ..pipeline import Outcome, Pipeline

log = logging.getLogger("mini_middleware.adapters.http")
_CID_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")


def make_server(
    pipeline: Pipeline,
    host: str = "127.0.0.1",
    port: int = 8080,
    token: str = "",
    max_bytes: int = 1_000_000,
) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            log_event(log, "http_access", logging.DEBUG, line=fmt % args)

        def _send(self, status: int, body: dict | str, cid: str | None = None) -> None:
            if isinstance(body, str):
                payload, ctype = body.encode(), "text/plain; version=0.0.4"
            else:
                payload, ctype = json.dumps(body).encode(), "application/json"
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            if cid:
                self.send_header("X-Correlation-Id", cid)
            self.end_headers()
            self.wfile.write(payload)

        def _authorised(self) -> bool:
            if not token:
                return True
            supplied = self.headers.get("Authorization", "")
            return hmac.compare_digest(supplied.encode(), f"Bearer {token}".encode())

        def do_GET(self):
            if self.path == "/healthz":
                return self._send(200, {"status": "ok"})
            if self.path == "/metrics":
                return self._send(200, pipeline.metrics.render_text())
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/messages":
                return self._send(404, {"error": "not found"})
            if not self._authorised():
                return self._send(401, {"error": "unauthorised"})
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return self._send(411, {"error": "Content-Length required"})
            if length > max_bytes:
                return self._send(413, {"error": f"message larger than {max_bytes} bytes"})
            body = self.rfile.read(length)
            supplied = self.headers.get("X-Correlation-Id", "")
            cid = supplied if _CID_RE.fullmatch(supplied) else None
            result = pipeline.process(body, source=f"http:{self.client_address[0]}", correlation_id=cid)
            self._send(self._status_for(result), result.as_dict(), result.correlation_id)

        @staticmethod
        def _status_for(result) -> int:
            if result.outcome is not Outcome.DEAD_LETTERED:
                return 200
            if result.reason == KEY_REUSE_CONFLICT:
                return 409
            if result.reason in (MALFORMED_XML, VALIDATION_FAILED):
                return 422  # the sender must fix the message; retrying unchanged is pointless
            return 202  # accepted into our custody; delivery will be completed by replay

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server
