"""Bounded, authenticated Unix JSON Lines worker protocol; no database access."""
import asyncio
import json
import os
from pathlib import Path
import re
import socket
import stat
import struct

from .url_validation import validate_url, URLValidationError

MAX_MESSAGE = 65536
VALID_ACTIONS = frozenset(("inspect", "download", "cancel", "status"))
REQUEST_ID = re.compile("[A-Za-z0-9_-]{1,64}$")


class WorkerProtocolError(ValueError):
    pass


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise WorkerProtocolError("Duplicate JSON key")
        result[key] = value
    return result


def parse_request(raw: bytes) -> dict:
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_MESSAGE:
        raise WorkerProtocolError("Invalid message length")
    try:
        message = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except (ValueError, UnicodeError) as exc:
        raise WorkerProtocolError("Invalid JSON") from exc
    if (type(message) is not dict
            or set(message) != {"version", "request_id", "task_id", "action", "payload"}
            or type(message["version"]) is not int or message["version"] != 1
            or type(message["request_id"]) is not str or not REQUEST_ID.fullmatch(message["request_id"])
            or type(message["task_id"]) is not int or not 0 < message["task_id"] < 2**63
            or message["action"] not in VALID_ACTIONS or type(message["payload"]) is not dict):
        raise WorkerProtocolError("Invalid request fields")
    action, payload = message["action"], message["payload"]
    if action == "inspect":
        if set(payload) != {"url"} or type(payload["url"]) is not str:
            raise WorkerProtocolError("Invalid inspect payload")
        try:
            validate_url(payload["url"])
        except URLValidationError as exc:
            raise WorkerProtocolError("URL not permitted") from exc
    elif action == "download":
        if set(payload) != {"url", "variant"} or type(payload["url"]) is not str or payload["variant"] not in ("video", "audio", "480p", "720p", "1080p", "MP3"):
            raise WorkerProtocolError("Invalid download payload")
        try:
            validate_url(payload["url"])
        except URLValidationError as exc:
            raise WorkerProtocolError("URL not permitted") from exc
    elif payload:
        raise WorkerProtocolError("Unexpected payload")
    return message


def peer_uid(writer: asyncio.StreamWriter) -> int:
    sock = writer.get_extra_info("socket")
    if sock is None or not hasattr(socket, "SO_PEERCRED"):
        raise WorkerProtocolError("Peer credentials unavailable")
    raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    return struct.unpack("3i", raw)[1]


class WorkerState:
    def __init__(self):
        self.jobs = {}
        self.inspect_slots = asyncio.Semaphore(2)
        self.download_slot = asyncio.Lock()

    async def inspect(self, task_id, url):
        async with self.inspect_slots:
            from .metadata_process import run_json_subprocess
            import sys
            return await run_json_subprocess(
                [sys.executable, "-m", "bot.metadata_worker", url],
                cwd=Path(__file__).resolve().parent.parent,
                timeout_seconds=60, max_output_bytes=60000,
            )

    async def dispatch(self, message):
        action, task_id, payload = message["action"], message["task_id"], message["payload"]
        if action == "status":
            return {"status": self.jobs.get(task_id, {}).get("status", "not_found")}
        if action == "cancel":
            job = self.jobs.get(task_id)
            if job is None:
                return {"status": "not_found"}
            if job["status"] in ("completed", "failed", "cancelled"):
                return {"status": job["status"]}
            return {"status": "failed", "error": "Cancellation not yet safely implemented"}
        if action == "download":
            return {"status": "failed", "error": "Protected download pipeline not yet implemented"}
        if task_id in self.jobs:
            return {"status": self.jobs[task_id]["status"], "result": self.jobs[task_id].get("result")}
        self.jobs[task_id] = {"status": "running"}
        try:
            result = await self.inspect(task_id, payload["url"])
            self.jobs[task_id] = {"status": "completed", "result": result}
            return {"status": "completed", "result": result}
        except Exception:
            self.jobs[task_id] = {"status": "failed"}
            return {"status": "failed", "error": "Inspection failed"}


async def handle_connection(reader, writer, *, allowed_uid, state):
    message = None
    try:
        if peer_uid(writer) != allowed_uid:
            raise WorkerProtocolError("Unauthorized peer")
        raw = await asyncio.wait_for(reader.readline(), timeout=5)
        if not raw.endswith(b"\n") or len(raw) > MAX_MESSAGE + 1:
            raise WorkerProtocolError("Invalid framing")
        message = parse_request(raw[:-1])
        result = await state.dispatch(message)
        response = {"version": 1, "request_id": message["request_id"],
                    "task_id": message["task_id"], **result}
    except (WorkerProtocolError, asyncio.TimeoutError, asyncio.LimitOverrunError, OSError):
        response = {"version": 1, "request_id": message["request_id"] if message else "",
                    "task_id": message["task_id"] if message else 0,
                    "status": "failed", "error": "Invalid or unauthorized request"}
    try:
        encoded = json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
        if len(encoded) > MAX_MESSAGE:
            encoded = json.dumps({"version": 1, "request_id": response["request_id"],
                                  "task_id": response["task_id"], "status": "failed",
                                  "error": "Response too large"}).encode() + b"\n"
        writer.write(encoded)
        await asyncio.wait_for(writer.drain(), 5)
    except (OSError, ConnectionError, asyncio.TimeoutError):
        pass
    finally:
        writer.close()
        await writer.wait_closed()


async def serve(socket_path: Path, *, allowed_uid: int):
    if not socket_path.is_absolute() or type(allowed_uid) is not int or allowed_uid < 1:
        raise WorkerProtocolError("Invalid worker configuration")
    parent = socket_path.parent
    info = parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise WorkerProtocolError("Unsafe runtime directory")
    if socket_path.exists() or socket_path.is_symlink():
        raise WorkerProtocolError("Socket already exists")
    state = WorkerState()
    server = await asyncio.start_unix_server(
        lambda reader, writer: handle_connection(reader, writer, allowed_uid=allowed_uid, state=state),
        path=str(socket_path), limit=MAX_MESSAGE + 2)
    try:
        os.chmod(socket_path, 0o600)
        async with server:
            await server.serve_forever()
    finally:
        server.close()
        await server.wait_closed()
        if socket_path.is_socket():
            socket_path.unlink()
