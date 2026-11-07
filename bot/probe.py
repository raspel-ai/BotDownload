"""Operator-only diagnostic run as ExecStartPre in the permanent worker cgroup.

Start a known-good HTTP server on 127.0.0.1:18765 OUTSIDE the worker first.
This probe never creates a security attestation and never relaxes filtering.
"""
import asyncio
import json
import socket
import sys
import urllib.request
from pathlib import Path

async def main():
    cgroup=Path('/proc/self/cgroup').read_text()
    if not any(s.endswith('/tgbot-worker.service') for s in cgroup.splitlines()):
        raise SystemExit('INCONCLUSIVE: probe is outside tgbot-worker.service')
    for family,address in [(socket.AF_INET,('127.0.0.1',18765)),(socket.AF_INET6,('::1',18765))]:
        with socket.socket(family) as s:
            s.settimeout(2)
            try:s.connect(address)
            except OSError:pass
            else:raise SystemExit('FAIL: loopback reachable')
    child=await asyncio.create_subprocess_exec(sys.executable,'-c',
        "import socket; s=socket.socket(); s.settimeout(2); s.connect(('127.0.0.1',18765))",
        stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
    if await child.wait()==0:
        raise SystemExit('FAIL: child can reach loopback')
    # urllib uses system DNS and HTTPS, with normal certificate verification.
    socket.getaddrinfo('www.youtube.com',443,type=socket.SOCK_STREAM)
    with urllib.request.urlopen('https://www.youtube.com/robots.txt',timeout=20) as r:
        if r.status!=200:raise SystemExit('FAIL: HTTPS')
    print('PASS: worker and child loopback refused; DNS/HTTPS work. Requires external baseline proof.')

if __name__=='__main__':asyncio.run(main())
