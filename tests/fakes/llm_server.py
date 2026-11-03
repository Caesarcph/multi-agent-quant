"""A tiny OpenAI-compatible chat-completions server for offline tests.

It speaks the real HTTP protocol, so tests exercise the installed ``openai`` SDK
end to end (request building, JSON parsing and SSE streaming) without network
access or API keys.

    with FakeLLMServer() as server:
        server.reply('{"action": "HOLD"}')
        client = OpenAI(base_url=server.base_url, api_key="test")
        ...
        assert server.requests[0]["model"] == "deepseek-test"
"""

from __future__ import annotations

import json
import threading
from collections import deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


@dataclass
class ScriptedReply:
    content: str
    reasoning: str | None = None
    status: int = 200
    chunk_size: int = 7  # characters per SSE chunk when the request asks to stream


class FakeLLMServer:
    def __init__(self, model: str = "fake-model") -> None:
        self.model = model
        self.requests: list[dict[str, Any]] = []
        self._replies: deque[ScriptedReply] = deque()
        self._lock = threading.Lock()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ scripting
    def reply(self, content: str, reasoning: str | None = None) -> None:
        with self._lock:
            self._replies.append(ScriptedReply(content=content, reasoning=reasoning))

    def fail(self, status: int, message: str = "error") -> None:
        with self._lock:
            self._replies.append(ScriptedReply(content=message, status=status))

    @property
    def pending(self) -> int:
        return len(self._replies)

    # ------------------------------------------------------------------ lifecycle
    @property
    def base_url(self) -> str:
        assert self._httpd is not None, "server not started"
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}/v1"

    def start(self) -> FakeLLMServer:
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # keep test output quiet
                pass

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                with server._lock:
                    server.requests.append(body)
                    reply = server._replies.popleft() if server._replies else None
                if reply is None:
                    self._send_json(500, {"error": {"message": "no scripted reply left"}})
                    return
                if reply.status != 200:
                    self._send_json(reply.status, {"error": {"message": reply.content}})
                    return
                if body.get("stream"):
                    self._send_stream(reply, body)
                else:
                    self._send_json(200, server._completion(reply, body))

            def _send_json(self, status: int, payload: dict[str, Any]) -> None:
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _send_stream(self, reply: ScriptedReply, body: dict[str, Any]) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for delta in server._deltas(reply):
                    chunk = server._chunk(delta, body)
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        # A short poll interval keeps shutdown() (test teardown) fast.
        httpd = self._httpd
        self._thread = threading.Thread(
            target=lambda: httpd.serve_forever(poll_interval=0.02), daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    def __enter__(self) -> FakeLLMServer:
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    # ------------------------------------------------------------------ payloads
    def _completion(self, reply: ScriptedReply, body: dict[str, Any]) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": reply.content}
        if reply.reasoning:
            message["reasoning_content"] = reply.reasoning
        return {
            "id": f"chatcmpl-{len(self.requests)}",
            "object": "chat.completion",
            "created": 0,
            "model": body.get("model", self.model),
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    def _deltas(self, reply: ScriptedReply) -> list[dict[str, Any]]:
        deltas: list[dict[str, Any]] = [{"role": "assistant", "content": ""}]
        if reply.reasoning:
            deltas.append({"reasoning_content": reply.reasoning})
        text = reply.content
        for i in range(0, len(text), reply.chunk_size):
            deltas.append({"content": text[i : i + reply.chunk_size]})
        return deltas

    def _chunk(self, delta: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": f"chatcmpl-{len(self.requests)}",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": body.get("model", self.model),
            "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
        }
