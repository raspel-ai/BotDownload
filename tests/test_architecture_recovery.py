import ast
import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import pytest
from bot.state import Store,StateUnavailable
from bot.broker import Broker
from bot.urls import validate_url
from test_state_broker import TG,Client,job


def test_worker_import_graph_has_no_sqlite():
    root=Path(__file__).resolve().parents[1]/'bot'
    seen=set()
    def visit(name):
        if name in seen:return
        seen.add(name)
        tree=ast.parse((root/f'{name}.py').read_text())
        for n in ast.walk(tree):
            if isinstance(n,ast.Import):
                assert all(a.name not in {'sqlite3','bot.state','bot.main'} for a in n.names)
            if isinstance(n,ast.ImportFrom):
                assert n.module not in {'sqlite3','state','main','broker','telegram'}
                if n.level==1 and (root/f'{n.module}.py').exists():visit(n.module)
    visit('worker')
    assert 'media' in seen and 'processes' in seen


def test_no_old_execution_paths():
    root=Path(__file__).resolve().parents[1]
    for p in (root/'bot').glob('*.py'):
        s=p.read_text()
        assert 'systemd-run' not in s
        if 'yt_dlp' in s:assert p.name=='tool.py'
    for name in ('worker_supervisor.py','offline_executor.py','media_namespace_entry.py','metadata.py'):
        assert not (root/'bot'/name).exists()
    worker=(root/'deploy/tgbot-worker.service').read_text()
    bot=(root/'deploy/tgbot.service').read_text()
    assert 'IPAddressDeny=127.0.0.0/8' in worker and 'IPAddressDeny=::1/128' in worker
    assert 'IPAddressDeny' not in bot and 'PrivateNetwork' not in worker
    for s in (worker,bot):
        assert all(x in s for x in ('User=tgbot','NoNewPrivileges=true','ProtectSystem=full','ProtectHome=true','PrivateTmp=true','KillMode=control-group'))


def test_not_found_checks_ready_before_redownload(store,config):
    from bot.files import ready_path
    i=job(store);store.select(i,123,10,2,'video',20);eid=store.execution(i,'download')
    p=ready_path(eid,False);p.write_bytes(b'confirmed')
    m={'filename':p.name,'size':p.stat().st_size,'duration':1,'format':'mp4','validated':True,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
    p.with_suffix('.json').write_text(json.dumps(m))
    store.update(i,phase='running',download_attempts=1)
    b=Broker(store,Client(),config,TG())
    asyncio.run(b.reconcile(store.get(i),{'status':'not_found'}))
    assert store.get(i)['phase']=='upload_pending'
    assert store.get(i)['download_attempts']==1


def test_queue_fifo_skip_disk_without_reset(store,config):
    first=job(store);store.select(first,123,10,2,'video',20)
    second=store.create(124,10,3,4,validate_url('https://youtu.be/abcdefghijl'))
    store.update(second,phase='card');store.select(second,124,10,4,'video',20)
    old=store.get(first)['queued']
    class C(Client):
        async def call(self,eid,action,payload=None):
            self.calls.append((eid,action,payload));return {'status':'accepted'}
    c=C();b=Broker(store,c,config,TG());b.disk_allows=lambda j:j['id']==second
    asyncio.run(b.tick())
    assert store.get(first)['phase']=='queued' and store.get(first)['queued']==old
    assert store.get(second)['phase']=='running'
    assert len(c.calls)==1


def test_unrecoverable_corrupt_db_never_implicitly_replaced(store,config):
    store.path.write_bytes(b'broken')
    with pytest.raises(StateUnavailable):store.initialize(config['backup'])
    assert list(Path('data').glob('database_corrupt_*.sqlite'))
    with pytest.raises(StateUnavailable):Store().initialize(config['backup'])
    assert not store.path.exists()


def test_safe_legacy_data_migration(store,config):
    # Only data migration remains; no legacy worker code is imported.
    with store.db() as db:
        db.execute("DELETE FROM migrations WHERE name='legacy'")
        db.executescript('''CREATE TABLE tasks(id INTEGER,user_id INTEGER,chat_id INTEGER,message_id INTEGER,source_url TEXT,variant TEXT,status TEXT,created_at TEXT,download_attempts INTEGER,upload_attempts INTEGER,telegram_file_id TEXT);
        CREATE TABLE daily_usage(task_id INTEGER,user_id INTEGER,utc_day TEXT);
        CREATE TABLE link_attempts(user_id INTEGER,attempted_at INTEGER);
        CREATE TABLE bans(user_id INTEGER,banned_until INTEGER);''')
        db.execute("INSERT INTO tasks VALUES(1,123,10,2,'https://youtu.be/abcdefghijk','720p','downloading','2026-11-07T00:00:00Z',1,0,NULL)")
        db.execute("INSERT INTO daily_usage VALUES(1,123,'2026-11-07')")
        db.execute('INSERT INTO bans VALUES(123,9999999999)')
    store.initialize(config['backup'])
    assert store.get(1)['phase']=='queued' and store.get(1)['download_attempts']==1
    assert store.rows('SELECT * FROM quota')[0]['job_id']==1
    assert store.rows('SELECT * FROM bans_v1')[0]['until']==9999999999
    assert store.rows('SELECT * FROM tasks')
    assert list(Path('backups').glob('*.sqlite'))

def test_duplicate_invalid_and_valid_updates_are_durable(store,config):
    b=Broker(store,Client(),config,TG())
    assert b.accept(123,10,10,'https://evil.com/a')[0]=='invalid'
    assert b.accept(123,10,10,'https://evil.com/a')[0]=='duplicate'
    result,jid,_=b.accept(123,10,11,'https://youtu.be/abcdefghijk')
    assert result=='accepted' and store.get(jid)['phase']=='inspect'
    assert b.accept(123,10,11,'https://youtu.be/abcdefghijk')[0]=='duplicate'
    assert store.rows('SELECT count(*) n FROM attempts')[0]['n']==2


def test_queue_timeout_and_twenty_pending(store,config):
    b=Broker(store,Client(),config,TG())
    ids=[]
    for n in range(20):
        i=store.create(123,10,100+n,200+n,validate_url('https://youtu.be/abcdefghijk'))
        store.update(i,phase='card');assert store.select(i,123,10,200+n,'video',20);ids.append(i)
    i=store.create(123,10,1000,2000,validate_url('https://youtu.be/abcdefghijk'))
    store.update(i,phase='card')
    with pytest.raises(OverflowError):store.select(i,123,10,2000,'video',20)
    store.update(ids[0],queued=time.time()-1900)
    b.disk_allows=lambda j:False
    asyncio.run(b.tick())
    assert store.get(ids[0])['phase']=='failed'
    assert store.get(ids[0])['error']=='queue_timeout'


def test_corrupt_ready_never_sent_and_can_redownload(store,config):
    from bot.files import ready_path
    i=job(store);store.select(i,123,10,2,'video',20);eid=store.execution(i,'download')
    p=ready_path(eid,False);p.write_bytes(b'not validated')
    store.update(i,phase='upload_pending',ready_file=str(p),download_attempts=1)
    tg=TG();b=Broker(store,Client(),config,tg)
    asyncio.run(b.upload(store.get(i)))
    assert not tg.sent and store.get(i)['phase']=='queued'
    assert not p.exists()
