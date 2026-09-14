"""Bounded process-lifetime counters; never store prompts, responses or keys."""
import json
import threading
import time
from collections import deque

PATHS = {"/v1/chat/completions", "/v1/responses", "/v1/messages"}


class RequestMetrics:
    def __init__(self):
        self.lock = threading.Lock()
        self.started_at = int(time.time())
        self.in_flight = self.total = self.success = self.http_success = 0
        self.duration_sum = 0
        self.api_count = self.test_count = 0
        self.recent = deque(maxlen=100)

    def begin(self):
        with self.lock:
            self.in_flight += 1

    def finish(self, path, source, status, ok, duration, outcome):
        with self.lock:
            self.in_flight -= 1
            self.total += 1
            self.success += int(ok)
            self.http_success += int(status is not None and 200 <= status < 300)
            self.duration_sum += duration
            self.api_count += int(source == "api")
            self.test_count += int(source == "test")
            self.recent.appendleft({"time": int(time.time()), "path": path, "source": source,
                                    "status": status, "ok": ok, "duration_ms": round(duration), "outcome": outcome})

    def snapshot(self):
        with self.lock:
            return {"started_at": self.started_at, "completed": self.total, "in_flight": self.in_flight,
                    "succeeded": self.success, "failed": self.total - self.success,
                    "success_rate": round(100*self.success/self.total, 1) if self.total else None,
                    "http_success_rate": round(100*self.http_success/self.total, 1) if self.total else None,
                    "avg_duration_ms": round(self.duration_sum/self.total) if self.total else None,
                    "api_count": self.api_count, "test_count": self.test_count, "recent": list(self.recent)}


class MetricsMiddleware:
    def __init__(self, app, metrics, source="api"):
        self.app, self.metrics, self.source = app, metrics, source

    async def __call__(self, scope, receive, send):
        path = scope.get("path")
        if scope["type"] != "http" or scope["method"] != "POST" or path not in PATHS:
            return await self.app(scope, receive, send)
        start = time.monotonic()
        self.metrics.begin()
        status = None
        completed = failed = streaming = terminal = disconnected = False
        buffer = b""
        oversized_line = False

        def event(line):
            nonlocal failed, terminal
            if not line.startswith(b"data:"):
                return
            payload = line[5:].strip()
            if payload == b"[DONE]":
                terminal = True
                return
            try:
                value = json.loads(payload)
            except ValueError:
                return
            if not isinstance(value, dict):
                return
            typ = value.get("type")
            if value.get("error") or typ in ("error", "response.failed", "response.incomplete"):
                failed = True
            if typ in ("response.completed", "message_stop"):
                terminal = True
            response = value.get("response")
            if isinstance(response, dict) and (response.get("error") or response.get("status") in ("failed", "incomplete")):
                failed = True

        async def observed_receive():
            nonlocal disconnected
            message = await receive()
            if message["type"] == "http.disconnect" and not completed:
                disconnected = True
            return message

        async def observed_send(message):
            nonlocal status, streaming, completed, buffer, failed, oversized_line
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = dict(message.get("headers", []))
                streaming = b"text/event-stream" in headers.get(b"content-type", b"").lower()
            elif message["type"] == "http.response.body":
                if streaming:
                    # Bound storage even if the upstream sends a huge unterminated line.
                    for fragment in message.get("body", b"").splitlines(keepends=True):
                        end = fragment.endswith(b"\n")
                        if not oversized_line and len(buffer) + len(fragment) <= 65536:
                            buffer += fragment
                        else:
                            oversized_line = True
                            buffer = b""
                        if end:
                            if not oversized_line:
                                event(buffer)
                            buffer = b""
                            oversized_line = False
                if not message.get("more_body", False):
                    if streaming and buffer:
                        event(buffer)
                    await send(message)
                    completed = True
                    return
            await send(message)

        try:
            await self.app(scope, observed_receive, observed_send)
        except BaseException:
            failed = True
            raise
        finally:
            http_ok = status is not None and 200 <= status < 300
            ok = http_ok and completed and not disconnected and not failed and (not streaming or terminal)
            outcome = "success" if ok else "stream_error" if failed and streaming else "interrupted" if not completed or disconnected or (streaming and not terminal) else "http_error"
            self.metrics.finish(path, self.source, status, ok, (time.monotonic()-start)*1000, outcome)
