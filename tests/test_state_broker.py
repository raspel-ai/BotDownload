import asyncio
import json
from pathlib import Path
import pytest
from bot.state import Store,day
from bot.urls import validate_url
from bot.broker import Broker
from bot.client import WorkerUnavailable

class TG:
    def __init__(self):self.sent=[]
    async def status(self,c,m,t,k=None):return m
    async def notify(self,*a):pass
    async def send(self,job,source,cache=False):self.sent.append((job['id'],source,cache));return 'FILE'
class Client:
    def __init__(self):self.calls=[];self.unavailable=False
    async def call(self,eid,action,payload=None):
        self.calls.append((eid,action,payload))
        if self.unavailable:raise WorkerUnavailable()
        return {'status':'not_found'}

def job(store):
    i=store.create(123,10,1,2,validate_url('https://youtu.be/abcdefghijk'))
    store.update(i,phase='card',metadata=json.dumps({'variants':[]}))
    return i

def test_idempotency_ownership_and_capacity(store):
    i=job(store)
    assert i==store.create(123,10,1,2,validate_url('https://youtu.be/abcdefghijk'))
    assert not store.select(i,999,10,2,'video',20)
    assert not store.select(i,123,10,3,'video',20)
    assert not store.select(i,123,10,2,'720p',20)
    assert store.select(i,123,10,2,'video',20)
    assert not store.select(i,123,10,2,'audio',20)

def test_rate_persists(store,config):
    c=config['rate_limit']
    assert all(store.rate_allow(1,c,now=1000) for _ in range(5))
    assert not Store().rate_allow(1,c,now=1000)
    assert not Store().rate_allow(1,c,now=1100)
    assert Store().rate_allow(1,c,now=5000)

def test_quota_atomic_once_and_cache(store):
    i=job(store);store.select(i,123,10,2,'video',20)
    store.update(i,phase='uploading')
    assert store.delivered(i,'FILE')
    assert not store.delivered(i,'FILE')
    assert store.used(123)==1
    j=store.create(123,10,4,5,validate_url('https://youtu.be/abcdefghijk'));store.update(j,phase='card')
    assert store.select(j,123,10,5,'video',1)
    assert store.get(j)['phase']=='upload_pending'
    assert store.get(j)['quota_exempt']
    assert store.delivered(j,'FILE')
    assert store.used(123)==1

def test_backup_restore_and_corruption(store,config):
    i=job(store);p=store.backup(3)
    store.update(i,phase='failed')
    store.restore(p)
    assert store.get(i)['phase']=='card'
    for _ in range(4):store.backup(3)
    assert len(list(Path('backups').glob('backup_*.sqlite')))==3
    assert Store.verify(p) is False # rotated older copy

def test_cancel_disconnect_does_not_claim_success(store,config):
    i=job(store);store.update(i,phase='running',execution_id=1)
    client=Client();client.unavailable=True
    b=Broker(store,client,config,TG())
    done,pending=asyncio.run(b.cancel_all(123))
    assert (done,pending)==(0,1)
    assert store.get(i)['phase']=='running' and store.get(i)['cancel_requested']
    client.unavailable=False
    assert asyncio.run(b.cancel(store.get(i)))
    assert store.get(i)['phase']=='cancelled'

def test_unknown_disk_threshold(store,config):
    i=job(store);b=Broker(store,Client(),config,TG())
    threshold=config['storage']['unknown_size_fallback_mib']*1024**2
    assert not b.disk_allows(store.get(i),threshold)
    assert b.disk_allows(store.get(i),threshold+1)

def test_fallback_exactly_two_and_no_repeat_inspect(store,config):
    i=job(store);tg=TG();seen=[]
    async def status(c,m,t,k=None):seen.append(k);return m
    tg.status=status
    b=Broker(store,Client(),config,tg)
    asyncio.run(b.card(store.get(i),{},True))
    assert seen[-1]==[('Скачать видео','video'),('Скачать аудио','audio')]
    assert store.select(i,123,10,2,'video',20)
    assert b.payload(store.get(i),'download')['format']=='video'
    assert b.client.calls==[]
