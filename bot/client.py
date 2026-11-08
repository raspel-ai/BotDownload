"""Bot-side IPC, with no worker autostart."""
import asyncio
import os
import socket
import stat
import struct
import uuid
from .protocol import MAX_MESSAGE, encode, request, response
class WorkerUnavailable(ConnectionError):
    pass

def peer_uid(writer):
    s = writer.get_extra_info('socket')
    return struct.unpack('3i', s.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1]

class WorkerClient:
    def __init__(self, path):
        self.path = path
    async def call(self, task_id, action, payload=None):
        v = {'version': 1, 'request_id': uuid.uuid4().hex, 'task_id': task_id, 'action': action, 'payload': payload or {}}
        raw = encode(v)
        request(raw)
        writer = None
        try:
            info = self.path.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise OSError('unsafe_socket')
            async with asyncio.timeout(5):
                reader, writer = await asyncio.open_unix_connection(self.path, limit=MAX_MESSAGE)
                if peer_uid(writer) != os.geteuid():
                    raise OSError('wrong_peer')
                writer.write(raw)
                await writer.drain()
                return response(await reader.readline(), v)
        except (OSError, TimeoutError, ValueError) as exc:
            raise WorkerUnavailable('Защищённый worker недоступен; выполнение приостановлено.') from exc
        finally:
            if writer:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass
