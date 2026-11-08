"""Admission/storage/IPC/loader boundary checks; external services are not contacted."""
from types import SimpleNamespace
import pytest
from bot.broker import Broker
from bot.protocol import encode,request
from bot.urls import validate_url
from bot import tool
from test_state_broker import Client,TG
from test_youtube_urls import REAL_CASES,BAD_URLS


@pytest.mark.parametrize('raw,rid',REAL_CASES)
def test_canonical_url_saved_and_revalidated_for_both_worker_actions(store,config,raw,rid):
    b=Broker(store,Client(),config,TG())
    canonical=f'https://www.youtube.com/watch?v={rid}'
    result,jid,error=b.accept(123,10,1,raw)
    assert result=='accepted' and not error
    job=store.get(jid)
    assert (job['url'],job['platform'],job['resource_id'])==(canonical,'youtube',rid)
    inspect=b.payload(job,'inspect')
    store.update(jid,phase='card',message_id=2)
    assert store.select(jid,123,10,2,'video',20)
    download=b.payload(store.get(jid),'download')
    for action,payload in (('inspect',inspect),('download',download)):
        frame={'version':1,'request_id':'canonical_test','task_id':jid,'action':action,'payload':payload}
        assert request(encode(frame))['payload']['url']==canonical
        # Valid share links still cannot bypass the canonical-only IPC contract.
        with pytest.raises(ValueError):request(encode(frame|{'payload':payload|{'url':raw}}))
    assert b.accept(123,10,1,raw)[:2]==('duplicate',None)
    assert len(store.rows('SELECT * FROM jobs'))==1


@pytest.mark.parametrize('raw',BAD_URLS[:12])
def test_invalid_admission_creates_no_job_and_cannot_reach_worker(store,config,raw):
    c=Client();b=Broker(store,c,config,TG())
    result,jid,error=b.accept(123,10,1,raw)
    assert result=='invalid' and jid is None and error and not c.calls
    assert not store.rows('SELECT * FROM jobs')
    for action in ('inspect','download'):
        payload={'url':raw,'platform':'youtube','resource_id':'abcdefghijk'}
        if action=='download':payload['format']='video'
        with pytest.raises(ValueError):
            request(encode({'version':1,'request_id':'invalid_test','task_id':1,'action':action,'payload':payload}))


@pytest.mark.parametrize('raw,rid',REAL_CASES)
@pytest.mark.parametrize('action',['inspect','download'])
def test_yt_dlp_receives_only_canonical_single_video_url(store,config,monkeypatch,capsys,raw,rid,action):
    seen=[];guards=[]
    class Extractor:
        def __init__(self,options):
            assert options['noplaylist'] is True
            assert options['allowed_extractors']==['youtube','tiktok','instagram']
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def extract_info(self,url,download):
            seen.append((url,download));return {'id':rid,'title':'fixture'}
    monkeypatch.setattr(tool,'load_settings',lambda **kw:SimpleNamespace(data=config))
    monkeypatch.setattr(tool,'require_isolation',lambda c:guards.append(c))
    monkeypatch.setattr(tool.yt_dlp,'YoutubeDL',Extractor)
    monkeypatch.setattr(tool.sys,'argv',['tool',action,'1','video',raw])
    tool.main()
    assert guards==[config]
    assert seen==[(f'https://www.youtube.com/watch?v={rid}',action=='download')]
    capsys.readouterr()


def test_preexisting_noncanonical_job_is_canonicalized_on_dispatch(store,config):
    # Existing v1 records need no destructive database migration.
    u=validate_url('https://youtu.be/abcdefghijk')
    jid=store.create(123,10,1,2,u)
    with store.db() as db:
        db.execute('UPDATE jobs SET url=? WHERE id=?',('https://youtu.be/abcdefghijk',jid))
    b=Broker(store,Client(),config,TG())
    payload=b.payload(store.get(jid),'inspect')
    assert payload['url']=='https://www.youtube.com/watch?v=abcdefghijk'
    assert request(encode({'version':1,'request_id':'recovery','task_id':jid,'action':'inspect','payload':payload}))['payload']==payload
