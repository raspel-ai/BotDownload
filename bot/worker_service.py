"""Offline IPC worker for manual V1 execution under the bot account."""
import asyncio
import os
import pwd
import stat
from pathlib import Path
import sys

from .worker_ipc import serve


def main() -> None:
    if os.name != "posix" or os.geteuid() == 0:
        raise SystemExit("Worker должен работать без root")
    bot_user = os.environ.get("BOTDOWNLOAD_CLIENT_USER", "tgbot")
    if not bot_user or not bot_user.isascii():
        raise SystemExit("Неверный пользователь клиента")
    try:
        allowed_uid = pwd.getpwnam(bot_user).pw_uid
    except KeyError:
        raise SystemExit("Пользователь клиента не найден") from None
    socket_path = Path(os.environ.get(
        "BOTDOWNLOAD_WORKER_SOCKET", "/run/botdownload/worker.sock",
    ))
    # Manual V1: create a private runtime directory without systemd.
    # Never follow an existing symlink or accept a shared writable directory.
    parent = socket_path.parent
    if not parent.exists() and not parent.is_symlink():
        try:
            parent.mkdir(mode=0o700, parents=True)
        except OSError as exc:
            raise SystemExit(f"Не удалось создать каталог сокета: {exc}") from None
    info = parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise SystemExit("Каталог сокета должен принадлежать текущему пользователю и иметь права 0700")
    try:
        asyncio.run(serve(socket_path, allowed_uid=allowed_uid))
    except (OSError, ValueError) as exc:
        print(f"Worker не запущен: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
