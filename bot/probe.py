"""Explicit operator-requested tests inside the unchanged permanent worker unit.

No media tools, SQLite, token or automatic approval. A missing listener is
INCONCLUSIVE, never evidence of filtering. The root helper rechecks both live
baseline fixtures before it can record the operator's confirmation.
"""
import argparse
import asyncio
import errno
import json
import os
from pathlib import Path
import socket
import stat
import sys
import time
import urllib.request
from .isolation import unit_digest

REQUEST = Path('/run/botdownload-probe.request')
REPORT = Path('/run/botdownload/isolation-probe.json')

def loopback(family, host):
    try:
        with socket.socket(family, socket.SOCK_STREAM) as s:
            s.settimeout(2)
            s.connect((host, 18765))
    except TimeoutError:
        return 'BLOCK_OBSERVED'
    except OSError as exc:
        if exc.errno in {errno.EACCES, errno.EPERM, errno.ETIMEDOUT}:
            return 'BLOCK_OBSERVED'
        return 'INCONCLUSIVE'  # ECONNREFUSED, EAFNOSUPPORT, ENETUNREACH, etc.
    return 'FAIL'

def requested(path=REQUEST):
    info=path.lstat()
    if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode & 0o022 or info.st_size>4096:
        raise ValueError('unsafe_probe_request')
    r=json.loads(path.read_text())
    if set(r)!={'request_id','created_at','boot_id','unit_sha256'}:
        raise ValueError('invalid_probe_request')
    if r['boot_id']!=Path('/proc/sys/kernel/random/boot_id').read_text().strip() or r['unit_sha256']!=unit_digest():
        raise ValueError('stale_probe_request')
    if type(r['created_at']) not in (int,float) or not 0<=time.time()-r['created_at']<=1800:
        raise ValueError('expired_probe_request')
    return r

async def child_check():
    p=await asyncio.create_subprocess_exec(sys.executable,'-m','bot.probe','--child-loopback',
        stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
    try:
        async with asyncio.timeout(10):
            out,_=await p.communicate()
        if p.returncode!=0:
            return {'ipv4':'INCONCLUSIVE','ipv6':'INCONCLUSIVE'}
        result=json.loads(out)
        if set(result)!={'ipv4','ipv6'} or any(v not in {'BLOCK_OBSERVED','FAIL','INCONCLUSIVE'} for v in result.values()):
            raise ValueError
        return result
    except (TimeoutError,ValueError):
        return {'ipv4':'INCONCLUSIVE','ipv6':'INCONCLUSIVE'}
    finally:
        if p.returncode is None:
            p.kill()
        await p.wait()

async def probe(r):
    cgroup=Path('/proc/self/cgroup').read_text().splitlines()
    paths=[line.split(':',2)[2] for line in cgroup if line.count(':')==2]
    group=next((p for p in paths if p.endswith('/tgbot-worker.service')),None)
    if group is None or os.geteuid()==0:
        raise ValueError('probe_outside_unprivileged_permanent_worker')
    checks={'parent_ipv4':loopback(socket.AF_INET,'127.0.0.1'),
            'parent_ipv6':loopback(socket.AF_INET6,'::1')}
    children=await child_check()
    checks.update(child_ipv4=children['ipv4'],child_ipv6=children['ipv6'])
    checks['dns']='INCONCLUSIVE'
    checks['https']='INCONCLUSIVE'
    try:
        socket.getaddrinfo('www.youtube.com',443,type=socket.SOCK_STREAM)
        checks['dns']='PASS'
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open('https://www.youtube.com/robots.txt',timeout=20) as response:
            if response.status==200:
                checks['https']='PASS'
    except (OSError,ValueError):
        pass
    complete=all(checks[k]=='BLOCK_OBSERVED' for k in ('parent_ipv4','parent_ipv6','child_ipv4','child_ipv6')) and checks['dns']==checks['https']=='PASS'
    status='OBSERVED' if complete else 'FAIL' if 'FAIL' in checks.values() else 'INCONCLUSIVE'
    return {**r,'generated_at':time.time(),'uid':os.geteuid(),'cgroup':group,
            'invocation_id':os.environ.get('INVOCATION_ID',''),'status':status,'checks':checks}

def write_report(report):
    # Runtime directory belongs to the worker; never overwrite a symlink.
    if REPORT.is_symlink():
        raise ValueError('unsafe_report')
    temporary=REPORT.with_name('isolation-probe.new')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'w') as f:
            json.dump(report,f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary,REPORT)
    finally:
        temporary.unlink(missing_ok=True)

def main():
    parser=argparse.ArgumentParser(description='Проверка защиты в постоянном worker; не подтверждает допуск автоматически')
    parser.add_argument('--if-requested',action='store_true')
    parser.add_argument('--child-loopback',action='store_true')
    args=parser.parse_args()
    if args.child_loopback:
        print(json.dumps({'ipv4':loopback(socket.AF_INET,'127.0.0.1'),'ipv6':loopback(socket.AF_INET6,'::1')}))
        return
    if args.if_requested and not REQUEST.exists() and not REQUEST.is_symlink():
        return  # No network tests and no approval when no operator request exists.
    try:
        report=asyncio.run(probe(requested()))
        write_report(report)
        print('Worker isolation observations: '+report['status']+'; operator baseline verification still required.')
        if report['status']!='OBSERVED':
            raise SystemExit(2)
    except (ValueError,OSError) as exc:
        raise SystemExit('INCONCLUSIVE: '+str(exc)) from None

if __name__=='__main__':
    main()
