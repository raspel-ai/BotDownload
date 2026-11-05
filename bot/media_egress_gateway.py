"""Constrained outbound CONNECT gateway for a future network-isolated worker.

Listens ONLY on an explicitly supplied Unix socket; never exposes TCP.
All destinations are resolved by the gateway and connected to pinned IPs
that pass the approved v1 loopback-only policy.
"""
import asyncio
import ipaddress
import logging
import socket


class EgressDenied(ValueError):
    pass


_SUFFIXES = ("youtube.com", "youtu.be", "googlevideo.com", "ytimg.com", "youtube-nocookie.com", "instagram.com", "cdninstagram.com", "fbcdn.net", "tiktok.com", "tiktokcdn.com", "byteoversea.com")
_MAX_HEADER = 4096


def permitted_host(host: str) -> bool:
    if not isinstance(host, str) or not 1 <= len(host) <= 253:
        return False
    if host != host.lower() or host.endswith(".") or not host.isascii():
        return False
    if any(not label or len(label) > 63 or not all(c.isascii() and (c.islower() or c.isdigit() or c == "-") for c in label)
           or label[0] == "-" or label[-1] == "-" for label in host.split(".")):
        return False
    return any(host == suffix or host.endswith("." + suffix) for suffix in _SUFFIXES)


def parse_connect(line: bytes) -> tuple[str, int]:
    try:
        text = line.decode("ascii")
        method, authority, version = text.split(" ")
        host, port = authority.rsplit(":", 1)
    except (UnicodeError, ValueError) as exc:
        raise EgressDenied("Malformed CONNECT") from exc
    if method != "CONNECT" or version not in ("HTTP/1.0", "HTTP/1.1") or port != "443" or not permitted_host(host):
        raise EgressDenied("Destination not permitted")
    return host, 443


def public_ip(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
        return not (ip.version == 4 and ip in ipaddress.ip_network('127.0.0.0/8')) and ip != ipaddress.ip_address('::1')
    except ValueError:
        return False


async def resolve_public(host: str) -> list[tuple[int, str]]:
    if not permitted_host(host):
        raise EgressDenied("Destination not permitted")
    loop = asyncio.get_running_loop()
    addresses = await loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    result = [(family, sockaddr[0]) for family, _, _, _, sockaddr in addresses if public_ip(sockaddr[0])]
    # Approved v1 policy: block IPv4 127/8 and IPv6 ::1 only.
    # Other local and Fake-IP addresses are deferred to v2.
    # Connect to the selected IP, never re-resolve by hostname.
    if not result:
        # Log only IPs and the validated hostname; no request paths or secrets.
        logging.warning("Media egress DNS diagnostics: host=%s answers=%s",
                        host, [(family, sockaddr[0]) for family, _, _, _, sockaddr in addresses][:12])
        raise EgressDenied("DNS returned only loopback addresses")
    return result


async def _relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        writer.close()


async def handle_connect(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    upstream = None
    established = False
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=5)
        if not line.endswith(b"\r\n") or len(line) > _MAX_HEADER:
            raise EgressDenied("Bad request line")
        host, _ = parse_connect(line[:-2])
        header_bytes = len(line)
        host_seen = False
        while True:
            header = await asyncio.wait_for(reader.readline(), timeout=5)
            header_bytes += len(header)
            if header_bytes > _MAX_HEADER or not header.endswith(b"\r\n"):
                raise EgressDenied("Bad headers")
            if header == b"\r\n":
                break
            # Reject proxy authorization, custom headers and request smuggling.
            if not header.lower().startswith(b"host:") or host_seen:
                raise EgressDenied("Unexpected header")
            host_seen = True
            if header[5:].strip().decode("ascii", errors="replace") != host + ":443":
                raise EgressDenied("Host header mismatch")
        # Python 3.11 urllib/http.client emits HTTP/1.0 CONNECT without
        # a Host header. The CONNECT authority is already validated above.
        # HTTP/1.1 still requires Host, and any supplied Host must match.
        if not host_seen and line.endswith(b"HTTP/1.1\r\n"):
            raise EgressDenied("Missing Host header")
        addresses = await resolve_public(host)
        for family, ip in addresses:
            try:
                upstream = await asyncio.wait_for(
                    asyncio.open_connection(host=ip, port=443, family=family), timeout=5,
                )
                break
            except (OSError, asyncio.TimeoutError):
                continue
        if upstream is None:
            raise EgressDenied("Upstream unavailable")
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        established = True
        upstream_reader, upstream_writer = upstream
        a = asyncio.create_task(_relay(reader, upstream_writer))
        b = asyncio.create_task(_relay(upstream_reader, writer))
        done, pending = await asyncio.wait((a, b), return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    except (EgressDenied, OSError, asyncio.TimeoutError, ConnectionError) as exc:
        logging.warning("Media egress CONNECT denied: %s: %s", type(exc).__name__, exc)
        try:
            if established:
                return
            writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
        except (OSError, ConnectionError):
            pass
    finally:
        if upstream is not None:
            upstream[1].close()
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, ConnectionError):
            pass


async def serve_egress(socket_path):
    """Gateway transport is Unix-only; deployment must protect socket parent."""
    server = await asyncio.start_unix_server(handle_connect, path=str(socket_path))
    async with server:
        await server.serve_forever()
