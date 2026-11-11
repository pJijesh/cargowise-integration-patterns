"""A mock ERP HTTP server that misbehaves on request.

Behaviour is chosen from the shipment reference in the delivered message, so tests and the
demo are deterministic:

    SIM-FAIL500-<n>   the first n requests for this reference return HTTP 500
    SIM-THROTTLE-<n>  the first n requests return HTTP 429 with Retry-After: 0
    SIM-TIMEOUT-<n>   the first n requests hang, so the client times out; nothing is stored
    SIM-LOSTACK-<n>   the first n requests are stored, but the response never arrives
    SIM-REJECT-...    always HTTP 422 (a permanent rejection)
    SIM-OUTAGE-...    always HTTP 503 until recover() is called
    anything else     stored and accepted (HTTP 201)

The server also remembers every Idempotency-Key it has stored and answers HTTP 409 for a
repeat, which is how many real APIs signal "already processed".
"""

from __future__ import annotations

import json
import re
import threading
import time
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class MockErp:
    def __init__(
        self, host: str = "127.0.0.1", port: int = 0, token: str = "", slow_seconds: float = 2.0
    ) -> None:
        self.token = token
        self.slow_seconds = slow_seconds
        self._lock = threading.Lock()
        self._records: dict[str, dict] = {}
        self._counters: dict[str, int] = {}
        self._recovered = False
        self._server = ThreadingHTTPServer((host, port), self._make_handler())
        self._server.daemon_threads = True
        self._thread: threading.Thread | None = None

    # ---- lifecycle ---------------------------------------------------------------

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/erp/messages"

    def start(self) -> "MockErp":
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def serve_forever(self) -> None:
        self._server.serve_forever()

    # ---- test / demo controls ----------------------------------------------------

    def recover(self) -> None:
        with self._lock:
            self._recovered = True

    def forget(self, key: str) -> bool:
        """Simulate the ERP losing a record, so reconciliation has something to find."""
        with self._lock:
            return self._records.pop(key, None) is not None

    def records(self) -> list[dict]:
        with self._lock:
            return [dict(v, key=k) for k, v in self._records.items()]

    # ---- behaviour ---------------------------------------------------------------

    def _tick(self, reference: str) -> int:
        with self._lock:
            self._counters[reference] = self._counters.get(reference, 0) + 1
            return self._counters[reference]

    def _decide(self, reference: str) -> str:
        """Return one of: ok, 500, 429, 422, 503, timeout, lostack."""
        m = re.match(r"SIM-(FAIL500|THROTTLE|TIMEOUT|LOSTACK)-(\d+)", reference)
        if m:
            kind, n = m.group(1), int(m.group(2))
            if self._tick(reference) <= n:
                return {"FAIL500": "500", "THROTTLE": "429", "TIMEOUT": "timeout", "LOSTACK": "lostack"}[kind]
            return "ok"
        if reference.startswith("SIM-REJECT"):
            return "422"
        if reference.startswith("SIM-OUTAGE"):
            with self._lock:
                return "ok" if self._recovered else "503"
        return "ok"

    def _store(self, key: str, reference: str, size: int) -> bool:
        """Store a record. Returns False if the key was already present."""
        with self._lock:
            if key in self._records:
                return False
            self._records[key] = {
                "reference": reference,
                "bytes": size,
                "stored_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            return True

    def _make_handler(self):
        erp = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # keep test and demo output clean
                pass

            def _reply(self, status: int, body: dict | None = None, headers: dict | None = None):
                payload = json.dumps(body or {}).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    for k, v in (headers or {}).items():
                        self.send_header(k, v)
                    self.end_headers()
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # the client already gave up, which is exactly the scenario

            def _authorised(self) -> bool:
                if not erp.token:
                    return True
                return self.headers.get("Authorization", "") == f"Bearer {erp.token}"

            def do_GET(self):
                if not self._authorised():
                    return self._reply(401, {"error": "unauthorised"})
                if urlparse(self.path).path == "/erp/messages":
                    return self._reply(200, {"records": erp.records()})
                self._reply(404, {"error": "not found"})

            def do_POST(self):
                parsed = urlparse(self.path)
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                if parsed.path == "/_control/recover":
                    erp.recover()
                    return self._reply(200, {"recovered": True})
                if parsed.path == "/_control/forget":
                    key = parse_qs(parsed.query).get("key", [""])[0]
                    return self._reply(200, {"forgotten": erp.forget(key)})
                if parsed.path != "/erp/messages":
                    return self._reply(404, {"error": "not found"})
                if not self._authorised():
                    return self._reply(401, {"error": "unauthorised"})
                key = self.headers.get("Idempotency-Key", "")
                if not key:
                    return self._reply(400, {"error": "Idempotency-Key header required"})
                try:
                    root = ET.fromstring(body)
                    if root.tag != "ErpInboundMessage":
                        raise ValueError("unexpected root element")
                    reference = (root.findtext("ExternalRef") or "").strip()
                    if not reference:
                        raise ValueError("ExternalRef missing")
                except (ET.ParseError, ValueError) as exc:
                    return self._reply(400, {"error": f"bad message: {exc}"})

                decision = erp._decide(reference)
                if decision == "500":
                    return self._reply(500, {"error": "simulated internal error"})
                if decision == "503":
                    return self._reply(503, {"error": "simulated outage"})
                if decision == "429":
                    return self._reply(429, {"error": "slow down"}, {"Retry-After": "0"})
                if decision == "422":
                    return self._reply(422, {"error": "simulated business rule rejection"})
                if decision == "timeout":
                    time.sleep(erp.slow_seconds)
                    return self._reply(200, {"note": "too late"})
                if decision == "lostack":
                    erp._store(key, reference, len(body))
                    time.sleep(erp.slow_seconds)
                    return self._reply(201, {"note": "stored, but you will not see this"})
                if erp._store(key, reference, len(body)):
                    return self._reply(201, {"stored": key})
                return self._reply(409, {"error": "already processed", "key": key})

        return Handler
