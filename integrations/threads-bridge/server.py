"""Read-only Threads connection check. Publishing is disabled."""
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def account():
    token = os.environ.get("THREADS_ACCESS_TOKEN", "").strip()
    if not token:
        return 503, {"error": "threads_token_not_configured"}

    request = Request(
        "https://graph.threads.net/v1.0/me?fields=id,username",
        headers={"Authorization": "Bearer " + token},
    )
    try:
        with urlopen(request, timeout=15) as response:
            data = json.load(response)
        if not isinstance(data, dict) or not all(
            field in data for field in ("id", "username")
        ):
            return 502, {"error": "unexpected_meta_response"}
        return 200, {
            "id": str(data["id"]),
            "username": data["username"],
            "publishing_enabled": False,
        }
    except HTTPError as error:
        return 502, {
            "error": "meta_request_failed",
            "upstream_status": error.code,
        }
    except (URLError, TimeoutError, ValueError):
        return 502, {"error": "meta_connection_failed"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, status, data):
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == "/health":
            return self.respond(200, {"ok": True})
        if self.path != "/account":
            return self.respond(404, {"error": "not_found"})

        key = os.environ.get("BRIDGE_API_KEY", "").strip()
        if not key:
            return self.respond(503, {"error": "bridge_key_not_configured"})
        if not hmac.compare_digest(
            self.headers.get("Authorization", ""), "Bearer " + key
        ):
            return self.respond(401, {"error": "unauthorized"})

        return self.respond(*account())

    def do_POST(self):
        self.respond(405, {"error": "publishing_not_enabled"})


if __name__ == "__main__":
    if not os.environ.get("BRIDGE_API_KEY", "").strip():
        raise SystemExit("BRIDGE_API_KEY must be configured")
    ThreadingHTTPServer(
        ("0.0.0.0", int(os.environ.get("PORT", "8000"))), Handler
    ).serve_forever()
