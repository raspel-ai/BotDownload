"""Run as root only AFTER collecting permanent-unit acceptance evidence."""
import json
import os
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bot.isolation import unit_digest

if os.geteuid()!=0:
    raise SystemExit('Требуются права оператора root для записи подтверждения; бот таких прав не получает.')
props=subprocess.check_output(['systemctl','show','tgbot-worker.service','-p','IPAddressDeny','-p','User','-p','NoNewPrivileges','-p','ProtectHome','-p','ProtectSystem','-p','PrivateTmp','-p','KillMode'],text=True)
print(props)
if not all(s in props for s in ('User=tgbot','127.0.0.0/8','::1','NoNewPrivileges=yes','ProtectSystem=full','ProtectHome=yes','PrivateTmp=yes','KillMode=control-group')):
    raise SystemExit('Отказ: effective-настройки не соответствуют политике.')
print('Это подтверждение НЕ выполняет сетевые тесты. Сначала зафиксируйте baseline TCP, отказ внутри постоянного worker и его дочернего процесса, рабочие DNS/HTTPS и Fake-IP (если используется).')
if input('Введите VERIFIED-PERMANENT-WORKER, только если эти проверки фактически пройдены [no]: ')!='VERIFIED-PERMANENT-WORKER':
    raise SystemExit('Отменено.')
root=Path('/etc/botdownload');root.mkdir(mode=0o755,exist_ok=True)
p=root/'worker-isolation.json'
if p.is_symlink():raise SystemExit('Отказ: symlink')
record={'unit':'tgbot-worker.service','boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'loopback_denied':True,'dns_https_ok':True,'children_confined':True,'unit_sha256':unit_digest()}
tmp=root/'worker-isolation.json.new'
with tmp.open('x') as f:json.dump(record,f);f.flush();os.fsync(f.fileno())
tmp.chmod(0o644);os.replace(tmp,p)
print('Подтверждение записано; оно действует до перезагрузки VPS или изменения unit/drop-in.')
