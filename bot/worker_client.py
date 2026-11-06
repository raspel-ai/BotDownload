"""Bot-side IPC client for the protected worker."""
import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

MAX_MESSAGE = 65536


class IPCError(RuntimeError):
    pass


async def request(action: str, task_id: int, payload=None, *, socket_path=None, timeout=75):
    if action not in ("inspect", "download", "cancel", "status"):
        raise IPCError("Invalid action")
    if type(task_id) is not int or not 0 < task_id < 2**63:
        raise IPCError("Invalid task ID")
    path = socket_path or Path(os.environ.get("BOTDOWNLOAD_WORKER_SOCKET", "/run/botdownload/worker.sock"))
    message = {"version": 1, "request_id": str(uuid4()), "task_id": task_id, "action": action, "payload": payload if payload is not None else {}}
    raw = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
    if len(raw) > MAX_MESSAGE:
        raise IPCError("Request too large")
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(path)), 3)
        try:
            writer.write(raw)
            await asyncio.wait_for(writer.drain(), 3)
            reply = await asyncio.wait_for(reader.readline(), timeout)
        finally:
            writer.close()
            await writer.wait_closed()
    except (OSError, asyncio.TimeoutError, ConnectionError) as exc:
        raise IPCError("Protected worker unavailable") from exc
    if not reply.endswith(b"\n") or len(reply) > MAX_MESSAGE:
        raise IPCError("Invalid response size")
    try:
        result = json.loads(reply)
    except (ValueError, UnicodeError) as exc:
        raise IPCError("Invalid response JSON") from exc
    if (not isinstance(result, dict) or result.get("version") != 1
            or result.get("request_id") != message["request_id"]
            or result.get("task_id") != task_id
            or result.get("status") not in ("accepted", "running", "completed", "failed", "cancelled", "not_found")):
        raise IPCError("Invalid response fields")
    return result
