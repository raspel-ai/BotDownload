"""Launch yt-dlp in a fresh unprivileged user+network namespace.

No host-network fallback. The gateway is reachable only via Unix socket;
the namespace contains loopback and no external network interface.
"""
import asyncio
import os
import logging
from pathlib import Path
import shutil
import signal
import sys

from .media_executor import UnsafeMediaRequest, validate_media_url


def namespace_command(
    url: str, gateway: Path, *, timeout_seconds: int = 120,
    max_bytes: int = 256 * 1024 * 1024,
) -> list[str]:
    validate_media_url(url)
    if (not isinstance(gateway, Path) or not gateway.is_absolute()
            or not gateway.is_socket()
            or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3600
            or type(max_bytes) is not int or not 1 <= max_bytes <= 2**31):
        raise UnsafeMediaRequest("Недопустимая конфигурация изоляции")
    unshare = shutil.which("unshare")
    ip = shutil.which("ip")
    if not unshare or not ip:
        raise UnsafeMediaRequest("Network namespace недоступен")
    return [
        unshare, "--user", "--map-root-user", "--net", "--fork",
        "--", ip, "link", "set", "lo", "up",
    ]


async def execute_isolated(
    url: str, *, gateway: Path, workspace: Path,
    timeout_seconds: int = 120, max_bytes: int = 256 * 1024 * 1024,
    cancel: asyncio.Event | None = None, variant: str = "480p",
) -> Path:
    """Run through a bounded local CONNECT gateway without user namespaces."""
    # An 'ip link' command cannot exec the bridge: use a dedicated launcher
    # with an explicit namespace-local setup and no shell.
    from .media_namespace_entry import download_args
    from .media_executor import validate_media_url
    validate_media_url(url)
    if not gateway.is_absolute() or not gateway.is_socket():
        raise UnsafeMediaRequest("Шлюз недоступен")
    command = [sys.executable, "-m", "bot.media_namespace_bridge",
               str(gateway), str(timeout_seconds),
               *download_args(url, str(max_bytes), variant)]
    if not workspace.is_dir() or workspace.is_symlink():
        raise UnsafeMediaRequest("Некорректная рабочая директория")
    if cancel is not None and cancel.is_set():
        raise asyncio.CancelledError()
    env = dict(os.environ)
    project = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = project
    diagnostic = workspace / ".namespace-stderr.log"
    with diagnostic.open("wb") as log_file:
        os.chmod(diagnostic, 0o600)
        proc = await asyncio.create_subprocess_exec(
            *command, env=env, cwd=str(workspace), stdin=asyncio.subprocess.DEVNULL,
            stdout=log_file, stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
    waiter = asyncio.create_task(proc.wait())
    canceller = asyncio.create_task(cancel.wait()) if cancel else None
    try:
        done, _ = await asyncio.wait(
            [waiter] + ([canceller] if canceller else []),
            timeout=timeout_seconds + 10, return_when=asyncio.FIRST_COMPLETED,
        )
        if canceller is not None and canceller in done:
            raise asyncio.CancelledError()
        if not done:
            raise TimeoutError("Время загрузки истекло")
        if waiter.result() == 124:
            raise TimeoutError("Время загрузки истекло (namespace yt-dlp)")
        if waiter.result() != 0:
            details = diagnostic.read_text(encoding="utf-8", errors="replace")[-3000:]
            import re
            details = re.sub(r"https?://\\S+", "[URL]", details)
            logging.error("Namespace/yt-dlp diagnostic (task-local): %s", details)
            raise RuntimeError(f"Изолированная загрузка завершилась с кодом {waiter.result()}")
        diagnostic.unlink(missing_ok=True)
        files = list(workspace.iterdir())
        if len(files) != 1 or not files[0].is_file() or files[0].is_symlink():
            raise RuntimeError("Небезопасный результат загрузки")
        expected_suffix = ".mp3" if variant == "MP3" else ".mp4"
        if files[0].suffix.lower() != expected_suffix:
            raise RuntimeError("Формат готового файла не соответствует выбору")
        if not 0 < files[0].stat().st_size <= max_bytes:
            raise RuntimeError("Размер файла вне допустимого диапазона")
        return files[0]
    finally:
        # Kill the dedicated process group even when the launcher has already
        # exited: namespace grandchildren may otherwise outlive their parent.
        # The group was created exclusively for this invocation.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await proc.wait()
        waiter.cancel()
        if canceller is not None:
            canceller.cancel()
