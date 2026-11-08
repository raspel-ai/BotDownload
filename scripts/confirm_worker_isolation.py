"""Standalone root operator helper. Install reviewed copies under root ownership.

Run /usr/bin/python3 -I /usr/local/libexec/botdownload/confirm_worker_isolation.py.
Never import or execute the tgbot-owned working tree as root.
"""
import argparse
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import pwd
import secrets
import stat
import subprocess
import time

REQUEST=Path('/run/botdownload-probe.request')
REPORT=Path('/run/botdownload/isolation-probe.json')
TARGET=Path('/etc/botdownload/worker-isolation.json')


def trusted_file(path,uid=0,max_size=65536):
    info=path.lstat()
    if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_uid!=uid or info.st_mode & 0o022 or info.st_size>max_size:
        raise ValueError('Небезопасный файл: '+str(path))
    return path


def root_directory(path):
    for p in [*reversed(path.parents),path]:
        info=p.lstat()
        if p.is_symlink() or not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode & 0o022:
            raise ValueError('Требуется защищённый каталог root: '+str(p))


def digest_function():
    parent=Path(__file__).absolute().parent
    root_directory(parent)
    trusted_file(Path(__file__).absolute())
    path=trusted_file(parent/'isolation.py')
    spec=importlib.util.spec_from_file_location('_root_owned_isolation',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.unit_digest


def properties():
    names=['User','Group','IPAddressDeny','IPAddressAllow','NoNewPrivileges','ProtectHome',
           'ProtectSystem','PrivateTmp','PrivateNetwork','KillMode','ActiveState','InvocationID','ControlGroup']
    out=subprocess.check_output(['/usr/bin/systemctl','show','tgbot-worker.service',*[f'--property={p}' for p in names]],text=True)
    return dict(line.split('=',1) for line in out.splitlines() if '=' in line)


def validate_properties(p):
    expected={'User':'tgbot','Group':'tgbot','NoNewPrivileges':'yes','ProtectHome':'yes',
              'ProtectSystem':'full','PrivateTmp':'yes','PrivateNetwork':'no','KillMode':'control-group','IPAddressAllow':''}
    if any(p.get(k)!=v for k,v in expected.items()):
        raise ValueError('Effective-настройки unit не соответствуют политике; запреты не снимать.')
    deny={str(ipaddress.ip_network(item,strict=False)) for item in p.get('IPAddressDeny','').split()}
    if deny!={'127.0.0.0/8','::1/128'}:
        raise ValueError('IPAddressDeny должен содержать ровно два утверждённых диапазона.')


def load_json(path,uid=0):
    # Report is worker-owned: validate the opened fd, not a pathname before open.
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as f:
        info=os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=uid or info.st_mode & 0o022 or info.st_size>65536:
            raise ValueError('Небезопасный JSON-файл отчёта/запроса')
        raw=f.read(65537)
        if len(raw)>65536:
            raise ValueError('Отчёт слишком большой')
    return json.loads(raw)



def validate_report(r,q,p,uid,boot,digest,now):
    if set(q)!={'request_id','created_at','boot_id','unit_sha256'}:
        raise ValueError('Некорректный запрос диагностики')
    expected={'parent_ipv4':'BLOCK_OBSERVED','parent_ipv6':'BLOCK_OBSERVED',
              'child_ipv4':'BLOCK_OBSERVED','child_ipv6':'BLOCK_OBSERVED','dns':'PASS','https':'PASS'}
    if (r.get('checks')!=expected or r.get('status')!='OBSERVED' or r.get('uid')!=uid
            or not p.get('InvocationID') or r.get('invocation_id')!=p['InvocationID']
            or r.get('cgroup')!=p.get('ControlGroup') or not r['cgroup'].endswith('/tgbot-worker.service')
            or p.get('ActiveState')!='active'):
        raise ValueError('Нет полного отчёта текущего постоянного worker; подтверждение запрещено.')
    for key,value in q.items():
        if r.get(key)!=value:
            raise ValueError('Отчёт не соответствует запросу диагностики')
    if q['boot_id']!=boot or q['unit_sha256']!=digest or not 0<=now-q['created_at']<=1800 or not 0<=now-r.get('generated_at',0)<=900:
        raise ValueError('Отчёт устарел или unit изменился: повторите диагностику без изменения защиты.')


def baseline(request_id,uid):
    # Same UID as worker, outside its cgroup, with no project imports/proxy.
    account=pwd.getpwuid(uid)
    code="""import sys,urllib.request
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
for host in ('127.0.0.1','[::1]'):
 with opener.open('http://'+host+':18765/botdownload-probe.txt',timeout=3) as response:
  if response.status!=200 or response.read(128).decode().strip()!=sys.argv[1]:
   raise SystemExit(2)
print('BASELINE_OK')
"""
    result=subprocess.run(['/usr/bin/python3','-I','-c',code,request_id],user=uid,group=account.pw_gid,
        extra_groups=[],cwd='/',env={'PATH':'/usr/bin:/bin'},capture_output=True,text=True,timeout=10)
    if result.returncode!=0 or result.stdout.strip()!='BASELINE_OK':
        raise ValueError('Baseline под UID tgbot вне worker не подтверждён; оба сервера должны отвечать с текущим идентификатором.')


def write_new(path,record):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644)
    with os.fdopen(fd,'w') as f:
        os.fchmod(f.fileno(),0o644)  # nonsecret record must remain readable by tgbot, even with root umask 077
        json.dump(record,f)
        f.flush()
        os.fsync(f.fileno())


def main():
    parser=argparse.ArgumentParser(description='Подготовка и явное подтверждение испытаний постоянного worker')
    parser.add_argument('--prepare',action='store_true',help='Создать новый запрос диагностики; не включает загрузки')
    args=parser.parse_args()
    if os.geteuid()!=0:
        raise ValueError('Эту установленную root-owned утилиту запускает только администратор.')
    unit_digest=digest_function()
    p=properties()
    validate_properties(p)
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    digest=unit_digest()
    if args.prepare:
        if TARGET.exists() or TARGET.is_symlink():
            raise ValueError('Есть прежнее подтверждение. Сначала согласуйте его сохранение и отзыв; автоматической замены нет.')
        write_new(REQUEST,{'request_id':secrets.token_hex(16),'created_at':time.time(),'boot_id':boot,'unit_sha256':digest})
        print('Запрос создан. Это НЕ PASS и не допуск к inspect/download. Далее нужны оба baseline-сервера и рестарт worker.')
        return
    q=load_json(REQUEST)
    uid=pwd.getpwnam('tgbot').pw_uid
    root_directory(REPORT.parent.parent)
    info=REPORT.parent.lstat()
    if REPORT.parent.is_symlink() or not stat.S_ISDIR(info.st_mode) or info.st_uid!=uid or info.st_mode & 0o077:
        raise ValueError('Небезопасный runtime-каталог отчёта')
    r=load_json(REPORT,uid)
    validate_report(r,q,p,uid,boot,digest,time.time())
    baseline(q['request_id'],uid)
    print('Свежий отчёт текущего worker прочитан; оба baseline отвечают HTTP 200 с текущим идентификатором.')
    print('Это наблюдения, не замена проверки оператором. Убедитесь, что серверы работали непрерывно во время пробы.')
    if input('Введите VERIFIED-PERMANENT-WORKER только после проверки результатов [no]: ')!='VERIFIED-PERMANENT-WORKER':
        print('Отменено; допуск не создан.')
        return
    # Recheck after the prompt; never authorize a changed/restarted unit.
    p2=properties()
    validate_properties(p2)
    validate_report(r,q,p2,uid,boot,unit_digest(),time.time())
    baseline(q['request_id'],uid)
    TARGET.parent.mkdir(mode=0o755,exist_ok=True)
    root_directory(TARGET.parent)
    record={'unit':'tgbot-worker.service','boot_id':boot,'loopback_denied':True,
            'dns_https_ok':True,'children_confined':True,'unit_sha256':digest}
    # O_EXCL preserves existing records; no silent overwrite of prior acceptance.
    write_new(TARGET,record)
    print('Подтверждение записано. Архивируйте созданный запрос из /run/botdownload-probe.request после сохранения доказательств; unit не изменять.')

if __name__=='__main__':
    try:
        main()
    except (OSError,ValueError,KeyError,TypeError,subprocess.CalledProcessError) as exc:
        raise SystemExit('Подтверждение не выполнено: '+str(exc)) from None
