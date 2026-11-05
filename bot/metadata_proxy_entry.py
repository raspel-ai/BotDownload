"""Portable metadata subprocess using the loopback-only CONNECT bridge."""
import asyncio
from pathlib import Path
import sys

from .metadata_namespace_entry import run


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(2)
    gateway = Path(sys.argv[1])
    if not gateway.is_absolute() or not gateway.is_socket():
        raise SystemExit(2)
    raise SystemExit(asyncio.run(run(gateway, sys.argv[2])))


if __name__ == "__main__":
    main()
