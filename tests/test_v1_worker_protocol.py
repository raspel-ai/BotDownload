"""Protocol admission tests; no network access required."""
import json
import pytest

from bot.worker_ipc import MAX_MESSAGE, WorkerProtocolError, parse_request


def request(action="inspect", payload=None):
    return json.dumps({
        "version": 1,
        "request_id": "test-123",
        "task_id": 42,
        "action": action,
        "payload": payload if payload is not None else {"url": "https://youtu.be/dQw4w9WgXcQ"},
    }).encode()


def test_valid_inspect():
    assert parse_request(request())["action"] == "inspect"


@pytest.mark.parametrize("raw", [
    b"",
    b"{}",
    b"not json",
    b'{"version":1,"version":1}',
    b" " * (MAX_MESSAGE + 1),
    request("execute_offline"),
    request("inspect", {"url": "http://127.0.0.1/"}),
    request("inspect", {"url": "https://example.com/video"}),
    request("cancel", {"url": "https://youtu.be/dQw4w9WgXcQ"}),
])
def test_rejected_requests(raw):
    with pytest.raises(WorkerProtocolError):
        parse_request(raw)


def test_cancel_empty_payload():
    assert parse_request(request("cancel", {}))["action"] == "cancel"
