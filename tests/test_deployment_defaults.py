"""Deployment regression: cloud default, private pre-created logs and read-only code."""
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import yaml
import pytest
from bot.config import validate

ROOT=Path(__file__).resolve().parents[1]


def test_cloud_default_and_explicit_local():
    config=yaml.safe_load((ROOT/'config.yaml').read_text())
    assert config['telegram']['api_mode']=='cloud'
    assert config['telegram']['api_base_urls']['cloud']=='https://api.telegram.org'
    validate(config)
    config['telegram']['api_mode']='local'
    validate(config)


def checker():
    spec=importlib.util.spec_from_file_location('deployment_permissions',ROOT/'scripts/check_installation_permissions.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(os.geteuid()==0,reason='Preflight must run as the non-root service user')
def test_preflight_rejects_missing_logs(tmp_path,monkeypatch):
    module=checker()
    monkeypatch.chdir(tmp_path)
    Path('bot').mkdir();Path('data').mkdir(mode=0o700)
    for name in ('.env','config.yaml'):
        Path(name).touch(mode=0o600)
    Path('.venv/bin').mkdir(parents=True);Path('.venv/bin/python').symlink_to('/usr/bin/python3')
    monkeypatch.setattr(module,'check_source',lambda p:True)
    assert module.main()==1
    Path('logs').mkdir(mode=0o700)
    assert module.main()==0


@pytest.mark.parametrize('owner,mode,writable,expected',[
    (0,0o40755,False,True),(1000,0o40755,False,False),
    (0,0o40777,True,False),(0,0o40755,True,False),
])
def test_source_must_not_be_service_writable(monkeypatch,owner,mode,writable,expected):
    module=checker()
    class FakePath:
        def lstat(self):return SimpleNamespace(st_uid=owner,st_mode=mode)
        def is_symlink(self):return False
    monkeypatch.setattr(module.os,'geteuid',lambda:1000)
    monkeypatch.setattr(module.os,'access',lambda p,m:writable if m==os.W_OK else True)
    assert module.check_source(FakePath())==expected


def test_installation_prepares_logs_before_service_start():
    readme=(ROOT/'README.md').read_text()
    command='sudo install -d -o tgbot -g tgbot -m 0700'
    preparation=next(line for line in readme.splitlines() if command in line and '/opt/botdownload/logs' in line)
    assert readme.index(preparation)<readme.index('sudo systemctl start')
    assert 'sudo -u tgbot git clone' not in readme
    assert 'sudo -u tgbot python3 -m venv' not in readme
