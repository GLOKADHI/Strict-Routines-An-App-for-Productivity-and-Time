import asyncio

import pytest

from local_api_security import (LoopbackRequestGuardMiddleware, RequestBodyLimitMiddleware,
                                validate_bind_host)
from run_local import create_parser, server_config


def _scope(path="/analyze", *, client=("127.0.0.1", 50000), headers=()):
    return {"type": "http", "asgi": {"version": "3.0"}, "method": "POST", "path": path,
            "raw_path": path.encode(), "query_string": b"", "headers": list(headers),
            "client": client, "server": ("127.0.0.1", 8000), "scheme": "http", "http_version": "1.1"}


async def _run(app, scope, messages):
    emitted = []
    iterator = iter(messages)
    async def receive():
        try:
            return next(iterator)
        except StopIteration:
            return {"type": "http.disconnect"}
    async def send(message):
        emitted.append(message)
    await app(scope, receive, send)
    return emitted


async def _drain_app(scope, receive, send):
    while True:
        message = await receive()
        if message["type"] == "http.disconnect" or not message.get("more_body", False):
            break
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok", "more_body": False})


def _status(messages):
    return next(message["status"] for message in messages if message["type"] == "http.response.start")


def test_loopback_bind_configuration_and_unsafe_host_rejection():
    assert server_config("legacy") == {"app": "app.main:app", "host": "127.0.0.1", "port": 8000}
    assert server_config("evidence", port=8081)["host"] == "127.0.0.1"
    assert validate_bind_host("::1") == "::1"
    with pytest.raises(ValueError):
        validate_bind_host("0.0.0.0")
    with pytest.raises(ValueError):
        server_config("legacy", host="192.168.1.5")
    with pytest.raises(SystemExit):
        create_parser().parse_args(["legacy", "--host", "0.0.0.0"])


def test_declared_oversized_body_is_rejected_before_receive():
    receive_calls = 0
    async def unused_receive():
        nonlocal receive_calls
        receive_calls += 1
        raise AssertionError("Body should not be read after oversized Content-Length")
    sent = []
    async def send(message):
        sent.append(message)
    middleware = RequestBodyLimitMiddleware(_drain_app, path_limits={"/analyze": 3})
    scope = _scope(headers=[(b"content-length", b"4")])
    asyncio.run(middleware(scope, unused_receive, send))
    assert _status(sent) == 413
    assert receive_calls == 0


@pytest.mark.parametrize("headers", [(), ((b"content-length", b"1"),)])
def test_streamed_body_limit_counts_absent_or_false_content_length(headers):
    messages = [{"type": "http.request", "body": b"1234", "more_body": False}]
    middleware = RequestBodyLimitMiddleware(_drain_app, path_limits={"/analyze": 3})
    assert _status(asyncio.run(_run(middleware, _scope(headers=headers), messages))) == 413


def test_exact_body_boundary_and_malformed_length():
    middleware = RequestBodyLimitMiddleware(_drain_app, path_limits={"/analyze": 3})
    messages = [{"type": "http.request", "body": b"123", "more_body": False}]
    assert _status(asyncio.run(_run(middleware, _scope(headers=[(b"content-length", b"3")]), messages))) == 200
    assert _status(asyncio.run(_run(middleware, _scope(headers=[(b"content-length", b"nope")]), messages))) == 400


@pytest.mark.parametrize("client,headers,expected", [
    (("192.0.2.4", 50000), [(b"host", b"127.0.0.1")], 403),
    (("127.0.0.1", 50000), [(b"host", b"example.com")], 400),
    (("127.0.0.1", 50000), [(b"host", b"127.0.0.1"), (b"origin", b"https://attacker.example")], 403),
    (("127.0.0.1", 50000), [(b"host", b"127.0.0.1"), (b"origin", b"http://127.0.0.1")], 200),
])
def test_loopback_peer_host_and_origin_guard(client, headers, expected):
    guard = LoopbackRequestGuardMiddleware(_drain_app)
    messages = [{"type": "http.request", "body": b"", "more_body": False}]
    result = asyncio.run(_run(guard, _scope(client=client, headers=headers), messages))
    assert _status(result) == expected

