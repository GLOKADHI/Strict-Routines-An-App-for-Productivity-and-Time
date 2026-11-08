"""Loopback-only request checks and bounded streaming request bodies."""
from __future__ import annotations

import ipaddress
import json
from urllib.parse import urlsplit


MIB = 1024 * 1024
MAX_LEGACY_REQUEST_BYTES = 11 * MIB  # 10 MiB image plus bounded multipart framing.
MAX_EVIDENCE_REQUEST_BYTES = 126 * MIB  # 125 MiB image content plus 1 MiB framing.


def is_loopback_bind(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except (ValueError, TypeError):
        return False


def validate_bind_host(host: str) -> str:
    """Only accept an explicit loopback address for local API listeners."""
    if not is_loopback_bind(host):
        raise ValueError("Local analysis services may bind only to a loopback IP address.")
    return host


def _headers(scope):
    return {key.lower(): value for key, value in scope.get("headers", [])}


def _loopback_name(value: str) -> bool:
    try:
        authority = urlsplit("//" + value)
        if authority.username or authority.password or authority.path or authority.query or authority.fragment:
            return False
        hostname = authority.hostname
        if not hostname:
            return False
        if hostname.lower() == "localhost":
            _ = authority.port  # Reject malformed ports.
            return True
        return ipaddress.ip_address(hostname).is_loopback
    except (ValueError, TypeError):
        return False


async def _json_response(send, status: int, detail: str):
    body = json.dumps({"detail": detail}, separators=(",", ":")).encode("utf-8")
    await send({"type": "http.response.start", "status": status, "headers": [
        (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode("ascii")),
        (b"cache-control", b"no-store"),
    ]})
    await send({"type": "http.response.body", "body": body, "more_body": False})


class LoopbackRequestGuardMiddleware:
    """Reject non-loopback peers and unsafe Host/Origin combinations."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        client = scope.get("client")
        try:
            if not client or not ipaddress.ip_address(client[0]).is_loopback:
                return await _json_response(send, 403, "This service accepts loopback requests only.")
        except (ValueError, TypeError):
            return await _json_response(send, 403, "This service accepts loopback requests only.")
        raw_headers = scope.get("headers", [])
        host_values = [value for key, value in raw_headers if key.lower() == b"host"]
        origin_values = [value for key, value in raw_headers if key.lower() == b"origin"]
        if len(host_values) != 1 or len(origin_values) > 1:
            return await _json_response(send, 400, "Invalid Host or Origin headers.")
        host = host_values[0].decode("latin-1")
        if not host or not _loopback_name(host):
            return await _json_response(send, 400, "The request Host is not allowed.")
        if origin_values:
            try:
                parsed = urlsplit(origin_values[0].decode("latin-1"))
                origin_host = parsed.netloc
                valid_origin = parsed.scheme in {"http", "https"} and _loopback_name(origin_host)
                valid_origin = valid_origin and origin_host.lower() == host.lower()
            except (UnicodeDecodeError, ValueError):
                valid_origin = False
            if not valid_origin:
                return await _json_response(send, 403, "Cross-origin requests are not allowed.")
        return await self.app(scope, receive, send)


class _RequestBodyTooLarge(Exception):
    pass


class RequestBodyLimitMiddleware:
    """Enforce per-path HTTP body budgets before route/form parsing begins."""

    def __init__(self, app, *, path_limits: dict[str, int], default_limit: int = MIB):
        self.app = app
        self.path_limits = dict(path_limits)
        self.default_limit = default_limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit = self.path_limits.get(scope.get("path", ""), self.default_limit)
        headers = [value for key, value in scope.get("headers", []) if key.lower() == b"content-length"]
        if len(headers) > 1:
            return await _json_response(send, 400, "Invalid Content-Length header.")
        if headers:
            try:
                raw_length = headers[0].decode("ascii")
                if not raw_length.isdecimal():
                    raise ValueError
                declared = int(raw_length)
            except (UnicodeDecodeError, ValueError):
                return await _json_response(send, 400, "Invalid Content-Length header.")
            if declared > limit:
                return await _json_response(send, 413, "Request body exceeds the allowed size.")

        received = 0
        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _RequestBodyTooLarge
            return message

        response_started = False
        async def tracked_send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except _RequestBodyTooLarge:
            if not response_started:
                await _json_response(send, 413, "Request body exceeds the allowed size.")

