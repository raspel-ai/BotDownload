import errno
import importlib.util
from pathlib import Path
import socket
import sys
import pytest
from bot import probe
from bot.isolation import unit_digest

spec=importlib.util.spec_from_file_location('confirm_helper',Path(__file__).resolve().parents[1]/'scripts/confirm_worker_isolation.py')
helper=importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)

@pytest.mark.parametrize('error,expected',[(ConnectionRefusedError(errno.ECONNREFUSED,'refused'),'INCONCLUSIVE'),(OSError(errno.EAFNOSUPPORT,'no IPv6'),'INCONCLUSIVE'),(OSError(errno.ENETUNREACH,'no route'),'INCONCLUSIVE'),(TimeoutError(),'BLOCK_OBSERVED'),(PermissionError(errno.EACCES,'denied'),'BLOCK_OBSERVED')])
def test_missing_listener_is_not_filter_pass(monkeypatch,error,expected):
    class S:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def settimeout(self,*args):pass
        def connect(self,*args):raise error
    monkeypatch.setattr(probe.socket,'socket',lambda *args:S())
    assert probe.loopback(socket.AF_INET,'127.0.0.1')==expected


def properties():
    return {'User':'tgbot','Group':'tgbot','NoNewPrivileges':'yes','ProtectHome':'yes','ProtectSystem':'full','PrivateTmp':'yes','PrivateNetwork':'no','KillMode':'control-group','IPAddressAllow':'','IPAddressDeny':'127.0.0.0/8 ::1/128','ActiveState':'active','InvocationID':'abc123','ControlGroup':'/system.slice/tgbot-worker.service'}

@pytest.mark.parametrize('change',[{'IPAddressAllow':'0.0.0.0/0'},{'IPAddressDeny':'127.0.0.0/8 ::1/128 10.0.0.0/8'},{'User':'root'},{'PrivateNetwork':'yes'},{'IPAddressDeny':'127.0.0.0/8 ::10/128'}])
def test_effective_policy_is_exact(change):
    p=properties();helper.validate_properties(p)
    with pytest.raises(ValueError):helper.validate_properties(p|change)


def test_report_bound_to_request_boot_unit_and_live_invocation():
    q={'request_id':'nonce','created_at':100,'boot_id':'boot','unit_sha256':'hash'}
    r={**q,'generated_at':110,'uid':1001,'cgroup':'/system.slice/tgbot-worker.service','invocation_id':'abc123','status':'OBSERVED','checks':{'parent_ipv4':'BLOCK_OBSERVED','parent_ipv6':'BLOCK_OBSERVED','child_ipv4':'BLOCK_OBSERVED','child_ipv6':'BLOCK_OBSERVED','dns':'PASS','https':'PASS'}}
    helper.validate_report(r,q,properties(),1001,'boot','hash',120)
    for changed in (r|{'invocation_id':'previous'},r|{'boot_id':'old'},r|{'unit_sha256':'different'},r|{'checks':r['checks']|{'child_ipv6':'INCONCLUSIVE'}},r|{'generated_at':-1000}):
        with pytest.raises(ValueError):helper.validate_report(changed,q,properties(),1001,'boot','hash',120)


def test_inherited_and_runtime_control_overrides_invalidate_digest(tmp_path):
    d=tmp_path/'service.d';d.mkdir();f=d/'policy.conf'
    original=unit_digest([tmp_path]);f.write_text('[Service]\nNoNewPrivileges=true\n')
    assert unit_digest([tmp_path])!=original
    previous=unit_digest([tmp_path]);f.write_text('[Service]\nNoNewPrivileges=false\n')
    assert unit_digest([tmp_path])!=previous


def test_no_operator_request_does_not_run_network(tmp_path,monkeypatch):
    monkeypatch.setattr(probe,'REQUEST',tmp_path/'missing')
    monkeypatch.setattr(sys,'argv',['probe','--if-requested'])
    monkeypatch.setattr(probe,'loopback',lambda *a:pytest.fail('unexpected network'))
    probe.main()


def test_root_helper_never_imports_project_bot():
    root=Path(__file__).resolve().parents[1]
    source=(root/'scripts/confirm_worker_isolation.py').read_text()
    assert 'from bot.' not in source and "sys.path.insert" not in source
    unit=(root/'deploy/tgbot-worker.service').read_text()
    assert 'bot.probe --if-requested' in unit


def test_baseline_uses_same_uid_outside_worker(monkeypatch):
    from types import SimpleNamespace
    calls=[]
    monkeypatch.setattr(helper.pwd,'getpwuid',lambda uid:SimpleNamespace(pw_gid=2001))
    def run(args,**kwargs):
        calls.append((args,kwargs))
        return SimpleNamespace(returncode=0,stdout='BASELINE_OK\n')
    monkeypatch.setattr(helper.subprocess,'run',run)
    helper.baseline('nonce',2000)
    args,kwargs=calls[0]
    assert args[:3]==['/usr/bin/python3','-I','-c']
    assert kwargs['user']==2000 and kwargs['group']==2001 and kwargs['extra_groups']==[]
    assert kwargs['cwd']=='/'
    monkeypatch.setattr(helper.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=1,stdout=''))
    with pytest.raises(ValueError):helper.baseline('nonce',2000)


@pytest.mark.skipif(__import__('os').geteuid()==0,reason='Permission checker intentionally refuses root')
def test_installation_check_does_not_read_or_change_secret_contents(tmp_path,monkeypatch,capsys):
    root=Path(__file__).resolve().parents[1]
    spec=importlib.util.spec_from_file_location('permissions_helper',root/'scripts/check_installation_permissions.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.chdir(tmp_path);tmp_path.chmod(0o700)
    Path('bot').mkdir(mode=0o700);Path('data').mkdir(mode=0o700);Path('logs').mkdir(mode=0o700)
    Path('config.yaml').write_text('private config content');Path('config.yaml').chmod(0o600)
    Path('.env').write_text('TELEGRAM_BOT_TOKEN=DO_NOT_PRINT_ME');Path('.env').chmod(0o600)
    Path('.venv/bin').mkdir(parents=True);Path('.venv/bin/python').symlink_to(sys.executable)
    # This test concerns secret handling, not privileged ownership setup.
    monkeypatch.setattr(module,'check_source',lambda path:True)
    assert module.main()==0
    assert 'DO_NOT_PRINT_ME' not in capsys.readouterr().out
    assert Path('.env').read_text()=='TELEGRAM_BOT_TOKEN=DO_NOT_PRINT_ME'
    Path('.env').chmod(0o644)
    assert module.main()==1
    assert Path('.env').stat().st_mode & 0o777==0o644


def test_record_readable_despite_root_style_umask(tmp_path):
    import os
    mask=os.umask(0o077)
    try:helper.write_new(tmp_path/'request',{'nonsecret':'nonce'})
    finally:os.umask(mask)
    assert (tmp_path/'request').stat().st_mode & 0o777==0o644
    with pytest.raises(FileExistsError):helper.write_new(tmp_path/'request',{'overwrite':True})
    assert helper.load_json(tmp_path/'request',uid=os.geteuid())=={'nonsecret':'nonce'}


def test_report_symlink_never_followed(tmp_path):
    import os
    secret=tmp_path/'secret';secret.write_text('{"secret":"not a report"}')
    p=tmp_path/'report';p.symlink_to(secret)
    with pytest.raises(OSError):helper.load_json(p,uid=os.geteuid())
